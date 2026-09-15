"""ИИ-оператор: файл инструкций для агента в корне учебной директории.

Текст один — `docs/AGENTS.md`; у операторов различаются только имена файлов. Он вписывается
блоком между маркерами, и `study agent` переписывает только этот блок: всё, что вне его, —
личные правила пользователя, они не трогаются. Симлинк не подходит: задача Claude Code Desktop
читает файл из Windows через `\\\\wsl.localhost`, а симлинки WSL оттуда не открываются.
"""
from .config import HERE, ROOT
from .fmt import table

SOURCE = HERE / "docs" / "AGENTS.md"
OPERATORS = {   # код → (кто читает, файл в корне учебной директории)
    "claude": ("Claude Code", "CLAUDE.md"),
    "codex": ("OpenAI Codex; тот же AGENTS.md читают Cursor, Copilot coding agent, Jules, Zed",
              "AGENTS.md"),
    "gemini": ("Gemini CLI", "GEMINI.md"),
    "copilot": ("GitHub Copilot в редакторе", ".github/copilot-instructions.md"),
}
BEGIN = "<!-- study:begin — блок обновляет `study agent`; править .digest/docs/AGENTS.md -->"
END = "<!-- study:end -->"


def block():
    """Текст docs/AGENTS.md между маркерами — то, что вписывается в файл оператора."""
    text = SOURCE.read_text(encoding="utf-8").strip()
    return f"{BEGIN}\n{text}\n{END}\n"


def status(operator):
    """Есть ли файл оператора, стоит ли в нём блок и совпадает ли он с docs/AGENTS.md."""
    name, rel = OPERATORS[operator]
    path = ROOT / rel
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    installed = BEGIN in text and END in text
    return {"operator": operator, "name": name, "path": str(path), "file": rel,
            "exists": path.exists(), "installed": installed,
            "current": installed and block() in text}


def install(operator):
    """Создать файл оператора или обновить в нём блок; остальной текст файла сохраняется."""
    path = ROOT / OPERATORS[operator][1]
    new = block()
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    i, j = text.find(BEGIN), text.find(END)
    if 0 <= i < j:
        rest = text[j + len(END):]
        text = text[:i] + new + (rest[1:] if rest.startswith("\n") else rest)
    elif text:
        text = text.rstrip("\n") + "\n\n" + new
    else:
        text = new
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return status(operator)


def render(rows, installed=False):
    """Таблица операторов; `installed` — строки после `study agent <код>`, иначе подсказка."""
    state = {True: "актуален", False: "устарел", None: "нет"}
    lines = [table([[r["operator"], r["file"], state[r["current"] if r["installed"] else None],
                     r["name"]] for r in rows], ["код", "файл", "блок", "кто читает"])]
    if installed:
        lines += ["", "Записано: " + ", ".join(r["path"] for r in rows),
                  "Личные правила — в том же файле вне блока study:begin…study:end."]
    else:
        lines += ["", "Поставить: study agent <код> [<код>…]  (блок в корне учебной директории)"]
    return "\n".join(lines)
