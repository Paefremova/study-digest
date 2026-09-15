"""Кодировка файлов задана явно везде: на Windows системная — cp1251, а у нас кириллица."""
import pathlib
import re
import tokenize
import unittest

HERE = pathlib.Path(__file__).resolve().parents[1]
CALL = re.compile(r"(\.read_text|\.write_text|(?<![\w.])open)\($")


def calls(path):
    """(строка, аргументы) каждого вызова read_text/write_text/open — по токенам, без строк."""
    with tokenize.open(path) as f:
        tokens = list(tokenize.generate_tokens(f.readline))
    depth, start, args, out = 0, None, [], []
    for i, tok in enumerate(tokens):
        if start is None:
            if tok.string == "(" and CALL.search("".join(t.string for t in tokens[i - 2:i + 1])):
                start, depth, args = tok.start[0], 1, []
            continue
        depth += {"(": 1, ")": -1}.get(tok.string, 0)
        if depth == 0:
            out.append((start, args))
            start = None
        elif tok.type == tokenize.NAME:
            args.append(tok.string)
    return out


class EncodingTest(unittest.TestCase):
    def test_every_text_io_names_encoding(self):
        files = [*HERE.glob("src/study/*.py"), *HERE.glob("tests/*.py"), HERE / "study",
                 HERE / "install.py"]
        bad = [f"{f.relative_to(HERE)}:{line}" for f in files if f.exists()
               for line, args in calls(f) if "encoding" not in args]
        self.assertEqual(bad, [])
