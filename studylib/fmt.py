"""Форматирование: единственный источник дат и текста для всего инструмента."""
import datetime
import html as htmllib
import re
import time


def left(sec):
    """Сколько осталось, словами."""
    if sec < 0:
        return "срок прошёл"
    if sec < 3600:
        return "меньше часа"
    if sec < 86400:
        return "%d ч" % (sec // 3600)
    return "%d дн" % (sec // 86400)


WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


def weekday(ts):
    return WEEKDAYS[time.localtime(ts).tm_wday]


def moment(ts, now=None):
    """Единый объект времени во всём JSON. Нет срока — None, а не пустая строка."""
    if not ts:
        return None
    ts = int(ts)
    now = int(now if now is not None else time.time())
    delta = ts - now
    return {
        "ts": ts,
        "iso": datetime.datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds"),
        "text": time.strftime("%d.%m %H:%M", time.localtime(ts)),
        "full": time.strftime("%d.%m.%Y %H:%M", time.localtime(ts)),
        "left": left(delta),
        "left_sec": delta,
        "overdue": delta < 0,
    }


def plain(s, limit=280):
    """HTML из описаний Moodle → однострочный текст."""
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = htmllib.unescape(re.sub(r"\s+", " ", s)).strip()
    return s[:limit] + ("…" if len(s) > limit else "")


# Названия заданий в ТУИС длинные и однотипные; в таблице нужна суть.
SHORT = [
    (r"^Сдать отч[её]т по лабораторной работе №?\s*(\d+)\.?\s*(.*)$", "ЛР {0}", "{1}"),
    (r"^Сдать отч[её]т по домашней работе №?\s*(\d+)\.?\s*(.*)$", "ДЗ {0}", "{1}"),
    (r"^Загрузка (\d+) лабораторной работы$", "ЛР {0}", ""),
    (r"^Выбрать тему доклада к лекции (\d+)$", "Тема доклада к лекции {0}", ""),
    (r"^(?:Сдать доклад\.\s*Лекция|Доклад к лекции)\s*(\d+)$", "Доклад к лекции {0}", ""),
]


def short_name(name, tail=True):
    """«Сдать отчет по лабораторной работе № 2. Простые сети» → «ЛР 2 — Простые сети»;
    `tail=False` — только «ЛР 2». Незнакомое имя — как есть, до 60 символов."""
    name = (name or "").strip()
    for pat, head, topic in SHORT:
        m = re.match(pat, name, re.I)
        if m:
            g = [x or "" for x in m.groups()]
            rest = topic.format(*g).strip(' ."«»') if tail else ""
            return head.format(*g) + (" — " + rest if rest else "")
    return plain(name, 60)


def md_table(rows, headers):
    """Markdown-таблица: сводку читают как markdown, а не в терминале."""
    def line(r):
        return "| " + " | ".join(str(c) for c in r) + " |"
    return "\n".join([line(headers), line("-" * max(len(h), 3) for h in headers)] +
                     [line(r) for r in rows])


def table(rows, headers=None):
    """Простая таблица с выравниванием по колонкам."""
    rows = [[str(c) for c in r] for r in rows]
    if not rows:
        return ""
    data = ([list(headers)] if headers else []) + rows
    widths = [max(len(r[i]) for r in data) for i in range(len(data[0]))]
    out = []
    for i, r in enumerate(data):
        out.append("  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip())
        if headers and i == 0:
            out.append("  ".join("-" * w for w in widths))
    return "\n".join(out)
