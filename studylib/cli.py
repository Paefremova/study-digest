"""Разбор команд и вывод. Каждая команда возвращает (данные, текст).

`--json` печатает данные, без него — текст. Ошибка StudyError печатается в stderr
с подсказкой, если она известна, и даёт код возврата 2.
"""
import argparse
import json
import os
import pathlib
import sys
import time

from . import answer as answer_mod
from . import digest as digest_mod
from . import files as files_mod
from . import hosting, rutube, local
from .config import Config, Course, ROOT, StudyError
from .fmt import moment, table
from .moodle import Moodle


def course_id(cfg, value):
    """id курса из числа, кода каталога (карта CODE) или старой строки COURSE."""
    if not value:
        return None
    if str(value).isdigit():
        return int(value)
    for c in cfg.courses():
        if c.code == value:
            return c.id
    cid = cfg.id_for_code(value)
    if cid:
        return cid
    raise StudyError("config", f"нет курса с кодом {value} (config.env: CODE/COURSE)")


def course_of(cfg, value):
    """Курс по коду каталога или id: старые COURSE, иначе карта CODE."""
    for c in cfg.courses():
        if c.code == value or str(c.id) == str(value):
            return c
    cid = int(value) if str(value).isdigit() else cfg.id_for_code(value)
    if cid:
        return Course(cid, cfg.code_for(cid) or "-", "")
    raise StudyError("config", f"нет курса «{value}» (config.env: CODE/COURSE)")


def kv(pairs):
    """k=v из командной строки; повтор ключа собирается в список для массивов Moodle."""
    out = {}
    for item in pairs:
        key, _, value = item.partition("=")
        if key in out:
            out[key] = (out[key] if isinstance(out[key], list) else [out[key]]) + [value]
        else:
            out[key] = value
    return out


# --- ТУИС

def cmd_me(cfg, args):
    d = Moodle(cfg).me()
    return d, "{} | userid {} | {} {} | функций: {}".format(
        d["fullname"], d["userid"], d["sitename"], d["release"], len(d["functions"]))


def _seen(la):
    return time.strftime("%Y-%m-%d", time.localtime(la)) if la else "никогда"


def _course_rows(cfg, m, include_hidden=False):
    now = time.time()
    win = cfg.active_days() * 86400
    ignore = cfg.ignore()
    rows = []
    for c in m.courses(include_hidden=include_hidden):
        la = c.get("lastaccess") or 0
        rows.append({"id": c["id"], "shortname": c.get("shortname"), "title": c["fullname"],
                     "lastaccess": la, "code": cfg.code_for(c["id"], c.get("shortname")),
                     "ignored": c["id"] in ignore, "stale": not la or (now - la) > win})
    return rows


def _course_table(rows):
    return table([[str(r["id"]), _seen(r["lastaccess"]),
                   "игнор" if r["ignored"] else ("старый?" if r["stale"] else ""),
                   r["code"] or "-", r["title"]] for r in rows],
                 ["id", "заходил", "", "папка", "курс"])


def _write_courses(cfg, ignore_ids, code_map):
    """Переписать в config.env строки COURSE_IGNORE и CODE (старые COURSE убрать)."""
    keep = [ln for ln in cfg.path.read_text().splitlines()
            if not ln.strip().split("=")[0].strip().startswith("COURSE_IGNORE")
            and not ln.strip().startswith(("CODE ", "COURSE "))]
    keep.append("COURSE_IGNORE=" + " ".join(str(i) for i in sorted(ignore_ids)))
    keep += [f"CODE {cid} {code}" for cid, code in sorted(code_map.items())]
    cfg.path.write_text("\n".join(keep) + "\n")
    os.chmod(cfg.path, 0o600)


