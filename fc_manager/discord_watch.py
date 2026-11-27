"""Wait on the exact Windows parent process and post a personal unexpected-exit alert."""

import ctypes
from ctypes import wintypes
from datetime import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import uuid

if __package__:
    from . import discord_io as io
else:
    # -I isolates the helper from environment and user site packages.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from fc_manager import discord_io as io


def _kernel():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    return kernel


def _marker(token):
    return Path(tempfile.gettempdir()) / f"fc-manager-{token}.closed"


def start_watcher(app_dir):
    if os.name != "nt":
        return None
    kernel = _kernel()
    # Inherit a handle instead of looking up a PID after startup. This also handles PID reuse.
    handle = kernel.OpenProcess(0x100000 | 0x1000, True, os.getpid())
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        token = uuid.uuid4().hex
        startup = subprocess.STARTUPINFO()
        startup.lpAttributeList = {"handle_list": [handle]}
        cmd = ([sys.executable, "--watch-exit"] if getattr(sys, "frozen", False)
               else [sys.executable, "-I", "-B", str(Path(__file__).resolve())])
        subprocess.Popen(cmd + [str(handle), token, str(Path(app_dir).resolve())],
                         startupinfo=startup, close_fds=True, creationflags=subprocess.CREATE_NO_WINDOW,
                         cwd=Path(__file__).resolve().parent.parent,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return _marker(token)
    finally:
        kernel.CloseHandle(handle)


def alert(app_dir, code, *, unprotect=io.unprotect, exchange=io.Exchange, sleep=time.sleep):
    try:
        settings = json.loads((app_dir / "settings.json").read_text(encoding="utf-8-sig"))
        if settings["DiscordEnabled"] is not True:
            return
        url = unprotect(settings["EncryptedWebhook"])
        if not io.WEBHOOK.fullmatch(url):
            return
    except (ValueError, OSError, TypeError, KeyError):
        return
    text = "도우미 프로세스가 정상 종료 확인 없이 닫혔습니다."
    if code is not None:
        text += f"\n종료 코드: {code}"
    body = io.webhook_body("FC 감독모드 도우미 비정상 종료", text)
    for attempt in range(1, 4):
        status, _ = exchange(url, body, timeout=10).run()
        if 200 <= status < 300:
            return
        if attempt < 3:
            sleep(2 * attempt)
    logs = app_dir / "오류확인용_보내기" / "logs"
    if logs.is_dir():
        with (logs / "exit-watcher.log").open("a", encoding="utf-8") as stream:
            stream.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 비정상 종료 Discord 알림 전송 실패\n")


def main(args=None):
    args = sys.argv[1:] if args is None else args
    if os.name != "nt" or len(args) != 3:
        return 2
    raw_handle, token, folder = args
    if not raw_handle.isdecimal() or not re.fullmatch("[0-9a-f]{32}", token):
        return 2
    handle = int(raw_handle)
    marker = _marker(token)
    kernel = _kernel()
    try:
        if kernel.WaitForSingleObject(handle, 0xffffffff) != 0:
            return 1
        if not marker.exists():
            code = wintypes.DWORD()
            known = kernel.GetExitCodeProcess(handle, ctypes.byref(code))
            alert(Path(folder), code.value if known else None)
    finally:
        kernel.CloseHandle(handle)
        marker.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
