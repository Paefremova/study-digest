"""GitVerse и SourceCraft за одним интерфейсом. Подробности — в ../hosting-api.md."""
import mimetypes
import pathlib

from . import local, net
from .config import StudyError


class Hosting:
    """Общий интерфейс: releases(), release(), asset(), web_url()."""

    name = ""
    source = ""
    token_key = ""
    repo_key = ""
    remote = ""
    host = ""

    def __init__(self, cfg, repo=None, path=None):
        self.cfg = cfg
        self.path = pathlib.Path(path) if path else None
        self.repo = repo or cfg.get(self.repo_key) or (
            local.repo_from_remote(self.path, self.remote, self.host) if self.path else "")
        if not self.repo:
            raise StudyError(self.source,
                             f"репозиторий не задан: {self.repo_key} в config.env "
                             f"или запуск из каталога репозитория (remote {self.remote})")

    def headers(self):
        return {"Authorization": "Bearer " + self.cfg.token(self.token_key)}

    def api(self, path, **kw):
        kw.setdefault("headers", {}).update(self.headers())
        return net.request(self.base + path, self.source, where=path, **kw)

    @staticmethod
    def _file(path):
        p = pathlib.Path(path)
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        return p.name, p.read_bytes(), ctype


class GitVerse(Hosting):
    name = "GitVerse"
    source = "gitverse"
    token_key = "GITVERSE_TOKEN_FILE"
    repo_key = "GV_REPO"
    remote = "origin"
    host = "gitverse.ru"
    base = "https://api.gitverse.ru"

    def headers(self):
        head = super().headers()
        # Без этого заголовка любой запрос отвечает 400 с пустым телом.
        head["Accept"] = "application/vnd.gitverse.object+json;version=1"
        return head

    def releases(self):
        """Ответ GitVerse — голый массив."""
        out = self.api(f"/repos/{self.repo}/releases") or []
        return [{"tag": r["tag_name"], "id": r["id"], "name": r.get("name"),
                 "assets": len(r.get("assets") or []),
                 "url": self.web_url(r["tag_name"])} for r in out]

    def release(self, tag, title, notes, sha=None):
        """target_commitish принимает только полный SHA коммита, имя ветки — 422."""
        if not sha:
            if not self.path:
                raise StudyError(self.source, "нужен --sha или запуск из каталога репозитория")
            sha = local.tag_sha(self.path, tag)
        out = self.api(f"/repos/{self.repo}/releases",
                       json_body={"tag_name": tag, "target_commitish": sha, "name": title,
                                  "body": notes, "draft": False, "prerelease": False})
        return {"id": out["id"], "tag": out["tag_name"], "url": self.web_url(out["tag_name"])}

    def update(self, tag, title=None, notes=None):
        """Правка названия и описания выпущенного релиза, адресуется по id."""
        ids = {r["tag"]: r["id"] for r in self.releases()}
        if tag not in ids:
            raise StudyError(self.source, f"релиза {tag} нет")
        body = {k: v for k, v in (("name", title), ("body", notes)) if v}
        self.api(f"/repos/{self.repo}/releases/{ids[tag]}", method="PATCH", json_body=body)
        return {"id": ids[tag], "tag": tag, "url": self.web_url(tag)}

    def asset(self, release_id, path, name=None):
        """Имя — query-параметром, файл — в поле attachment. .qmd и .html отвергаются."""
        fname, data, ctype = self._file(path)
        name = name or fname
        if name.endswith((".qmd", ".html")):
            raise StudyError(self.source, f"{name}: GitVerse не принимает .qmd и .html — "
                                          "класть .md или zip")
        self.api(f"/repos/{self.repo}/releases/{release_id}/assets?name={name}",
                 files=[("attachment", fname, data, ctype)], timeout=900)
        return {"name": name, "ok": True}

    def web_url(self, tag):
        return f"https://gitverse.ru/{self.repo}/releases/tag/{tag}"


class SourceCraft(Hosting):
    name = "SourceCraft"
    source = "sourcecraft"
    token_key = "SOURCECRAFT_TOKEN_FILE"
    repo_key = "SC_REPO"
    remote = "src"
    host = "sourcecraft"
    base = "https://api.sourcecraft.tech"

    def releases(self):
        """Ответ SourceCraft — объект {"releases": [...]} с другими именами полей."""
        out = self.api(f"/repos/{self.repo}/releases") or {}
        return [{"tag": r.get("tag"), "id": r.get("id"), "name": r.get("title"),
                 "status": r.get("status"), "assets": len(r.get("assets") or []),
                 "url": self.web_url(r.get("tag"))} for r in out.get("releases", [])]

    def localize(self, notes):
        """CHANGELOG ссылается на GitVerse (repository в package.json); в заметках
        SourceCraft подменяем хост и владельца — путь /commit/<sha> у обоих один."""
        gv = self.path and local.repo_from_remote(self.path, "origin", "gitverse.ru")
        if gv:
            notes = notes.replace(f"https://gitverse.ru/{gv}", f"https://sourcecraft.dev/{self.repo}")
        return notes

    def release(self, tag, title, notes, sha=None, branch=None):
        """REST вместо CLI src. target_branch заставляет SourceCraft создать тег самому
        и даёт 409 BranchAlreadyExists, если тег уже запушен; по нашему порядку тег
        всегда есть, поэтому поле передаём только по явной просьбе."""
        body = {"tag": tag, "title": title, "release_notes": self.localize(notes), "publish": True}
        if branch:
            body["target_branch"] = branch
        out = self.api(f"/repos/{self.repo}/releases", json_body=body) or {}
        return {"tag": out.get("tag", tag), "status": out.get("status"),
                "url": self.web_url(tag)}

    def update(self, tag, title=None, notes=None):
        """Правка описания: только по тегу, по id SourceCraft отвечает 404."""
        body = {k: v for k, v in (("title", title), ("release_notes", notes and self.localize(notes))) if v}
        self.api(f"/repos/{self.repo}/releases/tag/{tag}", method="PATCH", json_body=body)
        return {"tag": tag, "url": self.web_url(tag)}

    def asset(self, tag, path, name=None):
        """Файл грузится строго в поле file, ограничений по расширению нет."""
        fname, data, ctype = self._file(path)
        self.api(f"/repos/{self.repo}/releases/tag/{tag}/attachments",
                 files=[("file", name or fname, data, ctype)], timeout=900)
        return {"name": name or fname, "ok": True}

    def web_url(self, tag):
        return f"https://sourcecraft.dev/{self.repo}/releases/{tag}"


def both(cfg, path=None):
    """Пара хостингов для репозитория; недоступный отдаётся ошибкой, а не падением."""
    out = {}
    for cls in (GitVerse, SourceCraft):
        try:
            out[cls.source] = cls(cfg, path=path)
        except StudyError as e:
            out[cls.source] = e
    return out
