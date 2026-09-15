import unittest
from unittest import mock

from study import agent, update
from study.config import StudyError
from tests.fakes import git, patch, tmpdir


class UpdateTest(unittest.TestCase):
    """Клон `clone` с upstream в bare `origin.git`; свежие коммиты приходят из клона `other`."""

    def setUp(self):
        self.tmp = tmpdir(self)
        self.origin = self.tmp / "origin.git"
        self.origin.mkdir()
        git(self.origin, "init", "-q", "--bare")
        self.clone = self.tmp / "clone"
        self.clone.mkdir()
        git(self.clone, "init", "-q")
        git(self.clone, "remote", "add", "origin", str(self.origin))
        git(self.clone, "commit", "-q", "--allow-empty", "-m", "feat: первый")
        git(self.clone, "tag", "-a", "v1.0.0", "-m", "v1.0.0")
        git(self.clone, "push", "-q", "-u", "origin", "master", "--tags")
        root = self.tmp / "study"
        root.mkdir()
        src = self.tmp / "AGENTS.md"
        src.write_text("# Инструкция\n\nv1\n", encoding="utf-8")
        for mod, attr, value in ((update, "HERE", self.clone), (agent, "ROOT", root),
                                 (agent, "SOURCE", src)):
            patch(self, mod, attr, value)
        self.root, self.src = root, src

    def upstream(self, *messages):
        other = self.tmp / "other"
        git(self.tmp, "clone", "-q", str(self.origin), str(other))
        for msg in messages:
            git(other, "commit", "-q", "--allow-empty", "-m", msg)
        git(other, "push", "-q", "origin", "master")

    def test_up_to_date(self):
        d = update.check()
        self.assertEqual(d, {"version": "v1.0.0", "remote": "v1.0.0", "behind": 0, "ahead": 0,
                             "commits": [], "agents": []})
        self.assertEqual(update.note(d), [])
        self.assertEqual(update.apply()["updated"], False)

    def test_behind_and_apply(self):
        agent.install("claude")
        self.upstream("feat: новое", "fix: правка")
        # как если бы pull обновил docs/AGENTS.md
        self.src.write_text("# Инструкция\n\nv2\n", encoding="utf-8")
        d = update.check()
        self.assertEqual((d["behind"], d["ahead"], d["commits"]),
                         (2, 0, ["fix: правка", "feat: новое"]))   # новые первыми
        self.assertEqual(d["version"], "v1.0.0")
        self.assertTrue(d["remote"].startswith("v1.0.0-2-g"))
        self.assertEqual([(a["operator"], a["current"]) for a in d["agents"]], [("claude", False)])
        self.assertIn("Обновление study: v1.0.0 → v1.0.0-2-g", update.note(d)[0])
        out = update.apply()
        self.assertEqual((out["updated"], out["now"], out["refreshed"]),
                         (True, d["remote"], ["CLAUDE.md"]))
        self.assertIn("v2", (self.root / "CLAUDE.md").read_text(encoding="utf-8"))
        self.assertEqual(update.check()["behind"], 0)

    def test_check_without_fetch_and_ahead(self):
        self.upstream("feat: новое")
        self.assertEqual(update.check(fetch=False)["behind"], 0)   # без fetch origin не виден
        git(self.clone, "commit", "-q", "--allow-empty", "-m", "wip")
        d = update.check()
        self.assertEqual((d["behind"], d["ahead"]), (1, 1))
        self.assertEqual(update.version(),
                         "v1.0.0-1-g" + git(self.clone, "rev-parse", "--short", "HEAD"))

    def test_stale_agent_only(self):
        agent.install("codex")
        self.src.write_text("# Инструкция\n\nv2\n", encoding="utf-8")
        out = update.apply()
        self.assertEqual((out["updated"], out["refreshed"]), (False, ["AGENTS.md"]))
        self.assertTrue(agent.status("codex")["current"])

    def test_not_a_clone_and_fetch_failure(self):
        with mock.patch.object(update, "HERE", self.tmp / "plain"):
            self.assertIsNone(update.check())
            with self.assertRaises(StudyError):
                update.apply()
        git(self.clone, "remote", "set-url", "origin", str(self.tmp / "nowhere.git"))
        with self.assertRaises(StudyError) as e:
            update.check()
        self.assertIn("fetch не удался", e.exception.message)
        git(self.clone, "branch", "--unset-upstream")   # клон без ветки слежения — не обновляем
        self.assertIsNone(update.check())
