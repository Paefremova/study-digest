"""Снимок состояния между сводками: что считать «прошлым запуском».

`state.json` — последний снимок; рядом в `state/ГГГГ-ММ-ДД.json` — по одному на день
(последнее сохранение дня), чтобы сводку можно было пересчитать от любой даты.
"""
import datetime
import json
import re
import time

from .config import StudyError, write_atomic

KEEP_DAYS = 60
DAY = 86400


def history_dir(cfg):
    """Каталог снимков по дням — рядом с текущим снимком."""
    return cfg.state_file().with_name("state")


def history(cfg, day=None):
    """Дневные снимки не позже `day` (ГГГГ-ММ-ДД), от старых к новым."""
    return sorted(p for p in history_dir(cfg).glob("????-??-??.json")
                  if day is None or p.stem <= day)


def read(path):
    """Снимок из файла; битый (обрыв записи, правка руками) — None."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def latest(paths):
    """Последний целый снимок из списка → (снимок, путь); нет — (None, None)."""
    for p in reversed(paths):
        state = read(p)
        if state is not None:
            return state, p
    return None, None


def load_state(cfg, since=None, errors=None):
    """Состояние, которое считать прошлым запуском, по значению `--since`:

    None — текущий `state.json`; если он повреждён — последний целый дневной снимок,
    а без него пустой, как при первом запуске (об этом — запись в `errors`);
    `never` — снимка нет, как при первом запуске: обновления не отслеживаются;
    `all` — пустой снимок с точкой отсчёта в начале времён: новым считается всё;
    число — столько дней назад; `ГГГГ-ММ-ДД` — с полуночи этого дня: ближайший снимок
    не позже этой точки, а пока истории нет — текущий файл с ней как точкой отсчёта."""
    current = cfg.state_file()
    if since is None:
        state = read(current) if current.exists() else {}
        if state is not None:
            return state
        state, path = latest(history(cfg))
        if errors is not None:
            errors.append({"source": "local", "where": "снимок", "code": None,
                           "message": f"{current.name} повреждён, "
                                      + (f"взят снимок за {path.stem}" if path
                                         else "считаю первым запуском")})
        return state or {}
    if since == "never":
        return {}
    if since == "all":
        return {"last_run": 1, "grades": {}}
    if re.fullmatch(r"\d+", since):
        since = int(time.time()) - int(since) * DAY
    else:
        try:
            since = int(datetime.datetime.strptime(since, "%Y-%m-%d").timestamp())
        except ValueError:
            raise StudyError("config", "--since: ожидается ГГГГ-ММ-ДД, число дней, never или all, "
                                       f"а не «{since}»") from None
    state, _ = latest(history(cfg, time.strftime("%Y-%m-%d", time.localtime(since))))
    if state is not None:
        return state
    state = (read(current) if current.exists() else None) or {}
    return {**state, "last_run": since}


def save_state(cfg, state):
    """Записать снимок: текущий файл, копия за день по last_run, старше KEEP_DAYS — удалить."""
    current = cfg.state_file()
    current.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(state, ensure_ascii=False, indent=1)
    write_atomic(current, text)
    hist = history_dir(cfg)
    hist.mkdir(exist_ok=True)
    write_atomic(hist / time.strftime("%Y-%m-%d.json", time.localtime(state["last_run"])), text)
    cutoff = time.strftime("%Y-%m-%d", time.localtime(state["last_run"] - KEEP_DAYS * DAY))
    for p in hist.glob("????-??-??.json"):
        if p.stem < cutoff:
            p.unlink()
