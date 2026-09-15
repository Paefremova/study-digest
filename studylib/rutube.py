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
import time
import uuid

from . import net
from .config import StudyError

BASE = "https://rutube.ru/api"
REFRESH_URL = "https://rutube.ru/multipass/api/v3/accounts/token/"
UPLOAD_URL = "https://u.rutube.ru/upload/"
# u.rutube.ru за антиботом — ходим с браузерными UA/Origin/Referer, как студия.
WEB_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"


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
        os.chmod(path.parent, 0o700)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(value + "\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)

    @staticmethod
    def _jwt_exp(token):
        """Срок действия JWT (unix); 0 — если не разобрать (считаем истёкшим)."""
        try:
            p = token.split(".")[1]
            return json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4))).get("exp", 0)
        except Exception:
            return 0

    # --- вход ---

    def login(self, email=None, password=None):
        """Схема token: token_auth по email+паролю. Пароль не хранится — только сам токен."""
        email = email or input("Rutube email: ").strip()
        password = password or getpass.getpass("Rutube пароль: ")
        out = net.request(BASE + "/accounts/token_auth/", self.source,
                          json_body={"username": email, "password": password},
                          where="token_auth") or {}
        token = out.get("token") if isinstance(out, dict) else None
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
        out = net.request(REFRESH_URL, self.source, method="POST",
                          headers={"Cookie": "refreshToken=" + token},
                          where="token/refresh") or {}
        access = out.get("access_token") if isinstance(out, dict) else None
        if not access:
            raise StudyError(self.source, f"refresh не удался: {out}", code="auth")
        return access, out.get("refresh_token")

    def _mint(self):
        """Кэшированный access; refresh дёргаем только когда access истёк (он живёт ~14 дней)."""
        if self._access:
            return self._access
        af = self._access_path()
        if af.exists():
            cached = af.read_text().strip()
            if cached and self._jwt_exp(cached) - time.time() > 120:
                self._access = cached
                return cached
        rf = self._refresh_path()
        if not rf.exists():
            raise StudyError(self.source, f"нет файла {rf}", code="notoken")
        refresh = rf.read_text().strip()
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
            return {"Authorization": "Token " + self.cfg.token("RUTUBE_TOKEN_FILE")}
        raise StudyError(self.source, f"неизвестный режим {mode}", code="config")

    def api(self, path, **kw):
        head = self._auth_header()
        head.update(kw.pop("headers", None) or {})
        return net.request(BASE + path, self.source, headers=head, where=path, **kw)

    def me(self):
        """Проверка входа: список своих видео, первая страница."""
        out = self.api("/video/person/?limit=5") or {}
        rows = out.get("results", []) if isinstance(out, dict) else []
        return [{"id": v.get("id"), "title": v.get("title"), "url": v.get("video_url"),
                 "hidden": v.get("is_hidden")} for v in rows]

    # --- видео-флоу ---

    def categories(self):
        """Список категорий (публично, токен не нужен): id + короткое имя + название."""
        out = net.request(BASE + "/video/category/", self.source, where="video/category")
        rows = out.get("results", []) if isinstance(out, dict) else (out or [])
        return [{"id": c.get("id"), "short": c.get("short_name"), "name": c.get("name")} for c in rows]

    def video(self, vid):
        """Метаданные и состояние своего видео (v2)."""
        return self.api(f"/v2/video/{vid}/") or {}

    def edit(self, vid, **fields):
        """PATCH метаданных: только переданные (не None) поля title/description/category/is_hidden."""
        body = {k: v for k, v in fields.items() if v is not None}
        if not body:
            raise StudyError(self.source, "нечего менять", code="usage")
        return self.api(f"/v2/video/{vid}/?client=vulp", method="PATCH", json_body=body) or {}

    def _channel_id(self):
        """id канала (= user_id из access-токена) — нужен для списка своих плейлистов."""
        payload = self._mint().split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        cid = data.get("user_id") or (data.get("data") or {}).get("user_info", {}).get("id")
        if not cid:
            raise StudyError(self.source, "не удалось определить id канала", code="auth")
        return cid

    def playlists(self):
        """Свои плейлисты."""
        out = self.api(f"/playlist/user/{self._channel_id()}/") or {}
        rows = out.get("results", []) if isinstance(out, dict) else (out or [])
        return [{"id": p.get("id"), "title": p.get("title"),
                 "url": f"https://rutube.ru/plst/{p.get('id')}/",
                 "count": p.get("videos_count"), "hidden": p.get("is_hidden")} for p in rows]

    def playlist_create(self, title, hidden=False):
        return self.api("/playlist/custom/", method="POST",
                        json_body={"title": title, "is_hidden": bool(hidden)}) or {}

    def playlist_add(self, pid, vid):
        """Добавить видео vid в плейлист pid (в пути — id видео, include — id плейлистов)."""
        return self.api(f"/playlist/custom/update/{vid}/", method="POST",
                        json_body={"include": [int(pid)], "exclude": []}) or {}

    # --- загрузка ---

    def progress(self, vid):
        """Прогресс загрузки/конвертации видео."""
        return self.api(f"/uploader/{vid}/progress/") or {}

    def upload_url(self, src, title=None, description=None, category=None, hidden=False):
        """Импорт по URL: Rutube сам скачает файл, затем правим метаданные."""
        out = self.api("/video/", method="POST",
                       json_body={"url": src, "category_id": category or 13}) or {}
        vid = out.get("video_id") or out.get("id")
        if not vid:
            raise StudyError(self.source, f"video/ без id: {out}", code="upload")
        self.edit(vid, title=title, description=description, category=category, is_hidden=bool(hidden))
        return {"id": vid, "url": f"https://rutube.ru/video/{vid}/", "title": title, "hidden": bool(hidden)}

    def upload_file(self, path, title=None, description=None, category=None, hidden=False):
        """Прямая загрузка локального файла: сессия → метаданные → байты (tus)."""
        title = title or os.path.splitext(os.path.basename(path))[0]
        sess = self.api("/uploader/upload_session/?client=vulp&batch_id=" + uuid.uuid4().hex,
                        method="POST", json_body={"title": title}) or {}
        sid, vid = sess.get("sid"), sess.get("video")
        if not sid or not vid:
            raise StudyError(self.source, f"upload_session без sid/video: {sess}", code="upload")
        self.edit(vid, title=title, description=description, category=category, is_hidden=bool(hidden))
        self._tus(sid, vid, path)
        return {"id": vid, "url": f"https://rutube.ru/video/{vid}/", "title": title, "hidden": bool(hidden)}

    def _tus(self, sid, vid, path):
        """tus creation-with-upload: весь файл одним POST (как студийный клиент, chunkSize=∞)."""
        b64 = lambda s: base64.b64encode(str(s).encode()).decode()
        meta = f"sessionId {b64(sid)},videoId {b64(vid)},userId {b64(self._channel_id())}"
        body = open(path, "rb").read()
        head = {"User-Agent": WEB_UA, "Tus-Resumable": "1.0.0", "Origin": "https://studio.rutube.ru",
                "Referer": "https://studio.rutube.ru/", "Content-Type": "application/offset+octet-stream",
                "Upload-Length": str(len(body)), "Upload-Metadata": meta}
        _, hd, _ = net.send(UPLOAD_URL + sid, self.source, method="POST", headers=head, data=body, where="tus")
        got = int(hd.get("Upload-Offset", 0))
        if got != len(body):
            raise StudyError(self.source, f"загружено {got} из {len(body)} байт", code="upload")
        return got
