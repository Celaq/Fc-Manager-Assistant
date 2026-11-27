"""Windows current-user DPAPI, bounded HTTPS exchanges, and release data formats."""

import base64
import ctypes
from ctypes import wintypes
from collections import deque
from datetime import datetime
import http.client
import json
import os
from pathlib import Path
import re
import socket
import tempfile
import threading
from urllib.parse import urlsplit
import uuid


WEBHOOK = re.compile(r"https://(?:(?:canary|ptb)\.)?discord(?:app)?\.com/api/webhooks/[0-9]+/[A-Za-z0-9._-]+/?", re.ASCII | re.I)
SECRET = re.compile(r"[A-Za-z0-9_-]{43}", re.ASCII)
SNOWFLAKE = re.compile(r"[0-9]{17,20}", re.ASCII)
MAX_PNG = 2 * 1024 * 1024


def pairing_link(value):
    """Return the relay origin and fragment. The fragment is never sent as a URL."""
    value = value.strip()
    if any(c.isspace() or ord(c) < 32 or c in "\\?" for c in value):
        raise ValueError("본인의 /연결 응답에서 HTTPS 링크 전체를 복사하세요.")
    url = urlsplit(value)
    if (url.scheme != "https" or not url.hostname or url.username is not None
            or url.password is not None or url.path not in ("", "/")
            or not SECRET.fullmatch(url.fragment)):
        raise ValueError("본인의 /연결 응답에서 HTTPS 링크 전체를 복사하세요.")
    # Accessing port validates its range and syntax before saving or contacting the relay.
    if url.port == 0:
        raise ValueError("중계 서버 주소를 확인하세요.")
    return f"https://{url.netloc}", url.fragment


class _Blob(ctypes.Structure):
    _fields_ = [("count", wintypes.DWORD), ("data", ctypes.c_void_p)]


