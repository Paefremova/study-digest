"""Rutube: две схемы входа на выбор.

token — старый DRF TokenAuthentication: token_auth по email+паролю, заголовок «Token <t>»,
        токен бессрочный. Работает только для аккаунтов с паролем (auth_type=password).
jwt   — новая схема rupass (VK ID / Gazprom ID): годовой refresh_token (ротируется при каждом
        обращении) минтит короткий access_token, заголовок «Bearer <t>». Единственный путь для
        аккаунтов через внешний SSO (auth_type=gid). refresh_token берётся один раз из cookie
        браузера (rutube.ru → DevTools → Application → Cookies → refreshToken).

Режим выбирается флагом --mode; auto предпочитает jwt (если есть файл refresh), иначе token.
"""
import base64
import getpass
import json
import os
import pathlib
import time
import uuid

from . import net
from .config import StudyError

BASE = "https://rutube.ru/api"
REFRESH_URL = "https://rutube.ru/multipass/api/v3/accounts/token/"
UPLOAD_URL = "https://u.rutube.ru/upload/"
# u.rutube.ru за антиботом — ходим с браузерными UA/Origin/Referer, как студия.
WEB_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
          "Chrome/128.0 Safari/537.36")
# Возрастное ограничение: человеческий возраст → age_id (справочник зашит в студию, эндпоинта нет).
AGE = {0: 1, 6: 2, 12: 3, 14: 6, 16: 4, 18: 5}
DEFAULT_CATEGORY = 13   # «Разное»; список — `study rt categories`
ACCESS_MARGIN = 120     # секунд до истечения access, когда его пора перевыпустить


def _obj(out):
    """Ответ как словарь: пустое тело, текст или список → {}."""
    return out if isinstance(out, dict) else {}


def _extract_refresh(s):
    """Значение cookie refreshToken из строки; если передан сам токен — возвращает как есть."""
    s = (s or "").strip()
    for part in s.split(";"):
        name, sep, value = part.strip().partition("=")
        if sep and name == "refreshToken":
            return value.strip()
    return s