def cmd_courses(cfg, args):
    m = Moodle(cfg)
    rows = _course_rows(cfg, m, include_hidden=args.all)
    if not getattr(args, "setup", False):
        stale = " ".join(str(r["id"]) for r in rows if r["stale"] and not r["ignored"])
        lines = [_course_table(rows), "",
                 "Строка для config.env — курсы, которые НЕ отслеживать (кандидаты «старый?»):",
                 f"COURSE_IGNORE={stale}", "",
                 "Папку локального репозитория курса задать: CODE <id> <имя-папки>"]
        return rows, "\n".join(lines)
    # интерактивная настройка (вызывается из setup.sh): чекбоксы, переключение по номеру
    num = {i + 1: r for i, r in enumerate(sorted(rows, key=lambda r: (r["stale"], r["title"])))}
    ignore_ids = {r["id"] for r in rows if r["ignored"]}
    stale_ids = {r["id"] for r in rows if r["stale"]}

    def draw(prev):
        out = ["Отметь курсы, которые НЕ отслеживать ([x] = в игнор):"]
        header = False
        for i, r in num.items():
            if r["stale"] and not header:
                out.append("   -- давно не заходил --"); header = True
            box = "[x]" if r["id"] in ignore_ids else "[ ]"
            out.append(f"  {i:2} {box} {_seen(r['lastaccess']):>10}  {r['title'][:58]}")
        out.append("   номер - переключить | s - все давно не заходил | Enter/g - готово")
        if prev and sys.stdout.isatty():
            sys.stdout.write(f"\033[{prev + 1}A\033[J")   # стереть прошлый блок и строку ввода
        sys.stdout.write("\n".join(out) + "\n")
        sys.stdout.flush()
        return len(out)

    print("")
    drawn = 0
    while True:
        drawn = draw(drawn)
        ans = input("> ").strip().lower()
        if ans in ("", "g", "готово"):
            break
        if ans == "s":
            ignore_ids = ignore_ids - stale_ids if stale_ids <= ignore_ids else ignore_ids | stale_ids
            continue
        for tok in ans.replace(",", " ").split():
            if tok.isdigit() and int(tok) in num:
                ignore_ids ^= {num[int(tok)]["id"]}

    # папки: авто-коды уже есть; добавить/изменить парами «номер имя»
    code_map = {r["id"]: r["code"] for r in rows if r["id"] not in ignore_ids and r["code"]}
    print(f"\nЛокальные папки курсов (git/релизы/лабы). Определены: {', '.join(sorted(code_map.values())) or 'нет'}.")
    print("Добавить/изменить: <номер> <имя-папки> (напр. 3 num-methods); Enter - готово.")
    while True:
        ans = input("> ").strip()
        if not ans:
            break
        p = ans.split(None, 1)
        if len(p) == 2 and p[0].isdigit() and int(p[0]) in num:
            cid = num[int(p[0])]["id"]
            if cid in ignore_ids:
                print("  этот курс в игноре - пропущен")
            else:
                code_map[cid] = p[1].strip()
        else:
            print("  формат: номер и имя, напр. 3 num-methods")

    _write_courses(cfg, ignore_ids, code_map)
    for code in code_map.values():
        (ROOT / code / "stash").mkdir(parents=True, exist_ok=True)
        (ROOT / code / "tuis").mkdir(parents=True, exist_ok=True)
    return ({"ignore": sorted(ignore_ids), "code": code_map},
            "config.env обновлён: COURSE_IGNORE ({}), CODE ({})".format(len(ignore_ids), len(code_map)))


def cmd_functions(cfg, args):
    names = Moodle(cfg).functions()
    if args.filter:
        names = [n for n in names if args.filter in n]
        return names, "\n".join(names)
    return names, "всего функций: %d" % len(names)


def cmd_call(cfg, args):
    out = Moodle(cfg).call(args.function, **kv(args.params))
    return out, json.dumps(out, ensure_ascii=False, indent=1)


def cmd_assigns(cfg, args):
    only = course_id(cfg, args.course)
    courses, warnings = Moodle(cfg).assignments([only] if only else None)
    rows, lines = [], []
    for c in courses:
        if not c["assignments"]:
            continue
        lines.append("\n[{}] {}".format(c["id"], c["fullname"]))
        for a in c["assignments"]:
            due = moment(a.get("duedate"))
            rows.append({"course": {"id": c["id"], "title": c["fullname"]},
                         "assign_id": a["id"], "cmid": a["cmid"], "name": a["name"],
                         "due": due})
            lines.append("  id={} cmid={} до {}  {}".format(
                a["id"], a["cmid"], due["text"] if due else "—", a["name"]))
    if warnings:
        lines.append("\nСкрыто ограничением доступа: %d (сроки видны в `study digest`)"
                     % len(warnings))
    return {"assignments": rows, "warnings": warnings}, "\n".join(lines).lstrip()


