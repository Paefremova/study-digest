import unittest
from unittest import mock

from study import answer, files, local
from study.config import Course, StudyError
from study.moodle import Moodle
from tests.fakes import FakeNet, config, fixture, git, repo, tmpdir

NOW = 1789538400    # как в фикстурах: 002-dns.pdf, video.mp4, big.zip изменены 2 часа назад
SINCE = NOW - 86400


class FilesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tmpdir(self)
        self.net = FakeNet().install(self)
        p = mock.patch.object(files, "ROOT", self.tmp)
        p.start()
        self.addCleanup(p.stop)
        self.cfg = config(self.tmp, "CODE 1 nettech\n")
        self.m = Moodle(self.cfg)
        self.course = Course(1, "nettech", "Сетевые технологии")
        self.stash = self.tmp / "nettech" / "stash"
        self.net.reply("POST", ("core_course_get_contents", "courseid=1"),
                       fixture("course_contents"))

    def test_listing_filters(self):
        (self.stash / "old").mkdir(parents=True)
        (self.stash / "old" / "lecture-01.pptx").write_bytes(b"x")   # уже есть, в подкаталоге
        d = files.listing(self.cfg, self.m, self.course, since=SINCE, everything=True)
        self.assertEqual((d["course"]["code"], d["stash"], d["since"]["ts"]),
                         ("nettech", str(self.stash), SINCE))
        rows = {f["name"]: f for f in d["files"]}
        self.assertEqual([f["name"] for f in d["files"]][:3],
                         ["002-dns.pdf", "video.mp4", "big.zip"])   # свежие первыми
        self.assertEqual({n: (f["skip"], f["have"], f["new"]) for n, f in rows.items()},
                         {"002-dns.pdf": (None, False, True),
                          "video.mp4": ("тип .mp4", False, True),
                          "big.zip": ("размер 60 МБ", False, True),
                          "lecture-01.pptx": (None, True, False),
                          "index.html": ("страница курса", False, False)})
        self.assertEqual(rows["lecture-01.pptx"]["path"],
                         str(self.stash / "old" / "lecture-01.pptx"))
        self.assertEqual(rows["002-dns.pdf"]["path"], str(self.stash / "002-dns.pdf"))
        self.assertEqual((rows["002-dns.pdf"]["section"], rows["002-dns.pdf"]["module"]),
                         ("Лабораторные работы", "Методичка 2"))
        self.assertNotIn("Ссылка", rows)   # type=url — не файл
        new = files.listing(self.cfg, self.m, self.course, since=SINCE)
        self.assertEqual([f["name"] for f in new["files"]], ["002-dns.pdf", "video.mp4", "big.zip"])
        self.assertIn("Забрать: study files nettech --pull", files.render(new))

    def test_listing_since_from_state(self):
        d = files.listing(self.cfg, self.m, self.course)   # снимка нет — новым считается всё
        self.assertIsNone(d["since"])
        self.assertEqual(len(d["files"]), 5)
        self.assertIn("после начала времён", files.render(d))
        with self.assertRaises(StudyError):
            files.stash(Course(2))

    def test_pull(self):
        self.stash.mkdir(parents=True)
        (self.stash / "lecture-01.pptx").write_bytes(b"old")
        d = files.listing(self.cfg, self.m, self.course, since=SINCE, everything=True)
        self.net.reply("GET", "002-dns.pdf?forcedownload=1&token=test-token", b"%PDF-2")
        out = files.pull(self.m, d)
        self.assertEqual(out["pulled"], [{"name": "002-dns.pdf", "bytes": 6,
                                          "path": str(self.stash / "002-dns.pdf")}])
        self.assertEqual((self.stash / "002-dns.pdf").read_bytes(), b"%PDF-2")
        self.assertEqual((self.stash / "lecture-01.pptx").read_bytes(), b"old")   # без force
        self.assertEqual(out["errors"], [])
        text = files.render(out, pulled=True)
        self.assertIn("скачан       002-dns.pdf", text)
        self.assertIn("уже есть     lecture-01.pptx", text)
        self.assertIn("пропущен: тип .mp4 video.mp4", text)
        self.assertIn("Скачано: 1 файл(ов)", text)

    def test_pull_force_and_error(self):
        self.stash.mkdir(parents=True)
        (self.stash / "lecture-01.pptx").write_bytes(b"old")
        d = files.listing(self.cfg, self.m, self.course, since=SINCE, everything=True)
        self.net.reply("GET", "002-dns.pdf", StudyError("moodle", "HTTP 404", where="pluginfile"))
        self.net.reply("GET", "lecture-01.pptx?token=test-token", b"new")
        out = files.pull(self.m, d, force=True)
        self.assertEqual([g["name"] for g in out["pulled"]], ["lecture-01.pptx"])
        self.assertEqual((self.stash / "lecture-01.pptx").read_bytes(), b"new")
        self.assertEqual([(e["where"], e["message"]) for e in out["errors"]],
                         [("002-dns.pdf", "HTTP 404")])
        self.assertIn("не удалось: 002-dns.pdf — HTTP 404", files.render(out, pulled=True))


