#!/usr/bin/env python3
"""Установка study: зависимости, код, затем `study setup` (токены, каталоги, проверка, курсы).

Linux, macOS:          curl -fsSL <raw>/install.py -o install.py && python3 install.py
Windows (PowerShell):  iwr <raw>/install.py -OutFile install.py; py install.py
<raw> = https://raw.githubusercontent.com/nowherewashere/study-digest/master
Из каталога с инструментом: python3 install.py — код уже на месте, сразу настройка.

Только стандартная библиотека и ничего из пакета study: его ещё нет, пока не склонирован.
Каждый шаг — свой экран: [n/7] в заголовке, строка прогресса, снизу — результат; шаги 3–7
и итоговый экран рисует `study setup`, ему передаются сделанные шаги (STUDY_SETUP).
"""
import ctypes
import json
import os
import pathlib
import platform
import shutil
import subprocess
import sys

REPO = os.environ.get("STUDY_REPO", "https://github.com/nowherewashere/study-digest.git")
HERE = pathlib.Path(__file__).resolve().parent
STEPS = ["Зависимости", "Код", "Токены", "Каталоги", "Проверка", "Оператор", "Курсы"]
RULE = "-" * 72
POSIX = os.name == "posix"


# --- оформление: копия Screen из src/study/setup.py — цвета только в терминале

class Screen:
    def __init__(self, steps):
        self.steps, self.log, self.n = list(steps), [], 0
        self.tty = sys.stdout.isatty()
        self.B, self.D, self.G, self.Y, self.R, self.N = (
            ("\033[1m", "\033[2m", "\033[32m", "\033[33m", "\033[31m", "\033[0m")
            if self.tty else ("",) * 6)

    def step(self, n):
        self.n = n
        crumbs = []
        for i, name in enumerate(self.steps, 1):
            if i < n:
                crumbs.append(f"{self.G}[x]{self.N} {self.D}{name}{self.N}")
            elif i == n:
                crumbs.append(f"{self.B}[>] {name}{self.N}")
            else:
                crumbs.append(f"{self.D}[ ] {name}{self.N}")
        clear = "\033[H\033[2J" if self.tty else ""
        title = f"{self.B}[{n}/{len(self.steps)}] {self.steps[n - 1]}{self.N}"
        sys.stdout.write(f"{clear}{self.B}study{self.N}  установка  {title}\n"
                         f"{'  '.join(crumbs)}\n{RULE}\n\n")
        sys.stdout.flush()

    def ok(self, text):
        print(f"  {self.G}+{self.N} {text}")
        self.log.append(["ok", self.steps[self.n - 1], text])

    def warn(self, text):
        print(f"  {self.Y}!{self.N} {text}")
        self.log.append(["warn", self.steps[self.n - 1], text])

    def fail(self, text):
        print(f"  {self.R}!{self.N} {text}")
        raise SystemExit(1)

    def note(self, text):
        print(f"  {self.D}{text}{self.N}")

    def ask(self, question):
        return input(f"  {self.B}>{self.N} {question} ")


def windows_console():
    """Windows: UTF-8 в пайпы и VT-режим консоли, чтобы цвета и очистка экрана работали."""
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    k = ctypes.windll.kernel32
    handle, mode = k.GetStdHandle(-11), ctypes.c_uint()
    if k.GetConsoleMode(handle, ctypes.byref(mode)):
        k.SetConsoleMode(handle, mode.value | 4)


# --- шаги

def deps(s):
    if sys.version_info < (3, 8):   # noqa: UP036 — install.py запускают и старым python
        s.fail("нужен python3 3.8 или новее")
    if not shutil.which("git"):
        s.fail("git не найден")
    out = subprocess.run(["git", "--version"], capture_output=True, encoding="utf-8",
                         errors="replace", check=False).stdout.split()
    s.ok("git " + (out[2] if len(out) > 2 else "?"))
    s.ok("python3 " + platform.python_version())


def installed(path):
    return (path / "study").is_file() and (path / "src" / "study").is_dir()


def choose(s):
    """Куда ставить: вопрос с проверками пути; без терминала — путь по умолчанию."""
    s.note("инструмент ставится в <учебная директория>/.digest; рядом появятся папки курсов")
    default = pathlib.Path.home() / "study" / ".digest"
    target = default
    while sys.stdin.isatty():
        raw = s.ask(f"куда установить [{default}]:").strip()
        target = pathlib.Path(raw).expanduser() if raw else default
        if not target.is_absolute():
            target = pathlib.Path.cwd() / target   # относительный — от текущего каталога
        if "=" in str(target):
            s.warn("в пути нельзя '=' (ломает libvirt/virtiofsd)")
            continue
        if len(str(target)) > 100:
            s.warn("слишком длинный путь (лимит unix-сокетов Packer ~108 байт)")
            continue
        if installed(target):
            break   # уже установлено — берём как есть
        if target.exists() and any(target.iterdir()):
            s.warn(f"{target} существует и не пуст — выбери другой")
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            s.warn(f"нет доступа к {target.parent}")
            continue
        break
    return target


def code(s):
    """Каталог .digest: уже на месте, уже установлен или клонируется."""
    if installed(HERE):
        s.ok(f"уже на месте: {HERE}")
        return HERE
    target = choose(s)
    if installed(target):
        s.ok(f"уже установлено: {target}")
        if not (target / "src" / "study" / "setup.py").exists():
            s.fail("установлен старый study без команды setup: сначала study update")
        return target
    r = subprocess.run(["git", "clone", "--quiet", REPO, str(target)], check=False)
    if r.returncode:
        s.fail(f"git clone не удался: {REPO}")
    s.ok(f"склонировано в {target}")
    return target


def config(s, digest):
    cfg = digest / "config.env"
    if not cfg.exists():
        shutil.copy(digest / "config.env.example", cfg)
        s.ok("создан config.env из примера"
             + (" (права 600: в нём хранятся токены)" if POSIX else ""))
    if POSIX:
        cfg.chmod(0o600)
    s.ok(f"учебная директория: {digest.parent}")


def handoff(digest, log):
    """Остальные шаги — у `study setup`; сделанные шаги и их результаты — в STUDY_SETUP."""
    env = {**os.environ, "STUDY_SETUP": json.dumps({"steps": STEPS[:2], "log": log})}
    sys.stdout.flush()   # в пайпе наш буфер иначе выйдет после вывода подпроцесса
    return subprocess.call([sys.executable, str(digest / "study"), "setup"], env=env)


def main():
    if sys.platform == "win32":
        windows_console()
    s = Screen(STEPS)
    s.step(1)
    deps(s)
    s.step(2)
    digest = code(s)
    config(s, digest)
    return handoff(digest, s.log)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
