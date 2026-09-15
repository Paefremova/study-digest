"""Настройки: единственный парсер config.env, курсы, пути, токены.

Приоритет значений: переменная окружения → строка в config.env → значение по умолчанию.
Если ключ в config.env встречается дважды, побеждает последнее вхождение.
"""
import os
import pathlib
import re
from dataclasses import dataclass
from typing import Optional

HERE = pathlib.Path(__file__).resolve().parent.parent  # каталог .digest/
ROOT = HERE.parent                                     # ~/work/study

DEFAULTS = {
    "TUIS_URL": "https://esystem.rudn.ru",
    # Токены — строками TUIS_TOKEN=… в config.env. Rutube ведёт свои файлы сам (см. rutube.py);
    # RUTUBE_TOKEN — запасной путь для режима token, если значения в config.env нет.
    "RUTUBE_TOKEN_FILE": ".secrets/rutube-token",
    "RUTUBE_REFRESH_FILE": ".secrets/rutube-refresh",
    "RUTUBE_ACCESS_FILE": ".secrets/rutube-access",
    "DIGEST_DAYS": "21",
    "DIGEST_ACTIVE_DAYS": "60",
    "DIGEST_STATE": ".state.json",
    "GV_REPO": "",
    "SC_REPO": "",
}

# Подсказки к известным кодам ошибок — что делать пользователю.
HINTS = {
    "invalidtoken": "Токен просрочен или отозван. Профиль Moodle → «Ключи безопасности» → "
                    "служба Moodle mobile web service → «Очистка», значение показывается один раз.",
    "nopermissiontoviewgrades": "Запись на курс истекла, оценки недоступны.",
    "invalidrecord": "Функция не входит в службу этого токена: у служб РУДН свои ключи.",
    "notoken": "Статические токены — строками в config.env (их спрашивает setup.sh), "
               "Rutube — `study rt jwt` или `study rt login`. См. README.md, «Установка».",
}


class StudyError(Exception):
    """Единственный тип ошибки во всём инструменте."""

    def __init__(self, source, message, code=None, where=None):
        self.source = source          # config | moodle | gitverse | sourcecraft | rutube | git | local
        self.message = str(message)
        self.code = code
        self.where = where
        super().__init__(self.text())

    def text(self):
        parts = [self.source]
        if self.where:
            parts.append(self.where)
        parts.append(self.message)
        line = ": ".join(parts)
        if self.code:
            line += f" ({self.code})"
        return line

    def hint(self):
        return HINTS.get(self.code or "")

    def as_dict(self):
        return {"source": self.source, "where": self.where,
                "code": self.code, "message": self.message}


@dataclass
class Course:
    """Курс ТУИС: id, имя локальной папки (строка CODE) и название."""
    id: int
    code: Optional[str] = None
    title: str = ""

    @property
    def dir(self):
        return ROOT / self.code if self.code else None

    def as_dict(self):
        return {"id": self.id, "code": self.code, "title": self.title}


class Config:
    def __init__(self, path=None):
        self.path = pathlib.Path(path) if path else HERE / "config.env"
        self._values = {}
        self._codes = {}         # CODE <id> <code>: id → имя локальной папки
        self._ignore = set()     # COURSE_IGNORE: id, которые не отслеживаем
        self._tokens = {}
        self._read()

    def _read(self):
        if not self.path.exists():
            return
        for line in self.path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(None, 2)
            if parts[0] == "CODE" and len(parts) == 3 and parts[1].isdigit():
                self._codes[int(parts[1])] = parts[2].strip()
            elif "=" in line:
                key, _, value = line.partition("=")
                self._values[key.strip()] = value.strip()
        self._ignore = {int(x) for x in re.findall(r"\d+", self._values.pop("COURSE_IGNORE", ""))}

    def get(self, key):
        return os.environ.get(key) or self._values.get(key) or DEFAULTS.get(key, "")

    def path_of(self, key):
        """Путь из настройки: тильда разворачивается, относительный — от каталога .digest/."""
        p = pathlib.Path(self.get(key)).expanduser()
        return p if p.is_absolute() else HERE / p

    def token(self, name):
        """Токен: значение из config.env; иначе файл <NAME>_FILE, если такая настройка есть."""
        if name not in self._tokens:
            val = self.get(name)
            if not val:
                f = self.path_of(name + "_FILE") if self.get(name + "_FILE") else None
                if not f or not f.exists():
                    raise StudyError("config", f"нет {name} в {self.path.name}", code="notoken")
                val = f.read_text().strip()
            self._tokens[name] = val
        return self._tokens[name]

    # --- курсы

    def ignore(self):
        return set(self._ignore)

    def codes(self):
        """Карта CODE: id курса → имя локальной папки."""
        return dict(self._codes)

    def track(self, courses):
        """Из списка moodle.courses() (id/fullname) — отслеживаемые Course минус игнор."""
        return [Course(c["id"], self._codes.get(c["id"]), c.get("fullname") or "")
                for c in courses if c["id"] not in self._ignore]

    def write_courses(self, ignore_ids, codes):
        """Переписать в config.env строки COURSE_IGNORE и CODE (и убрать старые COURSE)."""
        keep = [ln for ln in self.path.read_text().splitlines()
                if not re.match(r"\s*(COURSE_IGNORE\s*=|CODE\s|COURSE\s)", ln)]
        keep.append("COURSE_IGNORE=" + " ".join(str(i) for i in sorted(ignore_ids)))
        keep += [f"CODE {cid} {code}" for cid, code in sorted(codes.items())]
        self.path.write_text("\n".join(keep) + "\n")
        os.chmod(self.path, 0o600)
        self._ignore, self._codes = set(ignore_ids), dict(codes)

    # --- сводка

    def days(self):
        return int(self.get("DIGEST_DAYS"))

    def active_days(self):
        return int(self.get("DIGEST_ACTIVE_DAYS"))

    def state_file(self):
        return self.path_of("DIGEST_STATE")
