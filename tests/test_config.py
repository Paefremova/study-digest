import os
import pathlib
import tempfile
import unittest

from study.config import Config, StudyError


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp())
        self.path = self.dir / "config.env"

    def write(self, text):
        self.path.write_text(text)
        return Config(self.path)

    def test_read(self):
        cfg = self.write("# коммент\nTUIS_URL = https://x/ \nCOURSE_IGNORE=1 2\n"
                         "  CODE 5 five\nCODEX=1\nCOURSE 7 seven Старое\nCODE 9\n")
        self.assertEqual(cfg.get("TUIS_URL"), "https://x/")
        self.assertEqual(cfg.ignore(), {1, 2})
        self.assertEqual(cfg.codes(), {5: "five"})   # CODEX — обычный ключ, CODE 9 — неполная
        self.assertEqual(cfg.get("CODEX"), "1")
        self.assertEqual(cfg.get("COURSE_IGNORE"), "")   # не переменная, а список

    def test_defaults_and_env(self):
        cfg = self.write("")
        self.assertEqual(cfg.get("DIGEST_DAYS"), "21")
        self.assertEqual(cfg.days(), 21)
        self.assertEqual(cfg.get("NOPE"), "")
        os.environ["DIGEST_DAYS"] = "7"
        try:
            self.assertEqual(cfg.days(), 7)
        finally:
            del os.environ["DIGEST_DAYS"]

    def test_token_value_file_missing(self):
        cfg = self.write("GITVERSE_TOKEN=abc\n")
        self.assertEqual(cfg.token("GITVERSE_TOKEN"), "abc")
        (self.dir / "t").write_text("fromfile\n")
        cfg = self.write(f"RUTUBE_TOKEN_FILE={self.dir / 't'}\n")
        self.assertEqual(cfg.token("RUTUBE_TOKEN"), "fromfile")
        with self.assertRaises(StudyError) as e:
            self.write("").token("TUIS_TOKEN")
        self.assertEqual(e.exception.code, "notoken")
        self.assertTrue(e.exception.hint())

    def test_track(self):
        cfg = self.write("COURSE_IGNORE=2\nCODE 1 one\n")
        got = cfg.track([{"id": 1, "fullname": "Один"}, {"id": 2, "fullname": "Два"}, {"id": 3}])
        self.assertEqual([(c.id, c.code, c.title) for c in got],
                         [(1, "one", "Один"), (3, None, "")])

    def test_write_courses(self):
        cfg = self.write("A=1\nCOURSE_IGNORE=1\nCODE 5 five\nCOURSE 7 x Старое\n# CODE 8 keep\n")
        cfg.write_courses({3}, {5: "five", 8: "eight"})
        self.assertEqual(self.path.read_text(),
                         "A=1\n# CODE 8 keep\nCOURSE_IGNORE=3\nCODE 5 five\nCODE 8 eight\n")
        self.assertEqual(oct(self.path.stat().st_mode)[-3:], "600")
        self.assertEqual(Config(self.path).codes(), {5: "five", 8: "eight"})


if __name__ == "__main__":
    unittest.main()
