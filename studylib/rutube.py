"""Rutube: две схемы входа на выбор.

token — старый DRF TokenAuthentication: token_auth по email+паролю, заголовок «Token <t>»,
        токен бессрочный. Работает только для аккаунтов с паролем (auth_type=password).
jwt   — новая схема rupass (VK ID / Gazprom ID): годовой refresh_token (ротируется при каждом
        обращении) минтит короткий access_token, заголовок «Bearer <t>». Единственный путь для
        аккаунтов через внешний SSO (auth_type=gid). refresh_token берётся один раз из cookie
        браузера (rutube.ru → DevTools → Application → Cookies → refreshToken).

Режим выбирается флагом --mode; auto предпочитает jwt (если есть файл refresh), иначе token.
"""
import getpass
import os

from . import net
from .config import StudyError

BASE = "https://rutube.ru/api"
REFRESH_URL = "https://rutube.ru/multipass/api/v3/accounts/token/"


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

    @staticmethod
    def _save(path, value):
        """Атомарно: пишем во временный файл и подменяем — обрыв не оставит пустой credential."""
        path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(path.parent, 0o700)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(value + "\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)

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
        """Из сохранённого refresh — свежий access; ротированный refresh сразу сохраняем."""
        if self._access:
            return self._access
        rf = self._refresh_path()
        if not rf.exists():
            raise StudyError(self.source, f"нет файла {rf}", code="notoken")
        token = rf.read_text().strip()
        access, new = self._refresh_call(token)
        if new and new != token:
            self._save(rf, new)
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
        return net.request(BASE + path, self.source, headers=self._auth_header(), where=path, **kw)

    def me(self):
        """Проверка входа: список своих видео, первая страница."""
        out = self.api("/video/person/?limit=5") or {}
        rows = out.get("results", []) if isinstance(out, dict) else []
        return [{"id": v.get("id"), "title": v.get("title"), "url": v.get("video_url"),
                 "hidden": v.get("is_hidden")} for v in rows]
