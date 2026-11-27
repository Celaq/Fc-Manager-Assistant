"""Portable entry point, executed by the bundled isolated Windows interpreter."""
import ctypes
import os
import runpy
import sys
from ctypes import wintypes
from pathlib import Path


def wait_for_previous_process(pid):
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    api.OpenProcess.restype = wintypes.HANDLE
    api.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    api.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = api.OpenProcess(0x00100000, False, pid)
    if not handle:
        if ctypes.get_last_error() == 87:  # ERROR_INVALID_PARAMETER: already exited
            return
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if api.WaitForSingleObject(handle, 30000) != 0:
            raise RuntimeError("이전 프로그램이 아직 실행 중입니다. 종료 후 다시 실행해 주세요.")
    finally:
        api.CloseHandle(handle)


if __name__ == "__main__":
    try:
        payload = Path(__file__).resolve().parent
        sys.path.insert(0, str(payload))
        os.environ["FC_MANAGER_HOME"] = str(payload.parent.parent)
        os.environ["FC_MANAGER_ASSETS"] = str(payload / "assets")
        if len(sys.argv) == 3 and sys.argv[1] == "--wait-pid":
            wait_for_previous_process(int(sys.argv[2]))
            del sys.argv[1:]
        runpy.run_module("fc_manager", run_name="__main__", alter_sys=True)
    except Exception as error:
        ctypes.windll.user32.MessageBoxW(None, str(error), "FC Manager", 16)
        raise
