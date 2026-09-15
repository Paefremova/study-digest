"""ИИ-оператор: файл инструкций для агента в корне учебной директории.

Текст один — `docs/AGENTS.md`; у операторов различаются только имена файлов. Он вписывается
блоком между маркерами, и `study agent` переписывает только этот блок: всё, что вне его, —
личные правила пользователя, они не трогаются. Симлинк не подходит: задача Claude Code Desktop
читает файл из Windows через `\\\\wsl.localhost`, а симлинки WSL оттуда не открываются.
"""
from .config import HERE, ROOT

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
    return f"{BEGIN}\n{SOURCE.read_text().strip()}\n{END}\n"


def status(operator):
    name, rel = OPERATORS[operator]
    path = ROOT / rel
    text = path.read_text() if path.exists() else ""
    installed = BEGIN in text and END in text
    return {"operator": operator, "name": name, "path": str(path), "file": rel,
            "exists": path.exists(), "installed": installed, "current": installed and block() in text}


def install(operator):
    """Создать файл оператора или обновить в нём блок; остальной текст файла сохраняется."""
    path = ROOT / OPERATORS[operator][1]
    new = block()
    text = path.read_text() if path.exists() else ""
    i, j = text.find(BEGIN), text.find(END)
    if 0 <= i < j:
        rest = text[j + len(END):]
        text = text[:i] + new + (rest[1:] if rest.startswith("\n") else rest)
    elif text:
        text = text.rstrip("\n") + "\n\n" + new
    else:
        text = new
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return status(operator)