def cmd_calendar(cfg, args):
    import time
    now = int(time.time())
    events = Moodle(cfg).calendar(now, now + args.days * 86400)
    rows = [{"at": moment(e["timesort"], now), "name": e.get("name"),
             "course": (e.get("course") or {}).get("shortname"),
             "course_id": (e.get("course") or {}).get("id")} for e in events]
    return rows, table([[r["at"]["text"], (r["name"] or "")[:60], r["course"] or ""]
                        for r in rows]) or "нет событий"


def cmd_grades(cfg, args):
    m = Moodle(cfg)
    only = course_id(cfg, args.course)
    ids = [only] if only else [c.id for c in cfg.courses()]
    titles = {c.id: c.title for c in cfg.courses()}
    rows, lines = [], []
    for cid in ids:
        try:
            grades = m.grades(cid)
        except StudyError as e:
            lines.append("\n{}: {}".format(titles.get(cid, cid), e.message))
            continue
        for t in grades:
            items = [{"name": i.get("itemname"), "raw": i.get("graderaw"),
                      "max": i.get("grademax"), "type": i.get("itemtype")}
                     for i in t.get("gradeitems", []) if i.get("graderaw") is not None]
            rows.append({"course": {"id": cid, "title": titles.get(cid, t.get("courseshortname"))},
                         "items": items})
            lines.append("\n" + (titles.get(cid) or t.get("courseshortname") or str(cid)))
            body = [["  " + (i["name"] or "")[:50], "%s из %s" % (i["raw"], i["max"])]
                    for i in items if i["type"] != "course"]
            total = next((i for i in items if i["type"] == "course"), None)
            if total:
                body.append(["  ИТОГО", "%s из %s" % (total["raw"], total["max"])])
            lines.append(table(body) if body else "  оценок пока нет")
    return rows, "\n".join(lines).lstrip()


def cmd_files(cfg, args):
    m = Moodle(cfg)
    d = files_mod.listing(cfg, m, course_of(cfg, args.course), everything=args.all)
    if args.pull:
        d = files_mod.pull(m, d, force=args.force)
    return d, files_mod.render(d, pulled=args.pull)


def cmd_status(cfg, args):
    d = Moodle(cfg).submission_status(args.assign_id)
    s = (d.get("lastattempt") or {}).get("submission") or {}
    out = {"status": s.get("status"), "attempt": s.get("attemptnumber"),
           "modified": moment(s.get("timemodified"))}
    return out, "статус: {} | попытка: {} | изменён: {}".format(
        out["status"] or "нет ответа", out["attempt"],
        out["modified"]["full"] if out["modified"] else "—")


def cmd_upload(cfg, args):
    paths = [pathlib.Path(f) for f in args.files]
    itemid = Moodle(cfg).upload(paths, args.itemid)
    out = {"itemid": itemid, "files": [p.name for p in paths]}
    return out, "itemid: {} · загружено: {}".format(itemid, ", ".join(out["files"]))


def cmd_submit(cfg, args):
    m = Moodle(cfg)
    text = pathlib.Path(args.text).read_text() if args.text else None
    courses, _ = m.assignments()
    found = next((a for c in courses for a in c["assignments"] if a["id"] == args.assign_id), None)
    plan = {"assign_id": args.assign_id, "name": found["name"] if found else None,
            "due": moment(found.get("duedate")) if found else None,
            "text_file": args.text, "text_chars": len(text or ""),
            "files_itemid": args.files, "confirmed": bool(args.confirm)}
    if not args.confirm:
        lines = ["Что будет отправлено:",
                 "  задание: {} (id {})".format(plan["name"] or "?", args.assign_id),
                 "  срок: {}".format(plan["due"]["full"] if plan["due"] else "—"),
                 "  текст: {}".format("{} ({} символов), формат Markdown".format(args.text, len(text))
                                       if text is not None else "нет"),
                 "  вложения: {}".format(args.files or "нет"),
                 "",
                 "Отправка необратима: у заданий курса submissiondrafts=0, "
                 "черновика не будет.",
                 "Повтори с --confirm."]
        print("\n".join(lines))
        sys.exit(1)
    out = m.save_submission(args.assign_id, text, args.files)
    plan["result"] = out
    return plan, "Отправлено: {} (id {})".format(plan["name"] or "?", args.assign_id)


# --- хостинги

def client(cfg, which, path=None):
    cls = hosting.GitVerse if which == "gv" else hosting.SourceCraft
    return cls(cfg, path=path or local.find_repo())