class SafeNameTest(unittest.TestCase):
    def test_safe(self):
        self.assertEqual(files.safe("../a/b\\c.pdf"), ".._a_b_c.pdf")
        self.assertEqual(files.safe(None), "file")
        self.assertEqual(len(files.safe("x" * 300)), 200)


class AnswerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tmpdir(self)
        for mod in (local, answer):
            p = mock.patch.object(mod, "ROOT", self.tmp)
            p.start()
            self.addCleanup(p.stop)
        self.cfg = config(self.tmp, "GV_REPO=cfg/gv\nSC_REPO=cfg/sc\n")
        self.repo = repo(self.tmp / "nettech" / "2026-study-nettech",
                         {"origin": "ssh://git@gitverse.ru:2222/me/nettech.git",
                          "src": "ssh://ssh.sourcecraft.dev/org/nettech.git"}, tag="v1.1.0")
        for d in ("labs/lab01/report/_output", "labs/lab01/presentation/_output", "homework/hw01"):
            (self.repo / d).mkdir(parents=True)
        (self.repo / "labs/lab01/report/_output/nettech-lab01-report.pdf").write_bytes(b"%PDF")
        (self.repo / "labs/lab01/presentation/_output/slides.html").write_text("")
        self.env = self.tmp / "nettech" / "tuis" / "lab01.env"

    def test_first_run_creates_env(self):
        d = answer.build(self.cfg, "nettech", "1")
        self.assertEqual(d, {"created": str(self.env), "text": None, "attachments": [],
                             "missing": local.VIDEO_KEYS})
        self.assertTrue(self.env.read_text().startswith(answer.TEMPLATE))
        self.assertEqual(self.env.read_text().count("=\n"), 10)
        self.assertIn("заполни ссылки", answer.render(d))
        self.assertFalse((self.tmp / "nettech/tuis/lab01.md").exists())

    def test_text_and_attachments(self):
        self.env.parent.mkdir(parents=True)
        self.env.write_text("RUTUBE_PLAYLIST=https://rutube.ru/plst/1/\n"
                            "RUTUBE_LAB=https://rutube.ru/video/a/\n"
                            "VK_DEFENSE=https://vk.com/video-1_4\n")
        d = answer.build(self.cfg, "nettech", "lab01")
        self.assertEqual(d["text"],
                         "- Скринкасты, Rutube: [плейлист](https://rutube.ru/plst/1/)\n"
                         "  - [Выполнение лабораторной работы](https://rutube.ru/video/a/)\n"
                         "- Скринкасты, VKvideo:\n"
                         "  - [Защита лабораторной работы](https://vk.com/video-1_4)\n"
                         "- Репозиторий и релиз:\n"
                         "  - [gitverse](https://gitverse.ru/me/nettech), "
                         "[релиз v1.1.0](https://gitverse.ru/me/nettech/releases/tag/v1.1.0)\n"
                         "  - [sourcecraft](https://sourcecraft.dev/org/nettech), "
                         "[релиз v1.1.0](https://sourcecraft.dev/org/nettech/releases/v1.1.0)\n")
        self.assertEqual((d["tag"], d["created"], d["path"]),
                         ("v1.1.0", None, str(self.env.with_suffix(".md"))))
        self.assertEqual(d["attachments"],
                         [str(self.repo / "labs/lab01/report/_output/nettech-lab01-report.pdf")])
        self.assertEqual(len(d["missing"]), 7)
        self.assertEqual(self.env.with_suffix(".md").read_text(), d["text"])
        text = answer.render(d)
        self.assertIn("Прикрепить к ответу:\n  " + d["attachments"][0], text)
        self.assertIn("Не заполнено в lab01.env: RUTUBE_REPORT, ", text)

    def test_tag_and_remote_only(self):
        self.env.parent.mkdir(parents=True)
        self.env.write_text("")
        git(self.repo, "remote", "remove", "src")   # SC_REPO из config.env не подставляется
        d = answer.build(self.cfg, "nettech", "1", tag="v1.0.0")
        self.assertEqual(d["text"], "- Репозиторий и релиз:\n"
                                    "  - [gitverse](https://gitverse.ru/me/nettech), "
                                    "[релиз v1.0.0](https://gitverse.ru/me/nettech/releases/tag/v1.0.0)\n")
        self.assertIn("PDF не собраны", answer.render(
            {**d, "attachments": []}))

    def test_homework_and_errors(self):
        d = answer.build(self.cfg, "nettech", "hw1")
        self.assertEqual(d["created"], str(self.tmp / "nettech/tuis/hw01.env"))
        with self.assertRaises(StudyError) as e:
            answer.build(self.cfg, "nettech", "2")
        self.assertIn("labs/lab02", e.exception.message)
        with self.assertRaises(StudyError) as e:
            answer.build(self.cfg, "nope", "1")
        self.assertIn("не найден репозиторий", e.exception.message)


if __name__ == "__main__":
    unittest.main()