class Rutube:
    source = "rutube"

    def __init__(self, cfg, mode="auto"):
        self.cfg = cfg
        self.mode = mode
        self._access = None

    def _token_path(self):
        return self.cfg.path_of("RUTUBE_TOKEN_FILE")

    def _refresh_path(self):
        return self.cfg.path_of("RUTUBE_REFRESH_FILE")

    def _access_path(self):
        return self.cfg.path_of("RUTUBE_ACCESS_FILE")

    @staticmethod
    def _save(path, value):
        """Атомарно: пишем во временный файл и подменяем — обрыв не оставит пустой credential."""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(value + "\n", encoding="utf-8")
        if os.name == "posix":
            path.parent.chmod(0o700)
            tmp.chmod(0o600)
        tmp.replace(path)

    @staticmethod
    def _jwt_payload(token):
        """Полезная нагрузка JWT; {} — если не разобрать."""
        try:
            p = token.split(".")[1]
            data = json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))
        except (IndexError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    # --- вход ---

    def login(self, email=None, password=None):
        """Схема token: token_auth по email+паролю. Пароль не хранится — только сам токен."""
        email = email or input("Rutube email: ").strip()
        password = password or getpass.getpass("Rutube пароль: ")
        out = _obj(net.request(BASE + "/accounts/token_auth/", self.source,
                               json_body={"username": email, "password": password},
                               where="token_auth"))
        token = out.get("token")
        if not token:
            raise StudyError(self.source, f"токен не получен: {out}", code="auth")
        self._save(self._token_path(), token)
        return {"token_file": str(self._token_path()), "ok": True}

    def save_refresh(self, refresh=None):
        """Схема jwt: проверить refresh_token (сам токен или строку cookie) и сохранить.

        Проверка идёт ДО записи, поэтому битый ввод не затирает уже рабочий файл; на диск
        ложится ротированный токен из ответа.
        """
        if refresh is None:
            refresh = getpass.getpass("Rutube refreshToken (или строка cookie): ")
        refresh = _extract_refresh(refresh)
        if not refresh:
            raise StudyError(self.source, "не нашёл refreshToken", code="auth")
        access, new = self._refresh_call(refresh)
        self._save(self._refresh_path(), new or refresh)
        self._save(self._access_path(), access)
        self._access = access
        return {"refresh_file": str(self._refresh_path()), "ok": True}

    # --- авторизация запросов ---

    def _refresh_call(self, token):
        """refresh_token → (свежий access_token, новый refresh_token|None). Ошибка — StudyError."""
        out = _obj(net.request(REFRESH_URL, self.source, method="POST",
                               headers={"Cookie": "refreshToken=" + token},
                               where="token/refresh"))
        access = out.get("access_token")
        if not access:
            raise StudyError(self.source, f"refresh не удался: {out}", code="auth")
        return access, out.get("refresh_token")

    def _mint(self):
        """Кэшированный access; refresh дёргаем только когда access истёк (он живёт ~14 дней)."""
        if self._access:
            return self._access
        af = self._access_path()
        if af.exists():
            cached = af.read_text(encoding="utf-8").strip()
            if cached and self._jwt_payload(cached).get("exp", 0) - time.time() > ACCESS_MARGIN:
                self._access = cached
                return cached
        rf = self._refresh_path()
        if not rf.exists():
            raise StudyError(self.source, f"нет файла {rf}", code="notoken")
        refresh = rf.read_text(encoding="utf-8").strip()
        access, new = self._refresh_call(refresh)
        if new and new != refresh:
            self._save(rf, new)
        self._save(af, access)
        self._access = access
        return access

    def _auth_header(self):
        mode = self.mode
        if mode == "auto":
            if self._refresh_path().exists():
                mode = "jwt"
            elif self._token_path().exists():
                mode = "token"
            else:
                raise StudyError(self.source, f"нет ни {self._refresh_path()}, ни "
                                 f"{self._token_path()} — см. rt login или rt jwt", code="notoken")
        if mode == "jwt":
            return {"Authorization": "Bearer " + self._mint()}
        if mode == "token":
            return {"Authorization": "Token " + self.cfg.token("RUTUBE_TOKEN")}
        raise StudyError(self.source, f"неизвестный режим {mode}", code="config")

    def api(self, path, **kw):
        """Запрос с авторизацией, ответ как есть (для `rt api`)."""
        head = self._auth_header()
        head.update(kw.pop("headers", None) or {})
        return net.request(BASE + path, self.source, headers=head, where=path, **kw)

    def _json(self, path, **kw):
        """То же, но ответ — всегда словарь."""
        return _obj(self.api(path, **kw))

    def me(self):
        """Проверка входа: список своих видео, первая страница."""
        rows = self._json("/video/person/?limit=5").get("results", [])
        return [{"id": v.get("id"), "title": v.get("title"), "url": v.get("video_url"),
                 "hidden": v.get("is_hidden")} for v in rows]

    # --- видео-флоу ---

    def categories(self):
        """Список категорий (публично, токен не нужен, ответ — голый массив)."""
        rows = net.request(BASE + "/video/category/", self.source, where="video/category") or []
        return [{"id": c.get("id"), "short": c.get("short_name"), "name": c.get("name")}
                for c in rows]

    @staticmethod
    def video_url(vid):
        return f"https://rutube.ru/video/{vid}/"

    @staticmethod
    def playlist_url(pid):
        return f"https://rutube.ru/plst/{pid}/"

    def video(self, vid):
        """Метаданные и состояние своего видео (v2)."""
        return self._json(f"/v2/video/{vid}/")

    def edit(self, vid, **fields):
        """PATCH метаданных: title/description/category (int id)/is_hidden/age (0,6,12,14,16,18)."""
        age = fields.pop("age", None)
        body = {k: v for k, v in fields.items() if v is not None}
        if age is not None:
            body["age_restriction"] = AGE.get(int(age), int(age))
        if not body:
            raise StudyError(self.source, "нечего менять", code="usage")
        return self._json(f"/v2/video/{vid}/?client=vulp", method="PATCH", json_body=body)

    def _channel_id(self):
        """id канала (= user_id из access-токена) — нужен для списка своих плейлистов."""
        data = self._jwt_payload(self._mint())
        cid = data.get("user_id") or (data.get("data") or {}).get("user_info", {}).get("id")
        if not cid:
            raise StudyError(self.source, "не удалось определить id канала", code="auth")
        return cid

    def playlists(self):
        """Свои плейлисты."""
        rows = self._json(f"/playlist/user/{self._channel_id()}/").get("results", [])
        return [{"id": p.get("id"), "title": p.get("title"), "url": self.playlist_url(p.get("id")),
                 "count": p.get("videos_count"), "hidden": p.get("is_hidden")} for p in rows]

    def playlist_create(self, title, hidden=False):
        out = self._json("/playlist/custom/", method="POST",
                         json_body={"title": title, "is_hidden": bool(hidden)})
        pid = out.get("id")
        return {"id": pid, "title": out.get("title", title), "hidden": out.get("is_hidden", hidden),
                "url": self.playlist_url(pid) if pid else None}

    def playlist_add(self, pid, vid):
        """Добавить видео vid в плейлист pid (в пути — id видео, include — id плейлистов)."""
        return self._json(f"/playlist/custom/update/{vid}/", method="POST",
                          json_body={"include": [int(pid)], "exclude": []})

    # --- загрузка ---

    def progress(self, vid):
        """Прогресс загрузки/конвертации видео."""
        return self._json(f"/uploader/{vid}/progress/")

    def _describe(self, vid, title, hidden, **fields):
        """Общий хвост загрузки: выставить метаданные и вернуть карточку видео."""
        self.edit(vid, title=title, is_hidden=bool(hidden), **fields)
        return {"id": vid, "url": self.video_url(vid), "title": title, "hidden": bool(hidden)}

    def upload_url(self, src, title=None, description=None, category=None, hidden=False, age=None):
        """Импорт по URL: Rutube сам скачает файл, затем правим метаданные."""
        out = self._json("/video/", method="POST",
                         json_body={"url": src, "category_id": category or DEFAULT_CATEGORY})
        vid = out.get("video_id") or out.get("id")
        if not vid:
            raise StudyError(self.source, f"video/ без id: {out}", code="upload")
        return self._describe(vid, title, hidden, description=description, category=category,
                              age=age)

    def upload_file(self, path, title=None, description=None, category=None, hidden=False,
                    age=None):
        """Прямая загрузка локального файла: сессия → метаданные → байты (tus)."""
        path = pathlib.Path(path)
        title = title or path.stem
        sess = self._json("/uploader/upload_session/?client=vulp&batch_id=" + uuid.uuid4().hex,
                          method="POST", json_body={"title": title})
        sid, vid = sess.get("sid"), sess.get("video")
        if not sid or not vid:
            raise StudyError(self.source, f"upload_session без sid/video: {sess}", code="upload")
        card = self._describe(vid, title, hidden, description=description, category=category,
                              age=age)
        self._tus(sid, vid, path.read_bytes())
        return card

    def _tus(self, sid, vid, body):
        """tus creation-with-upload: весь файл одним POST (как студийный клиент, chunkSize=∞)."""
        def b64(s):
            return base64.b64encode(str(s).encode()).decode()
        meta = f"sessionId {b64(sid)},videoId {b64(vid)},userId {b64(self._channel_id())}"
        head = {"User-Agent": WEB_UA, "Tus-Resumable": "1.0.0",
                "Origin": "https://studio.rutube.ru", "Referer": "https://studio.rutube.ru/",
                "Content-Type": "application/offset+octet-stream",
                "Upload-Length": str(len(body)), "Upload-Metadata": meta}
        _, hd, _ = net.send(UPLOAD_URL + sid, self.source, method="POST", headers=head,
                            data=body, where="tus")
        got = int(hd.get("Upload-Offset", 0))
        if got != len(body):
            raise StudyError(self.source, f"загружено {got} из {len(body)} байт", code="upload")
        return got