def cmd_host_releases(cfg, args):
    rows = client(cfg, args.host).releases()
    return rows, table([[r["tag"] or "—", r.get("name") or "", str(r["assets"]),
                         r.get("status") or ""] for r in rows],
                       ["тег", "название", "файлов", "статус"]) or "релизов нет"


def cmd_host_release(cfg, args):
    c = client(cfg, args.host)
    notes = pathlib.Path(args.notes).read_text()
    out = c.release(args.tag, args.title, notes, sha=args.sha) if args.host == "gv" \
        else c.release(args.tag, args.title, notes)
    return out, "Релиз {} создан: {}".format(out["tag"], out["url"])


def cmd_host_update(cfg, args):
    c = client(cfg, args.host)
    notes = pathlib.Path(args.notes).read_text() if args.notes else None
    out = c.update(args.tag, title=args.title, notes=notes)
    return out, "Релиз {} обновлён: {}".format(out["tag"], out["url"])


def cmd_host_asset(cfg, args):
    c = client(cfg, args.host)
    out = c.asset(args.release, args.file, args.name)
    return out, "Загружено: " + out["name"]


def cmd_host_api(cfg, args):
    out = client(cfg, args.host).api(args.path)
    return out, json.dumps(out, ensure_ascii=False, indent=1)


def cmd_rt_login(cfg, args):
    out = rutube.Rutube(cfg).login(args.email)
    return out, "Rutube: сохранён token (режим token): " + out["token_file"]


def cmd_rt_jwt(cfg, args):
    out = rutube.Rutube(cfg).save_refresh(args.refresh)
    return out, "Rutube: сохранён refresh_token (режим jwt): " + out["refresh_file"]


def cmd_rt_me(cfg, args):
    rows = rutube.Rutube(cfg, mode=args.mode).me()
    return rows, table([[str(v["id"]), v["title"] or "", "скрыто" if v["hidden"] else "",
                         v["url"] or ""] for v in rows],
                       ["id", "название", "", "ссылка"]) or "вход работает, видео пока нет"


def cmd_rt_api(cfg, args):
    out = rutube.Rutube(cfg, mode=args.mode).api(args.path)
    return out, json.dumps(out, ensure_ascii=False, indent=1)


def cmd_rt_categories(cfg, args):
    rows = rutube.Rutube(cfg).categories()
    return rows, table([[str(c["id"]), c["short"] or "", c["name"] or ""] for c in rows],
                       ["id", "код", "название"])


def cmd_rt_video(cfg, args):
    v = rutube.Rutube(cfg, mode=args.mode).video(args.video_id)
    keys = [("id", "id"), ("title", "название"), ("is_hidden", "скрыто"),
            ("video_url", "ссылка"), ("duration", "длительность")]
    cat = (v.get("category") or {}).get("name")
    lines = [f"{label}: {v.get(k)}" for k, label in keys] + [f"категория: {cat}"]
    return v, "\n".join(lines)


def cmd_rt_edit(cfg, args):
    fields = {"title": args.title, "category": args.category, "age": args.age,
              "description": pathlib.Path(args.desc).read_text() if args.desc else None}
    if args.hidden:
        fields["is_hidden"] = True
    if args.visible:
        fields["is_hidden"] = False
    v = rutube.Rutube(cfg, mode=args.mode).edit(args.video_id, **fields)
    return v, "готово: " + (v.get("title") or args.video_id)


def cmd_rt_pl_list(cfg, args):
    rows = rutube.Rutube(cfg, mode=args.mode).playlists()
    return rows, table([[str(p["id"]), p["title"] or "", "скрыто" if p["hidden"] else "",
                         p["url"] or ""] for p in rows],
                       ["id", "название", "", "ссылка"]) or "плейлистов нет"


def cmd_rt_pl_create(cfg, args):
    p = rutube.Rutube(cfg, mode=args.mode).playlist_create(args.title, args.hidden)
    pid = p.get("id")
    url = f"https://rutube.ru/plst/{pid}/" if pid else ""
    return p, f"плейлист создан: {pid} {url}".rstrip() + (f"\nRUTUBE_PLAYLIST={url}" if url else "")


def cmd_rt_pl_add(cfg, args):
    out = rutube.Rutube(cfg, mode=args.mode).playlist_add(args.playlist_id, args.video_id)
    return out, f"видео {args.video_id} → плейлист {args.playlist_id}"


