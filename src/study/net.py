"""HTTP поверх stdlib: form, json, multipart. Все ошибки — StudyError.

urllib по умолчанию представляется `Python-urllib/3.12`, поэтому User-Agent задаётся явно.
"""
import json as jsonlib
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from .config import StudyError

UA = "study/1"
# Повторы для чтения: обрыв, таймаут и 502–504 у Moodle случаются и проходят сами.
# Запись (отправка ответа, релиз, загрузка видео) не повторяется — второй раз она не идемпотентна.
RETRIES = 2         # повторов после первой попытки
RETRY_PAUSE = 2     # секунд между попытками
RETRY_HTTP = {502, 503, 504}


def multipart(fields, files):
    """fields: {имя: значение}, files: [(поле, имя файла, байты, тип)] → (тело, content-type)."""
    b = uuid.uuid4().hex
    out = bytearray()
    for key, value in (fields or {}).items():
        out += f'--{b}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
    for key, name, data, ctype in files or []:
        out += (f'--{b}\r\nContent-Disposition: form-data; name="{key}"; filename="{name}"\r\n'
                f'Content-Type: {ctype}\r\n\r\n').encode()
        out += data + b"\r\n"
    out += f"--{b}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={b}"


def error(e, source, where):
    """Ошибка сети → (StudyError, стоит ли повторить). Тело HTTP-ошибки читается обязательно:
    GitVerse отвечает 400/422 с пустым телом, и без чтения пользователь увидит меньше,
    чем видел с curl."""
    if isinstance(e, urllib.error.HTTPError):
        detail = (e.read() or b"")[:200].decode("utf-8", "replace").strip()
        msg = f"HTTP {e.code}: {detail}" if detail else f"HTTP {e.code} (пустой ответ)"
        return StudyError(source, msg, where=where), e.code in RETRY_HTTP
    if isinstance(e, urllib.error.URLError):
        # macOS со сборкой python.org без «Install Certificates.command» не доверяет никому
        bad_cert = isinstance(e.reason, ssl.SSLCertVerificationError)
        return StudyError(source, f"нет связи: {e.reason}", where=where,
                          code="certificate" if bad_cert else None), not bad_cert
    return StudyError(source, f"нет связи: {e}", where=where), True   # обрыв после соединения


def send(url, source, *, method=None, headers=None, data=None, timeout=600, where=None,
         retries=0):
    """Поход в сеть → (код, заголовки, тело); до `retries` повторов на проходящих ошибках."""
    head = {"User-Agent": UA}
    head.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=head,
                                 method=method or ("POST" if data is not None else "GET"))
    for attempt in range(retries + 1):
        if attempt:
            time.sleep(RETRY_PAUSE)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, dict(r.headers), r.read()
        except (urllib.error.URLError, OSError) as e:   # HTTPError — тоже URLError
            err, again = error(e, source, where)
            if not again:
                break
    raise err from None


def raw(url, source, *, headers=None, timeout=600, where=None, retries=0):
    """GET, отдающий байты: файлы курсов приходят не JSON."""
    return send(url, source, headers=headers, timeout=timeout, where=where, retries=retries)[2]


def request(url, source, *, method=None, headers=None, form=None, json_body=None,
            files=None, fields=None, timeout=120, where=None, retries=0):
    """Один запрос. Возвращает разобранный JSON, либо текст, если это не JSON."""
    head = dict(headers or {})
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        head.setdefault("Content-Type", "application/x-www-form-urlencoded")
    elif json_body is not None:
        # Тело сериализуется как есть: GitVerse отвергает хвостовой перевод строки (422).
        data = jsonlib.dumps(json_body, ensure_ascii=False).encode()
        head["Content-Type"] = "application/json"
    elif files is not None:
        data, head["Content-Type"] = multipart(fields, files)

    body = send(url, source, method=method, headers=head, data=data,
                timeout=timeout, where=where, retries=retries)[2]
    if not body:
        return None
    try:
        return jsonlib.loads(body)
    except ValueError:
        return body.decode("utf-8", "replace")
