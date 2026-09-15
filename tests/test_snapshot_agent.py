import json
import pathlib
import tempfile
import time
import unittest

from study import agent, snapshot, update
from study.config import Config, StudyError


class SnapshotTest(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp())
        (self.dir / "config.env").write_text(f"DIGEST_STATE={self.dir / '.state.json'}\n")
        self.cfg = Config(self.dir / "config.env")

    def test_load_variants(self):
        self.assertEqual(snapshot.load_state(self.cfg), {})
        snapshot.save_state(self.cfg, {"last_run": 1_700_000_000, "grades": {"1": {}}})
        self.assertEqual(snapshot.load_state(self.cfg)["last_run"], 1_700_000_000)
        self.assertEqual(snapshot.load_state(self.cfg, "never"), {})
        self.assertEqual(snapshot.load_state(self.cfg, "all")["last_run"], 1)
        # число дней и дата: ближайший снимок не позже точки; раньше всех снимков —
        # текущее состояние с этой точкой как last_run
        self.assertEqual(snapshot.load_state(self.cfg, "3")["last_run"], 1_700_000_000)
        self.assertEqual(snapshot.load_state(self.cfg, "2023-11-15")["last_run"], 1_700_000_000)
        early = snapshot.load_state(self.cfg, "2020-01-01")
        self.assertEqual(time.strftime("%Y-%m-%d", time.localtime(early["last_run"])), "2020-01-01")
        self.assertEqual(early["grades"], {"1": {}})
        with self.assertRaises(StudyError):
            snapshot.load_state(self.cfg, "вчера")

    def test_history_pruned(self):
        old = 1_700_000_000
        snapshot.save_state(self.cfg, {"last_run": old})
        snapshot.save_state(self.cfg, {"last_run": old + (snapshot.KEEP_DAYS + 1) * snapshot.DAY})
        days = sorted(p.stem for p in snapshot.history_dir(self.cfg).glob("*.json"))
        self.assertEqual(len(days), 1)
        kept = json.loads((snapshot.history_dir(self.cfg) / f"{days[0]}.json").read_text())
        self.assertEqual(kept["last_run"], old + (snapshot.KEEP_DAYS + 1) * snapshot.DAY)


class AgentTest(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp())
        self.src = self.root / "AGENTS.src.md"
        self.src.write_text("# Инструкция\n\nтекст v1\n")
        self._saved = agent.ROOT, agent.SOURCE
        agent.ROOT, agent.SOURCE = self.root, self.src

    def tearDown(self):
        agent.ROOT, agent.SOURCE = self._saved

    def test_new_append_stale_idempotent(self):
        r = agent.install("copilot")
        self.assertTrue(r["current"] and (self.root / ".github/copilot-instructions.md").exists())

        (self.root / "CLAUDE.md").write_text("# Моё\n\n- правило 1\n")
        agent.install("claude")
        text = (self.root / "CLAUDE.md").read_text()
        self.assertTrue(text.startswith("# Моё\n\n- правило 1\n\n" + agent.BEGIN))
        self.assertTrue(text.endswith(agent.END + "\n"))

        (self.root / "CLAUDE.md").write_text(text + "\n# После\n")
        self.src.write_text("# Инструкция\n\nтекст v2\n")
        self.assertFalse(agent.status("claude")["current"])
        agent.install("claude")
        text = (self.root / "CLAUDE.md").read_text()
        self.assertIn("текст v2", text)
        self.assertNotIn("текст v1", text)
        self.assertEqual(text.count(agent.BEGIN), 1)
        self.assertTrue(text.startswith("# Моё\n\n- правило 1\n\n"))
        self.assertTrue(text.endswith("\n# После\n"))
        agent.install("claude")
        self.assertEqual((self.root / "CLAUDE.md").read_text(), text)


class UpdateNoteTest(unittest.TestCase):
    def test_note(self):
        self.assertEqual(update.note(None), [])
        base = {"version": "v1", "remote": "v2", "commits": ["a", "b", "c", "d"], "agents": []}
        self.assertIn("1 коммит (", update.note({**base, "behind": 1})[0])
        self.assertIn("3 коммита", update.note({**base, "behind": 3})[0])
        self.assertIn("11 коммитов", update.note({**base, "behind": 11})[0])
        five = update.note({**base, "behind": 5})[0]
        self.assertIn("(a; b; c; …)", five)
        agents = [{"file": "CLAUDE.md", "operator": "claude", "current": False}]
        self.assertEqual(update.note({**base, "behind": 0, "agents": agents}),
                         ["Блок агента в CLAUDE.md устарел — `study agent claude`."])


if __name__ == "__main__":
    unittest.main()