def cmd_rt_upload(cfg, args):
    if bool(args.file) == bool(args.url):
        print("укажи либо файл, либо --url (одно из двух)")
        sys.exit(1)
    title = args.title or (pathlib.Path(args.file).stem if args.file else None)
    category = args.category or 13
    age = 0 if args.age is None else args.age
    if not args.confirm:
        size = f" ({pathlib.Path(args.file).stat().st_size} байт)" if args.file else ""
        print("\n".join(["Что будет загружено на Rutube:",
                         f"  источник: {('URL ' + args.url) if args.url else args.file + size}",
                         f"  название: {title or '?'}",
                         f"  категория: {category}   возраст: {age}+",
                         f"  видимость: {'скрыто' if args.hidden else 'публично'}",
                         f"  плейлист: {args.playlist or 'нет'}",
                         "", "Загрузка публикует видео в твой аккаунт. Повтори с --confirm."]))
        sys.exit(1)
    rt = rutube.Rutube(cfg, mode=args.mode)
    desc = pathlib.Path(args.desc).read_text() if args.desc else None
    up = rt.upload_url if args.url else rt.upload_file
    v = up(args.url or args.file, title=title, description=desc, category=category, hidden=args.hidden, age=age)
    if args.playlist:
        rt.playlist_add(args.playlist, v["id"])
    text = "загружено: " + v["url"] + (" (скрыто)" if v["hidden"] else "")
    if args.slot:
        text += f"\nRUTUBE_{args.slot.upper()}={v['url']}"
    return v, text


# --- сводки

def cmd_digest(cfg, args):
    d = digest_mod.collect(cfg, Moodle(cfg), days=args.days, save=not args.no_save,
                           since=args.since)
    return d, digest_mod.render_digest(d)


def cmd_state(cfg, args):
    d = digest_mod.state(cfg, Moodle(cfg), days=args.days, with_tuis=not args.local,
                         save=not args.no_save, pull=args.pull,
                         since=args.since)
    return d, digest_mod.render(d)


def cmd_answer(cfg, args):
    d = answer_mod.build(args.code, args.num, args.tag)
    return d, answer_mod.render(d)


# --- разбор аргументов

