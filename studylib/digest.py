"""Сводка по ТУИС: сбор данных в структуру и рендер.

Данные и рендер разделены: `--json` отдаёт ровно то, что видит рендер, без второго обхода API.
"""
import re
import time

from . import files, hosting, local
from .config import StudyError
from .fmt import md_table, moment, plain, short_name, weekday
from .snapshot import load_state, save_state

# Что считаем новостью в core_course_get_updates_since; остальное (submissions, grades,
# answers) — своя же активность и чужие голоса, то есть шум.
USEFUL = {"contentfiles", "introfiles", "configuration", "contents", "files"}
FILES = {"contentfiles", "files", "contents"}
# Сроки в составе курса: машинные идентификаторы, а не подписи — подпись зависит от языка.
DATE_IDS = {"duedate", "timeclose"}
KINDS = {"assign": "задание", "choice": "выбор темы",
         "workshop": "взаимная проверка", "feedback": "опрос"}
SUBMISSION = {"new": "не сдано", "draft": "черновик", "reopened": "переоткрыто",
              "submitted": "сдано", "hidden": "доступ закрыт"}
# Уведомления, которые Moodle шлёт сам: про наши же действия, про сроки (они уже в таблице)
# и про входы в аккаунт. Отсев по eventtype, а не по теме: тема зависит от языка.
AUTO_EVENTS = {"assign_due_soon", "assign_due_digest", "assign_notification", "newlogin"}
URGENT = 2 * 86400
MONTH = 30 * 86400


class Errors:
    """Мягкие ошибки: копятся, не роняют сводку, но и не теряются."""

    def __init__(self, strict=False):
        self.items = []
        self.strict = strict

    def soft(self, where):
        return _Soft(self, where)

    def add(self, err, where=None):
        item = err.as_dict()
        if where:
            item["where"] = where
        self.items.append(item)


class _Soft:
    def __init__(self, errors, where):
        self.errors, self.where = errors, where

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if isinstance(exc, StudyError):
            if self.errors.strict:
                return False
            self.errors.add(exc, self.where)
            return True
        return False


def lab_number(name):
    """Номер лабы из названия задания — через ту же таблицу имён, что и `short_name`:
    домашние работы и доклады каталога labNN не имеют."""
    m = re.match(r"ЛР (\d+)$", short_name(name, tail=False))
    return m.group(1).zfill(2) if m else None


