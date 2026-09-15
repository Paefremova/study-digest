"""Общее для тестов: подмена сети, фикстуры, временные config.env и git-репозитории.

Вся сеть инструмента идёт через `net.send` (`net.request` и `net.raw` — обёртки над ним),
поэтому подменяется только он. Ответы кладутся в очередь по (метод, подстроки): подстрока
ищется в «МЕТОД url», либо целиком равна полю формы `k=v` или его значению — у Moodle это
wsfunction и параметры, и `courseid=1` не совпадает с `courseid=12`.
Каждый ответ отдаётся один раз; вызов без ответа и ответ без вызова — AssertionError.
"""
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import urllib.parse
from unittest import mock

from study import net
from study.config import Config

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
NOW = 1789538400    # ср 16.09.2026 09:00 MSK — от него отсчитаны все времена в fixtures/
DAY = 86400
# Что Config читает из окружения раньше config.env — в тестах этого не должно быть; git —
# без глобального и системного конфига, чтобы status/describe не зависели от машины.
ENV = re.compile(r"^(TUIS_|GITVERSE_|SOURCECRAFT_|DIGEST_|RUTUBE_|GV_REPO$|SC_REPO$)")


def fixture(name):
    """tests/fixtures/<name>.json как объект; имя с расширением — как текст."""
    p = FIXTURES / name
    return p.read_text() if p.suffix else json.loads(p.with_suffix(".json").read_text())


def patch(case, obj, attr, value):
    """Подмена атрибута до конца теста."""
    p = mock.patch.object(obj, attr, value)
    p.start()
    case.addCleanup(p.stop)


def passed(case):
    """Не упал ли тест к моменту cleanup: частный API unittest, две его формы (3.8–3.10 и 3.11+)."""
    o = getattr(case, "_outcome", None)
    if o is None:
        return True
    if hasattr(o, "errors"):
        return not any(exc for _, exc in o.errors)
    return not any(t is case for t, _ in o.result.failures + o.result.errors)


def tmpdir(case):
    """Временный каталог и чистое окружение: без настроек study и без чужого gitconfig."""
    clean = {k: v for k, v in os.environ.items() if not ENV.match(k)}
    clean.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"})
    p = mock.patch.dict(os.environ, clean, clear=True)
    p.start()
    case.addCleanup(p.stop)
    d = pathlib.Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, d, True)
    return d


def config(tmp, extra=""):
    """config.env во временном каталоге: тестовые адреса и токены, снимок и секреты — там же."""
    p = tmp / "config.env"
    p.write_text("TUIS_URL=https://tuis.example\nTUIS_TOKEN=test-token\n"
                 "GITVERSE_TOKEN=gv-token\nSOURCECRAFT_TOKEN=sc-token\n"
                 f"DIGEST_STATE={tmp / '.state.json'}\n"
                 f"RUTUBE_TOKEN_FILE={tmp / 'rt-token'}\n"
                 f"RUTUBE_REFRESH_FILE={tmp / 'rt-refresh'}\n"
                 f"RUTUBE_ACCESS_FILE={tmp / 'rt-access'}\n" + extra)
    return Config(p)


# --- git

GIT = ["git", "-c", "user.name=study", "-c", "user.email=study@example.org",
       "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", "-c", "init.defaultBranch=master"]


def git(path, *args):
    """git в каталоге path с тестовой личностью и без подписи; stdout."""
    return subprocess.run([*GIT, "-C", str(path), *args], capture_output=True, text=True,
                          check=True).stdout.strip()


def repo(path, remotes=None, tag=None):
    """Репозиторий с одним коммитом, тегом и remote'ами."""
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q")
    for name, url in (remotes or {}).items():
        git(path, "remote", "add", name, url)
    git(path, "commit", "-q", "--allow-empty", "-m", "chore: init")
    if tag:
        git(path, "tag", "-a", tag, "-m", tag)
    return path


# --- сеть

def decode(headers, data):
    """Тело запроса обратно в то, из чего его собрал net.request."""
    out = {"form": None, "json_body": None, "fields": None, "files": None}
    ctype = (headers or {}).get("Content-Type", "")
    if data is None:
        return out
    if ctype.startswith("application/x-www-form-urlencoded"):
        out["form"] = dict(urllib.parse.parse_qsl(data.decode(), keep_blank_values=True))
    elif ctype.startswith("application/json"):
        out["json_body"] = json.loads(data)
    elif ctype.startswith("multipart/form-data"):
        out["fields"], out["files"] = {}, {}
        boundary = ("--" + ctype.split("boundary=")[1]).encode()
        for part in data.split(boundary)[1:-1]:
            head, _, body = part[2:].partition(b"\r\n\r\n")   # часть начинается с \r\n
            head, body = head.decode(), body[:-2]             # и кончается \r\n перед границей
            name = re.search(r'(?<!file)name="([^"]*)"', head).group(1)
            fname = re.search(r'filename="([^"]*)"', head)
            if fname:
                ct = re.search(r"Content-Type: (.*)", head).group(1)
                out["files"][name] = (fname.group(1), body, ct)
            else:
                out["fields"][name] = body.decode()
    return out


def encode(body):
    if body is None:
        return b""
    return body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()


class FakeNet:
    def __init__(self):
        self.queue = []   # (метод, подстроки ключа, тело, заголовки ответа)
        self.sent = []    # записи отправленного: method, url, headers, data, form, json_body, …

    def install(self, case):
        patch(case, net, "send", self.send)
        # очередь проверяется только у прошедшего теста: упавший и так отчитался
        case.addCleanup(lambda: passed(case) and self.done())
        return self

    def reply(self, method, what, body=None, headers=None):
        """Ответ на первый запрос, которому подходят все подстроки `what`.
        Тело: dict/list → JSON, bytes → как есть, None → пусто, исключение → поднимается."""
        what = (what,) if isinstance(what, str) else tuple(what)
        self.queue.append((method.upper(), what, body, headers or {}))

    def drop(self, *what):
        """Убрать из очереди ответы, среди подстрок которых есть все `what`."""
        self.queue = [q for q in self.queue if not set(what) <= set(q[1])]

    def done(self):
        left = [(m, " ".join(w)) for m, w, _, _ in self.queue]
        if left:
            raise AssertionError(f"ответы остались невостребованными: {left}")

    def calls(self, fn):
        """Формы запросов к ручке Moodle `fn` в порядке отправки."""
        return [r["form"] for r in self.sent if r["form"] and r["form"].get("wsfunction") == fn]

    def send(self, url, source, *, method=None, headers=None, data=None, timeout=600,
             where=None):
        method = method or ("POST" if data is not None else "GET")
        rec = {"method": method, "url": url, "source": source, "where": where,
               "timeout": timeout, "headers": dict(headers or {}), "data": data,
               **decode(headers, data)}
        self.sent.append(rec)
        head = f"{method} {url}"
        pairs = [f"{k}={v}" for k, v in (rec["form"] or {}).items()]
        tokens = set(pairs) | set((rec["form"] or {}).values())
        for i, (m, what, body, hd) in enumerate(self.queue):
            if m == method and all(w in head or w in tokens for w in what):
                del self.queue[i]
                if isinstance(body, Exception):
                    raise body
                return 200, hd, encode(body)
        raise AssertionError(f"нет ответа для {head} {' '.join(pairs)}")