def build_parser():
    p = argparse.ArgumentParser(
        prog="study", description="ТУИС, репозитории курсов и хостинги одной командой.")
    p.add_argument("--json", action="store_true", help="машиночитаемый вывод")
    # --json принимается и до, и после имени команды. SUPPRESS нужен, чтобы значение
    # из подкоманды не затирало уже разобранное значение основного разбора.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="машиночитаемый вывод")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="команда")

    def add(name, help, fn):
        s = sub.add_parser(name, help=help, description=help, parents=[common])
        s.set_defaults(fn=fn)
        return s

    s = add("state", "готовая ежедневная сводка: сроки, баллы, новое в курсах, состояние работ",
            cmd_state)
    s.add_argument("--days", type=int, help="окно дедлайнов, дней")
    s.add_argument("--local", action="store_true", help="без обращения к ТУИС")
    s.add_argument("--no-save", action="store_true", help="не обновлять снимок состояния")
    s.add_argument("--pull", action="store_true", help="забрать новые файлы курсов в stash/")
    s.add_argument("--since", help="считать прошлым запуском: ГГГГ-ММ-ДД, N дней назад, never, all")

    s = add("digest", "та же сводка, но только по ТУИС, без репозиториев", cmd_digest)
    s.add_argument("--days", type=int, help="окно дедлайнов, дней")
    s.add_argument("--no-save", action="store_true", help="не обновлять снимок состояния")
    s.add_argument("--since", help="считать прошлым запуском: ГГГГ-ММ-ДД, N дней назад, never, all")

    s = add("answer", "заготовка ответа в ТУИС по лабораторной работе", cmd_answer)
    s.add_argument("code", help="код предмета, каталог в ~/work/study")
    s.add_argument("num", help="номер лабораторной работы")
    s.add_argument("--tag", help="тег релиза; по умолчанию последний")

    add("me", "кто я и сколько функций доступно токену", cmd_me)

    s = add("courses", "мои курсы: список + строка COURSE_IGNORE для config.env", cmd_courses)
    s.add_argument("--all", action="store_true", help="включая скрытые")
    s.add_argument("--setup", action="store_true", help="интерактивно записать COURSE_IGNORE/CODE в config.env")

    s = add("functions", "функции, доступные токену", cmd_functions)
    s.add_argument("filter", nargs="?", help="подстрока имени")

    s = add("call", "произвольный вызов ручки Moodle", cmd_call)
    s.add_argument("function")
    s.add_argument("params", nargs="*", metavar="ключ=значение",
                   help="повтор ключа кодируется как массив")

    s = add("assigns", "задания и сроки", cmd_assigns)
    s.add_argument("--course", help="id или код предмета")

    s = add("calendar", "события календаря (только дедлайны, расписания пар в Moodle нет)",
            cmd_calendar)
    s.add_argument("--days", type=int, default=30)

    s = add("grades", "баллы по курсам", cmd_grades)
    s.add_argument("--course", help="id или код предмета")

    s = add("files", "файлы курса: что появилось и забрать в stash/", cmd_files)
    s.add_argument("course", help="код предмета или id курса")
    s.add_argument("--pull", action="store_true", help="скачать новые в <код>/stash/")
    s.add_argument("--all", action="store_true", help="показать все файлы, не только новые")
    s.add_argument("--force", action="store_true", help="перекачать даже то, что уже лежит")

    s = add("status", "состояние моего ответа по заданию", cmd_status)
    s.add_argument("assign_id", type=int)

    s = add("upload", "загрузка файлов в черновую область, печатает itemid", cmd_upload)
    s.add_argument("files", nargs="+")
    s.add_argument("--itemid", type=int, default=0, help="добавить к существующему itemid")

    s = add("submit", "отправка ответа на задание (необратимо)", cmd_submit)
    s.add_argument("assign_id", type=int)
    s.add_argument("--text", help="файл с текстом ответа (у заданий «только файлы» не нужен)")
    s.add_argument("--files", type=int, help="itemid из study upload")
    s.add_argument("--confirm", action="store_true", help="подтвердить отправку")

    for host, name in (("gv", "GitVerse"), ("sc", "SourceCraft")):
        h = sub.add_parser(host, help=name + ": релизы и вложения")
        h.set_defaults(host=host)
        hs = h.add_subparsers(dest="hostcmd", required=True, metavar="команда")
        hs.add_parser("releases", help="список релизов",
                      parents=[common]).set_defaults(fn=cmd_host_releases)
        r = hs.add_parser("release", help="создать релиз", parents=[common])
        r.set_defaults(fn=cmd_host_release)
        r.add_argument("tag")
        r.add_argument("--title", required=True)
        r.add_argument("--notes", required=True, help="файл с описанием")
        r.add_argument("--sha", help="GitVerse: полный SHA; по умолчанию из тега")
        u = hs.add_parser("update", help="изменить название или описание релиза", parents=[common])
        u.set_defaults(fn=cmd_host_update)
        u.add_argument("tag")
        u.add_argument("--title")
        u.add_argument("--notes", help="файл с описанием")
        a = hs.add_parser("asset", help="загрузить файл в релиз", parents=[common])
        a.set_defaults(fn=cmd_host_asset)
        a.add_argument("release", help="GitVerse: id релиза, SourceCraft: тег")
        a.add_argument("file")
        a.add_argument("--name", help="имя файла в релизе")
        q = hs.add_parser("api", help="произвольный запрос", parents=[common])
        q.set_defaults(fn=cmd_host_api)
        q.add_argument("path", help="например /repos/owner/repo/releases")

    rt = sub.add_parser("rt", help="Rutube: вход и видео")
    rts = rt.add_subparsers(dest="rtcmd", required=True, metavar="команда")
    lg = rts.add_parser("login", help="режим token: вход по email и паролю (пароль не хранится); "
                        "только для аккаунтов с паролем", parents=[common])
    lg.set_defaults(fn=cmd_rt_login)
    lg.add_argument("--email")
    jw = rts.add_parser("jwt", help="режим jwt: сохранить refreshToken из cookie браузера "
                        "(VK ID / Gazprom ID)", parents=[common])
    jw.set_defaults(fn=cmd_rt_jwt)
    jw.add_argument("--refresh", help="сам refreshToken или строка cookie; без флага спросит скрыто")
    me = rts.add_parser("me", help="проверить вход: мои видео", parents=[common])
    me.set_defaults(fn=cmd_rt_me)
    me.add_argument("--mode", choices=["auto", "jwt", "token"], default="auto",
                    help="какой вход использовать (по умолчанию auto)")
    ra = rts.add_parser("api", help="произвольный GET к rutube.ru/api", parents=[common])
    ra.set_defaults(fn=cmd_rt_api)
    ra.add_argument("path", help="например /video/person/")
    ra.add_argument("--mode", choices=["auto", "jwt", "token"], default="auto",
                    help="какой вход использовать (по умолчанию auto)")
    rts.add_parser("categories", help="список категорий Rutube (id для --category)",
                   parents=[common]).set_defaults(fn=cmd_rt_categories)
    vv = rts.add_parser("video", help="метаданные и состояние своего видео", parents=[common])
    vv.set_defaults(fn=cmd_rt_video)
    vv.add_argument("video_id")
    vv.add_argument("--mode", choices=["auto", "jwt", "token"], default="auto")
    ed = rts.add_parser("edit", help="правка названия/описания/категории/видимости", parents=[common])
    ed.set_defaults(fn=cmd_rt_edit)
    ed.add_argument("video_id")
    ed.add_argument("--title")
    ed.add_argument("--desc", help="файл с описанием")
    ed.add_argument("--category", type=int, help="id категории (см. rt categories)")
    ed.add_argument("--age", type=int, choices=[0, 6, 12, 14, 16, 18], help="возрастное ограничение")
    ed.add_argument("--hidden", action="store_true", help="сделать скрытым")
    ed.add_argument("--visible", action="store_true", help="сделать публичным")
    ed.add_argument("--mode", choices=["auto", "jwt", "token"], default="auto")
    pl = rts.add_parser("playlist", help="плейлисты: list/create/add")
    pls = pl.add_subparsers(dest="plcmd", required=True, metavar="действие")
    pll = pls.add_parser("list", help="свои плейлисты", parents=[common])
    pll.set_defaults(fn=cmd_rt_pl_list)
    pll.add_argument("--mode", choices=["auto", "jwt", "token"], default="auto")
    plc = pls.add_parser("create", help="создать плейлист", parents=[common])
    plc.set_defaults(fn=cmd_rt_pl_create)
    plc.add_argument("--title", required=True)
    plc.add_argument("--hidden", action="store_true", help="скрытый плейлист")
    plc.add_argument("--mode", choices=["auto", "jwt", "token"], default="auto")
    pla = pls.add_parser("add", help="добавить видео в плейлист", parents=[common])
    pla.set_defaults(fn=cmd_rt_pl_add)
    pla.add_argument("playlist_id")
    pla.add_argument("video_id")
    pla.add_argument("--mode", choices=["auto", "jwt", "token"], default="auto")
    up = rts.add_parser("upload", help="загрузить видео: файл (tus) или --url", parents=[common])
    up.set_defaults(fn=cmd_rt_upload)
    up.add_argument("file", nargs="?", help="локальный видеофайл (либо задать --url)")
    up.add_argument("--url", help="импорт по URL — Rutube скачает сам")
    up.add_argument("--title")
    up.add_argument("--desc", help="файл с описанием")
    up.add_argument("--category", type=int, help="id категории (см. rt categories; по умолчанию 13)")
    up.add_argument("--age", type=int, choices=[0, 6, 12, 14, 16, 18], help="возраст (по умолчанию 0+)")
    up.add_argument("--hidden", action="store_true", help="загрузить скрытым")
    up.add_argument("--playlist", help="id плейлиста — сразу добавить туда")
    up.add_argument("--slot", choices=["lab", "report", "presentation", "defense"],
                    help="напечатать строку RUTUBE_<SLOT>= для tuis/labNN.env")
    up.add_argument("--confirm", action="store_true", help="подтвердить загрузку (без него — план)")
    up.add_argument("--mode", choices=["auto", "jwt", "token"], default="auto")
    return p


def main(argv=None):
    parser = build_parser()
    argv = sys.argv[1:] if argv is None else list(argv)
    if not argv:
        parser.print_help()
        return 0
    args = parser.parse_args(argv)
    try:
        data, text = args.fn(Config(), args)
    except StudyError as e:
        print(e.text(), file=sys.stderr)
        if e.hint():
            print(e.hint(), file=sys.stderr)
        return 2
    except BrokenPipeError:
        return 0
    print(json.dumps(data, ensure_ascii=False, indent=1) if args.json else text)
    return 0
