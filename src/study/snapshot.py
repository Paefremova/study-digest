"""Снимок состояния между сводками: что считать «прошлым запуском».

`state.json` — последний снимок; рядом в `state/ГГГГ-ММ-ДД.json` — по одному на день
(последнее сохранение дня), чтобы сводку можно было пересчитать от любой даты.
"""
import datetime
import json
import re
import time

from .config import StudyError

KEEP_DAYS = 60


def history_dir(cfg):
    return cfg.state_file().with_name("state")


def load_state(cfg, since=None):
    """Состояние, которое считать прошлым запуском, по значению `--since`:

    None — текущий `state.json`;
    `never` — снимка нет, как при первом запуске: обновления не отслеживаются;
    `all` — пустой снимок с точкой отсчёта в начале времён: новым считается всё;
    число — столько дней назад; `ГГГГ-ММ-ДД` — с полуночи этого дня: ближайший снимок
    не позже этой точки, а пока истории нет — текущий файл с ней как точкой отсчёта."""
    current = cfg.state_file()
    if since is None:
        return json.loads(current.read_text()) if current.exists() else {}
    if since == "never":
        return {}
    if since == "all":
        return {"last_run": 1, "grades": {}}
    if re.fullmatch(r"\d+", since):
        since = int(time.time()) - int(since) * 86400
    else:
        try:
            since = int(datetime.datetime.strptime(since, "%Y-%m-%d").timestamp())
        except ValueError:
            raise StudyError("config", "--since: ожидается ГГГГ-ММ-ДД, число дней, never или all, "
                                       f"а не «{since}»")
    day = time.strftime("%Y-%m-%d", time.localtime(since))
    older = sorted(p for p in history_dir(cfg).glob("????-??-??.json") if p.stem <= day)
    if older:
        return json.loads(older[-1].read_text())
    state = json.loads(current.read_text()) if current.exists() else {}
    return {**state, "last_run": since}


def save_state(cfg, state):
    current = cfg.state_file()
    current.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(state, ensure_ascii=False, indent=1)
    current.write_text(text)
    hist = history_dir(cfg)
    hist.mkdir(exist_ok=True)
    (hist / time.strftime("%Y-%m-%d.json", time.localtime(state["last_run"]))).write_text(text)
    cutoff = time.strftime("%Y-%m-%d", time.localtime(state["last_run"] - KEEP_DAYS * 86400))
    for p in hist.glob("????-??-??.json"):
        if p.stem < cutoff:
            p.unlink()