def collect(cfg, moodle, days=None, save=True, strict=False, since=None):
    """Всё, что знает ТУИС: дедлайны, тесты, обновления, уведомления, баллы.

    `since` — что считать прошлым запуском, строка `--since` (см. `snapshot.load_state`)."""
    now = int(time.time())
    days = days or cfg.days()
    errors = Errors(strict)
    state = load_state(cfg, since)
    since = state.get("last_run")
    known = state.get("assignments", {})
    graded = state.get("grades")  # {курс: {работа: балл}} с прошлого запуска; None — снимка ещё нет

    watch = {c.id: c for c in cfg.courses()}
    events = []
    with errors.soft("календарь"):
        events = moodle.calendar(now - 7 * 86400, now + 120 * 86400)
    active = {(e.get("course") or {}).get("id") for e in events} - {None}

    courses = [c for c in moodle.courses() if c["id"] in (watch or active)]
    cmap = {c["id"]: (watch[c["id"]].title if c["id"] in watch else c["fullname"])
            for c in courses}
    codes = {c["id"]: (watch[c["id"]].code if c["id"] in watch else None) for c in courses}

    def course_of(cid):
        return {"id": cid, "code": codes.get(cid), "title": cmap.get(cid, "")}

    # --- задания
    assigns, soon, overdue, new_assigns, moved = {}, [], [], [], []
    course_list, _ = moodle.assignments()
    for c in course_list:
        if c["id"] not in cmap:
            continue
        for a in c["assignments"]:
            item = {"kind": "assign", "source": "assign_api", "assign_id": a["id"],
                    "cmid": a["cmid"], "course": course_of(c["id"]), "name": a["name"],
                    "short": short_name(a["name"]), "due": moment(a.get("duedate"), now),
                    "submission": None, "intro": plain(a.get("intro")),
                    "lab": lab_number(a["name"])}
            assigns[str(a["id"])] = item
            due = a.get("duedate") or 0
            prev = known.get(str(a["id"]))
            if since and prev is None:
                new_assigns.append(item)
            elif prev is not None and due and prev != due:
                moved.append({**item, "was": moment(prev, now)})
            if not due:
                continue
            if now <= due <= now + days * 86400:
                soon.append(item)
            elif due < now <= due + 30 * 86400:
                overdue.append(item)

    for item in sorted(soon + overdue, key=lambda x: x["due"]["ts"]):
        with errors.soft(f"статус задания {item['assign_id']}"):
            st = moodle.submission_status(item["assign_id"])
            sub = (st.get("lastattempt") or {}).get("submission") or {}
            item["submission"] = sub.get("status") or "new"
            item["grade"] = ((st.get("feedback") or {}).get("grade") or {}).get("grade")

    # --- элементы курса со сроками: ловят задания, скрытые ограничением доступа.
    # Статус ответа у них не запросить (requireloginerror), поэтому сразу "hidden".
    contents_cache = {}

    def contents(cid):
        if cid not in contents_cache:
            contents_cache[cid] = []
            with errors.soft(f"состав курса {cid}"):
                contents_cache[cid] = moodle.contents(cid)
        return contents_cache[cid]

    seen = {a["cmid"] for a in assigns.values()}
    for c in courses:
        for sec in contents(c["id"]):
            for m in sec.get("modules", []):
                if m["id"] in seen or m["modname"] not in KINDS:
                    continue
                for d in m.get("dates") or []:
                    if d.get("dataid") not in DATE_IDS:
                        continue
                    ts = d.get("timestamp") or 0
                    if now <= ts <= now + days * 86400:
                        soon.append({"kind": "activity", "source": "course_contents",
                                     "modname": m["modname"], "cmid": m["id"],
                                     "course": course_of(c["id"]), "name": m["name"],
                                     "short": short_name(m["name"]), "due": moment(ts, now),
                                     "submission": "hidden" if m["modname"] == "assign" else None,
                                     "intro": "", "lab": lab_number(m["name"])})

    # --- выбор темы доклада: сам срок ничего не говорит, важно, выбрана ли тема
    picks = [a for a in soon if a.get("modname") == "choice"]
    if picks:
        by_cmid = {}
        with errors.soft("темы докладов"):
            by_cmid = {c["coursemodule"]: c["id"]
                       for c in moodle.choices({a["course"]["id"] for a in picks})}
        for a in picks:
            cid = by_cmid.get(a["cmid"])
            if not cid:
                continue
            with errors.soft(f"варианты выбора {cid}"):
                opts = moodle.choice_options(cid)
                mine = [o["text"] for o in opts if o.get("checked")]
                a["choice"] = {"chosen": mine[0] if mine else None, "options": len(opts)}
                a["submission"] = "submitted" if mine else "new"

    # --- тесты
    quizzes = []
    with errors.soft("тесты"):
        for q in moodle.quizzes([c["id"] for c in courses]):
            close = q.get("timeclose") or 0
            if not close or close < now:
                continue
            used = None
            with errors.soft(f"попытки теста {q['id']}"):
                used = len(moodle.quiz_attempts(q["id"]))
            quizzes.append({"kind": "quiz", "source": "quiz", "quiz_id": q["id"],
                            "course": course_of(q["course"]), "name": q["name"],
                            "short": short_name(q["name"]),
                            "due": moment(close, now), "attempts_used": used,
                            "attempts_max": q.get("attempts") or None,
                            "timelimit_min": (q.get("timelimit") or 0) // 60 or None})
    soon += [q for q in quizzes if q["due"]["ts"] <= now + days * 86400]
    ahead = [q for q in quizzes if q["due"]["ts"] > now + days * 86400]

    # --- обновления в курсах
    updates = []
    if since:
        for c in courses:
            changed = []
            with errors.soft(f"обновления курса {c['id']}"):
                changed = [u for u in moodle.updates_since(c["id"], since) if u.get("updates")]
            if not changed:
                continue
            names = {m["id"]: (m["name"], m["modname"], sec["name"], m.get("contents") or [])
                     for sec in contents(c["id"]) for m in sec.get("modules", [])}
            for u in changed:
                kinds = {x["name"] for x in u["updates"]} & USEFUL
                if not kinds:
                    continue
                name, modname, section, items = names.get(
                    u["id"], (f"(модуль {u['id']})", "", "", []))
                # имена файлов — чтобы сводка говорила «появился 002-dns.pdf», а не «новые файлы»
                new_files = [i["filename"] for i in items
                             if i.get("type") == "file" and i.get("filesize")
                             and (i.get("timemodified") or 0) > since]
                updates.append({"course": course_of(c["id"]), "section": section,
                                "item": name, "modname": modname, "files": new_files,
                                "what": "новые файлы" if kinds & FILES else "изменены настройки"})

    # --- уведомления, баллы, курсы вне списка
    # Показываются один раз — те, что пришли после прошлого запуска, как и обновления курсов.
    notifications = []
    with errors.soft("уведомления"):
        for m in moodle.notifications(limit=20):
            if m["timecreated"] > (since or 0) and m.get("eventtype") not in AUTO_EVENTS:
                notifications.append({"id": m["id"], "at": moment(m["timecreated"], now),
                                      "subject": plain(m.get("subject"), 120)})
    notifications.sort(key=lambda n: -n["at"]["ts"])

    # Баллы: итог по курсу и то, что появилось или изменилось с прошлого запуска.
    grades = []
    for c in courses:
        with errors.soft(f"оценки, курс {c['id']}"):
            for t in moodle.grades(c["id"]):
                got = [i for i in t.get("gradeitems", [])
                       if i.get("graderaw") is not None and i.get("itemtype") != "course"]
                total = next((i for i in t.get("gradeitems", [])
                              if i.get("itemtype") == "course"), None)
                if not got:
                    continue
                # Итог курса Moodle может прятать; тогда считаем сумму работ сами.
                raw = total.get("graderaw") if total else None
                tot = ({"raw": raw, "max": total["grademax"], "computed": False}
                       if raw is not None else
                       {"raw": sum(i["graderaw"] for i in got),
                        "max": (total or {}).get("grademax") or sum(i["grademax"] for i in got),
                        "computed": True})
                before = (graded or {}).get(str(c["id"]), {})
                items = [{"name": i["itemname"], "short": short_name(i["itemname"], tail=False),
                          "raw": i["graderaw"], "max": i["grademax"],
                          "new": graded is not None and before.get(i["itemname"]) != i["graderaw"]}
                         for i in got]
                grades.append({"course": course_of(c["id"]), "items": items, "total": tot})

    outside = {}
    for e in events:
        c = e.get("course") or {}
        if c.get("id") and c["id"] not in cmap and now <= e.get("timesort", 0) <= now + days * 86400:
            key = (c["id"], c.get("fullname") or c.get("shortname"))
            outside.setdefault(key, []).append(e)

    deadlines = sorted(soon, key=lambda x: x["due"]["ts"])
    data = {
        "schema": 1, "now": moment(now, now), "days": days,
        "first_run": not since, "since": moment(since, now) if since else None,
        "courses": [course_of(c["id"]) for c in courses],
        "deadlines": deadlines,
        "overdue": sorted([a for a in overdue if a["submission"] in ("new", None)],
                          key=lambda x: x["due"]["ts"]),
        "submitted": sorted([a for a in overdue if a["submission"] not in ("new", None)],
                            key=lambda x: x["due"]["ts"]),
        # просроченное и несданное — впереди: пересдача всё ещё стоит баллов
        "not_started": [a for a in sorted(overdue, key=lambda x: x["due"]["ts"]) + deadlines
                        if a["source"] == "assign_api" and a["submission"] == "new"],
        "quizzes_ahead": sorted(ahead, key=lambda x: x["due"]["ts"]),
        "updates": updates, "new_assignments": new_assigns, "moved": moved,
        "notifications": notifications, "grades": grades,
        "outside": [{"course": {"id": cid, "title": name},
                     "count": len(evs),
                     "nearest": {"name": (min(evs, key=lambda x: x["timesort"])["name"] or "")[:60],
                                 "at": moment(min(e["timesort"] for e in evs), now)}}
                    for (cid, name), evs in outside.items()],
        "errors": errors.items,
    }

    if save:
        save_state(cfg, {
            "last_run": now,
            "assignments": {i: (a["due"]["ts"] if a["due"] else 0) for i, a in assigns.items()},
            "courses": {str(c["id"]): c["fullname"] for c in courses},
            "grades": {str(g["course"]["id"]): {i["name"]: i["raw"] for i in g["items"]}
                       for g in grades},
        })
    return data


def attempts(q):
    used = q["attempts_used"] if q["attempts_used"] is not None else "?"
    return "%s из %s" % (used, q["attempts_max"] or "∞")


def status_of(a):
    """Колонка «Состояние» — только по ТУИС. Готовность лабы на диске (`labs[].ready`)
    остаётся в JSON для сессий над лабой: лаба делается в один заход, в сводке это шум."""
    pick = a.get("choice")
    if pick:
        return ("выбрана: " + pick["chosen"] if pick["chosen"]
                else "не выбрана, %d вариантов" % pick["options"])
    if a["kind"] == "quiz":
        lim = ", %d мин" % a["timelimit_min"] if a["timelimit_min"] else ""
        return "попыток " + attempts(a) + lim
    if a["submission"] is None:
        # статус не получен (ошибка в errors) или элемент без ответа: опрос, взаимная проверка
        return KINDS.get(a.get("modname"), "?") if a["kind"] == "activity" else "?"
    return SUBMISSION.get(a["submission"], a["submission"])


def label(a):
    """Курс в таблице: код из config.env, а без него — название из ТУИС."""
    return a["course"]["code"] or a["course"]["title"]


def bold(cells):
    return ["**%s**" % c if c not in ("", "—") else c for c in cells]


def render(d):
    """Готовая сводка в markdown: то, что рутина печатает как есть."""
    t = d.get("tuis") or {}
    now = d["now"]["ts"]
    out = ["# Учёба · %s %s" % (weekday(now), d["now"]["text"])]
    if t.get("first_run"):
        out.append("\nПервый запуск: обновления в курсах начнут отслеживаться со следующего раза.")

    news = []
    for u in t.get("updates", []):
        if u.get("pulled"):
            files_ = ", ".join(u["pulled"])
        elif not u["files"]:
            files_ = "—"
        elif u["course"]["code"]:
            files_ = ", ".join(u["files"]) + " — не скачаны"
        else:
            files_ = ", ".join(u["files"]) + " — курса нет в config.env"
        news.append([label(u), u["section"] or "—", "%s: %s" % (u["item"], u["what"]), files_])
    for a in t.get("new_assignments", []):
        news.append([label(a), "—",
                     "новое задание: %s, до %s" % (a["short"], a["due"]["text"] if a["due"] else "—"),
                     "—"])
    for a in t.get("moved", []):
        news.append([label(a), "—",
                     "срок сдвинут: %s, было %s → стало %s" % (
                         a["short"], a["was"]["text"] if a["was"] else "—",
                         a["due"]["text"] if a["due"] else "—"), "—"])

    rows = []
    for a in t.get("overdue", []):
        rows.append(bold([a["due"]["text"], "просрочено", a["short"], label(a), status_of(a)]))
    for a in t.get("deadlines", []):
        if a.get("submission") == "submitted":  # у тестов ключа нет
            continue
        cells = [a["due"]["text"], a["due"]["left"], a["short"], label(a), status_of(a)]
        rows.append(bold(cells) if a["due"]["left_sec"] < URGENT else cells)
    if rows:
        out.append("\n## Сроки\n")
        out.append(md_table(rows, ["Когда", "Осталось", "Работа", "Курс", "Состояние"]))
    elif d.get("tuis") is None:
        out.append("\nТУИС не опрашивался (`--local`): только состояние репозиториев.")
    else:
        out.append("\nСроков в ближайшие %d дн нет.%s" % (
            d["days"], "" if news or t.get("first_run") else " Обновлений нет."))

    # Неполадки репозитория — одной строкой на курс и только когда они есть.
    trouble = []
    for c in d["courses"]:
        repo = c["repo"]
        if not repo:
            continue
        bad = []
        if repo["dirty"]:
            bad.append("незакоммичено %d" % len(repo["dirty"]))
        for u in c["unreleased_tags"]:
            bad.append("нет релиза на %s (%s)" % (u["hosting"], ", ".join(u["tags"])))
        for name, r in c["releases"].items():
            latest = r.get("latest")
            if latest and not latest.get("assets"):
                bad.append("релиз %s на %s без файлов" % (latest["tag"], name))
        if bad:
            trouble.append("%s: %s" % (c["code"], ", ".join(bad)))
    if trouble:
        out.append("\n" + "; ".join(trouble) + ".")

    if t.get("grades"):
        marks = []
        for g in t["grades"]:
            fresh = ["%s %.2f/%g" % (i["short"], i["raw"], i["max"]) for i in g["items"] if i["new"]]
            marks.append([label(g), "%.2f / %g" % (g["total"]["raw"], g["total"]["max"]),
                          " · ".join(fresh) or "—"])
        out.append("\n## Баллы\n")
        out.append(md_table(marks, ["Курс", "Итого", "Новое"]))

    if t.get("notifications"):
        out.append("\n## Уведомления\n")
        out.append(md_table([[n["at"]["text"], n["subject"]] for n in t["notifications"]],
                            ["Когда", "Тема"]))

    if news:
        out.append("\n## Новое в курсах\n")
        out.append(md_table(news, ["Курс", "Раздел", "Что", "Файлы"]))

    quizzes = [q for q in t.get("quizzes_ahead", []) if q["due"]["left_sec"] < MONTH]
    if quizzes:
        out.append("\n## Тесты\n")
        out.append(md_table(
            [[q["short"], label(q), q["due"]["full"], attempts(q),
              "%d мин" % q["timelimit_min"] if q["timelimit_min"] else "—"]
             for q in quizzes], ["Тест", "Курс", "Когда", "Попытки", "Время"]))

    if t.get("outside"):
        out.append("\n## Дедлайны вне списка курсов\n")
        out.append(md_table([[o["nearest"]["at"]["text"], o["nearest"]["name"],
                              "%s (id %d, ещё %d)" % (o["course"]["title"], o["course"]["id"], o["count"])]
                             for o in t["outside"]], ["Когда", "Работа", "Курс"]))
        out.append("\nДобавить курс в `config.env` или убедиться, что он неактуален.")

    todo = t.get("not_started") or []
    if todo:
        a = todo[0]
        code = a["course"]["code"]
        out.append("\n## Предлагаю начать\n")
        out.append("**%s** · %s · до %s · методички: %s" % (
            a["short"], code or a["course"]["title"], a["due"]["text"],
            code + "/stash/" if code else "каталога курса нет"))
    return "\n".join(out)


def render_digest(d):
    """`study digest`: тот же вид, но без состояния локальных репозиториев."""
    return render({"now": d["now"], "days": d["days"], "tuis": d, "courses": []})


def state(cfg, moodle, days=None, with_tuis=True, save=True, pull=False, strict=False,
          since=None):
    """Сводка ТУИС плюс состояние локальных репозиториев — всё одним объектом."""
    errors = Errors(strict)
    tuis = None
    if with_tuis:
        tuis = collect(cfg, moodle, days=days, save=save, strict=strict, since=since)
        if pull:
            # `since` берётся из сводки: снимок состояния к этому моменту уже сдвинут на «сейчас»
            since = (tuis["since"] or {}).get("ts", 0)
            for course in cfg.courses():
                todo = [u for u in tuis["updates"] if u["files"] and u["course"]["id"] == course.id]
                if not todo or not course.code:
                    continue
                with errors.soft(f"файлы, курс {course.code}"):
                    got = files.pull(moodle, files.listing(cfg, moodle, course, since=since))
                    names = [g["name"] for g in got["pulled"]]
                    for u in todo:
                        u["pulled"] = [n for n in u["files"] if n in names]
                    errors.items.extend(got["errors"])

    by_lab = {}
    t = tuis or {}
    for a in t.get("deadlines", []) + t.get("submitted", []):
        if a.get("lab") and a["course"].get("code"):
            by_lab[(a["course"]["code"], a["lab"])] = a

    courses = []
    for course in cfg.courses():
        if not course.code:
            continue
        repo = local.course_repo(course.code)
        item = {**course.as_dict(), "dir": str(course.dir), "repo": None,
                "releases": {}, "unreleased_tags": [], "labs": []}
        if repo:
            item["repo"] = local.repo_state(repo)
            published = {}
            for name, client in hosting.both(cfg, path=repo).items():
                if isinstance(client, StudyError):
                    errors.add(client, f"{name}, курс {course.code}")
                    item["releases"][name] = {"ok": False, "latest": None}
                    continue
                with errors.soft(f"{name}, курс {course.code}"):
                    rels = client.releases()
                    published[name] = {r["tag"] for r in rels}
                    item["releases"][name] = {"ok": True, "latest": rels[0] if rels else None,
                                              "tags": [r["tag"] for r in rels]}
            for name, tags in published.items():
                missing = [t for t in item["repo"]["tags"] if t not in tags]
                if missing:
                    item["unreleased_tags"].append({"hosting": name, "tags": missing})
            for lab in local.labs(repo, course.code):
                found = by_lab.get((course.code, lab["num"]))
                lab["tuis"] = ({"assign_id": found["assign_id"], "name": found["name"],
                                "due": found["due"], "submission": found["submission"],
                                "matched_by": "number"} if found else None)
                lab["ready"] = {
                    "report": lab["report"]["built"],
                    "presentation": lab["presentation"]["built"],
                    "videos": lab["videos"]["filled"] == lab["videos"]["total"],
                    "submitted": bool(found and found["submission"] == "submitted"),
                }
                item["labs"].append(lab)
        courses.append(item)

    return {"schema": 1, "now": moment(int(time.time())), "days": days or cfg.days(),
            "tuis": tuis, "courses": courses,
            "errors": errors.items + ((tuis or {}).get("errors") or [])}