def _crypt(data, protect):
    if os.name != "nt":
        raise OSError("Windows DPAPI가 필요합니다.")
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    method = crypt.CryptProtectData if protect else crypt.CryptUnprotectData
    method.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p,
                       ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
    method.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    buffer = ctypes.create_string_buffer(data)
    source = _Blob(len(data), ctypes.addressof(buffer))
    output = _Blob()
    try:
        if not method(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
            raise ctypes.WinError(ctypes.get_last_error())
        return ctypes.string_at(output.data, output.count)
    finally:
        ctypes.memset(buffer, 0, len(buffer))
        if output.data:
            ctypes.memset(output.data, 0, output.count)
            kernel.LocalFree(output.data)


def protect(value):
    # Windows PowerShell's ConvertFrom-SecureString uses UTF-16LE + DPAPI hex.
    return _crypt(value.encode("utf-16-le"), True).hex()


def unprotect(value):
    return _crypt(bytes.fromhex(value), False).decode("utf-16-le")


def atomic_text(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False, mode="w", encoding="utf-8") as temp:
        name = Path(temp.name)
        try:
            temp.write(value)
            temp.flush()
            os.fsync(temp.fileno())
        except BaseException:
            temp.close()
            name.unlink(missing_ok=True)
            raise
    try:
        os.replace(name, path)
    finally:
        name.unlink(missing_ok=True)


def limited(value, units):
    # Discord and the Node relay count UTF-16 code units.
    return value.encode("utf-16-le")[:2 * units].decode("utf-16-le", errors="ignore")


def webhook_body(title, message):
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return {"content": limited(f"**{title}**\n{message}\n시간: {stamp}", 1900),
            "username": "FC Manager Assistant", "allowed_mentions": {"parse": []}}


class Exchange:
    """One cancellable POST. Callers receive only a status and a bounded JSON object."""

    def __init__(self, url, body, token=None, timeout=6):
        self.url = url
        self.body = body
        self.token = token
        self.timeout = timeout
        self.socket = None
        self.cancelled = threading.Event()

    def cancel(self):
        self.cancelled.set()
        sock = self.socket
        if sock:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def run(self):
        url = urlsplit(self.url)
        conn = http.client.HTTPSConnection(url.hostname, url.port, timeout=self.timeout)
        try:
            if self.cancelled.is_set():
                return 0, {}
            conn.connect()
            self.socket = conn.sock
            if self.cancelled.is_set():
                return 0, {}
            headers = {"Content-Type": "application/json; charset=utf-8",
                       "User-Agent": "FC-Manager-Assistant"}
            if self.token:
                headers["Authorization"] = "Bearer " + self.token
            conn.request("POST", url.path, json.dumps(self.body, ensure_ascii=False).encode(), headers)
            with conn.getresponse() as response:
                if not 200 <= response.status < 300:
                    return response.status, {}  # Preserve 530 even for large gateway HTML.
                raw = response.read(16385)
                if len(raw) > 16384:
                    return 0, {}
                obj = json.loads(raw) if raw else {}
                return (response.status, obj) if isinstance(obj, dict) else (0, {})
        except (OSError, ValueError, http.client.HTTPException):
            return 0, {}
        finally:
            conn.close()
            self.socket = None


def _redact(text):
    text = re.sub(r"https?://\S+", "[URL]", text, flags=re.I)
    return re.sub(r"Bearer\s+\S+", "[인증 정보]", text, flags=re.I)


def valid_png(data):
    return 24 <= len(data) <= MAX_PNG and data.startswith(b"\x89PNG\r\n\x1a\n")


def make_report(folder, reason, capture, version, saved=False):
    now = datetime.now()
    parts = [f"FC Manager {version} · {reason} · {now:%Y-%m-%d %H:%M:%S}"]
    info = folder / "진단정보.txt"
    if info.is_file():
        with info.open(encoding="utf-8-sig", errors="replace") as stream:
            parts.append(stream.read(17500))
    payload = {"id": uuid.uuid4().hex}
    if capture:
        path = Path(capture)
        if path.is_file() and path.stat().st_size <= MAX_PNG:
            with path.open("rb") as stream:
                image = stream.read(MAX_PNG + 1)
            if valid_png(image):
                payload["png"] = base64.b64encode(image).decode("ascii")
                parts.append(f"첨부 캡처 · {path.name} · {datetime.fromtimestamp(path.stat().st_mtime):%Y-%m-%d %H:%M:%S}")

    logs = folder / "logs"
    paths = (sorted((p for p in logs.glob("FCManager_*.log")
                     if re.fullmatch(r"FCManager_[0-9]{8}\.log", p.name)), reverse=True)
             if saved else [logs / f"FCManager_{now:%Y%m%d}.log"])
    lines = []
    for path in paths:
        if path.is_file():
            with path.open(encoding="utf-8-sig", errors="replace") as stream:
                tail = deque((line.rstrip("\r\n") for line in stream), maxlen=40-len(lines))
            lines[:0] = tail
        if len(lines) == 40:
            break
    payload["text"] = limited(_redact("\n".join(parts + lines)), 17500)
    return payload


def manual_report(folder, version):
    path = folder / "last-error.json"
    if path.is_file() and path.stat().st_size <= 3 * 1024 * 1024:
        try:
            obj = json.loads(path.read_text(encoding="utf-8-sig"))
            body = obj["text"]
            image = obj.get("png")
            if (isinstance(body, str) and body.strip() and len(body.encode("utf-16-le")) <= 35000
                    and (not image or (isinstance(image, str) and len(image) <= 2796204
                                       and valid_png(base64.b64decode(image, validate=True))))):
                payload = {"id": uuid.uuid4().hex, "text": _redact(body)}
                if image:
                    payload["png"] = image
                return payload
        except (ValueError, KeyError, TypeError, OSError):
            pass
    return make_report(folder, "Discord 수동 요청 · 저장된 진단정보 및 실행 기록",
                       folder / "captures" / "coach-board.png", version, saved=True)
