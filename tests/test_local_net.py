import unittest

from study import local, net
from study.cli import kv
from study.config import Config
from study.hosting import GitVerse, SourceCraft


class LocalTest(unittest.TestCase):
    def test_repo_slug(self):
        for url in ("ssh://git@gitverse.ru:2222/owner/repo.git", "ssh://ssh.sourcecraft.dev/owner/repo.git",
                    "git@github.com:owner/repo.git", "https://github.com/owner/repo", ""):
            self.assertEqual(local.repo_slug(url), "owner/repo" if url else "")

    def test_work_id(self):
        self.assertEqual(local.work_id("1"), ("lab", "01"))
        self.assertEqual(local.work_id("lab02"), ("lab", "02"))
        self.assertEqual(local.work_id(" HW3 "), ("hw", "03"))

    def test_video_keys(self):
        self.assertEqual(local.VIDEO_KEYS[:2], ["RUTUBE_PLAYLIST", "RUTUBE_LAB"])
        self.assertEqual(len(local.VIDEO_KEYS), 10)


class NetTest(unittest.TestCase):
    def test_multipart(self):
        body, ctype = net.multipart({"a": "1"},
                                    [("f", "x.bin", b"\x00\xff", "application/octet-stream")])
        boundary = ctype.split("boundary=")[1]
        self.assertIn(f'--{boundary}\r\nContent-Disposition: form-data; name="a"\r\n\r\n1\r\n'
                      .encode(), body)
        self.assertIn(b'filename="x.bin"\r\nContent-Type: application/octet-stream'
                      b'\r\n\r\n\x00\xff\r\n', body)
        self.assertTrue(body.endswith(f"--{boundary}--\r\n".encode()))


class CliTest(unittest.TestCase):
    def test_kv(self):
        self.assertEqual(kv(["a=1", "b=x=y", "a=2", "a=3"]), {"a": ["1", "2", "3"], "b": "x=y"})


class HostingTest(unittest.TestCase):
    def test_urls(self):
        cfg = Config("/nonexistent/config.env")
        gv, sc = GitVerse(cfg, repo="o/r"), SourceCraft(cfg, repo="org/r")
        self.assertEqual(gv.web_url("v1"), "https://gitverse.ru/o/r/releases/tag/v1")
        self.assertEqual(sc.web_url("v1"), "https://sourcecraft.dev/org/r/releases/v1")
        self.assertEqual(sc.localize("see https://gitverse.ru/o/r/commit/abc"),
                         "see https://gitverse.ru/o/r/commit/abc")   # без path — нечего подменять


if __name__ == "__main__":
    unittest.main()
