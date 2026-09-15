"""HTTP поверх stdlib: form, json, multipart. Все ошибки — StudyError.

urllib по умолчанию представляется `Python-urllib/3.12`, поэтому User-Agent задаётся явно.
"""
import json as jsonlib
import ssl
import urllib.error
import urllib.parse
import urllib.request
import uuid

from .config import StudyError

UA = "study/1"


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


def send(url, source, *, method=None, headers=None, data=None, timeout=600, where=None):
    """Один поход в сеть → (код, заголовки, тело). Тело ошибки читается обязательно:
    GitVerse отвечает 400/422 с пустым телом, и без чтения пользователь увидит меньше,
    чем видел с curl."""
    head = {"User-Agent": UA}
    head.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=head,
                                 method=method or ("POST" if data is not None else "GET"))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        detail = (e.read() or b"")[:200].decode("utf-8", "replace").strip()
        msg = f"HTTP {e.code}: {detail}" if detail else f"HTTP {e.code} (пустой ответ)"
        raise StudyError(source, msg, where=where) from None
    except urllib.error.URLError as e:
        # macOS со сборкой python.org без «Install Certificates.command» не доверяет никому
        bad_cert = isinstance(e.reason, ssl.SSLCertVerificationError)
        raise StudyError(source, f"нет связи: {e.reason}", where=where,
                         code="certificate" if bad_cert else None) from None
    except OSError as e:   # обрыв или таймаут уже после соединения
        raise StudyError(source, f"нет связи: {e}", where=where) from None


def raw(url, source, *, headers=None, timeout=600, where=None):
    """GET, отдающий байты: файлы курсов приходят не JSON."""
    return send(url, source, headers=headers, timeout=timeout, where=where)[2]


def request(url, source, *, method=None, headers=None, form=None, json_body=None,
            files=None, fields=None, timeout=120, where=None):
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
                timeout=timeout, where=where)[2]
    if not body:
        return None
    try:
        return jsonlib.loads(body)
    except ValueError:
        return body.decode("utf-8", "replace")
