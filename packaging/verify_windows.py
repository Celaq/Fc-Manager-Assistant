"""Exercise the built ZIP offline in a temporary directory on a Windows desktop."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import zipfile


def main():
    if os.name != "nt":
        raise SystemExit("Run this smoke test on Windows x64.")
    from ctypes import POINTER, WinDLL, byref, wintypes
    directory = Path(sys.argv[1])
    manifest = json.loads((directory / "latest-python.json").read_text(encoding="utf8"))
    archive = directory / manifest["url"].rsplit("/", 1)[-1]
    with archive.open("rb") as file:
        if hashlib.file_digest(file, "sha256").hexdigest() != manifest["sha256"]:
            raise ValueError("Package digest mismatch.")
    with tempfile.TemporaryDirectory() as target:
        root = Path(target)
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(root)
        payload = root / "payloads" / manifest["version"]
        python = str(payload / "runtime/python.exe")
        dependencies = (
            "import cv2, numpy, psutil, tkinter, pywinauto; from PIL import ImageGrab; "
            "from winrt.windows.media.ocr import OcrEngine; "
            "from winrt.windows.graphics.imaging import SoftwareBitmap; "
            "from winrt.windows.storage.streams import DataWriter; "
            "from winrt.windows.globalization import Language; "
            "t=tkinter.Tk(); t.update(); t.destroy(); print('Windows runtime imports OK')"
        )
        subprocess.run([python, "-I", "-B", "-c", dependencies], cwd=root, check=True)
        subprocess.run([python, "-I", "-B", str(payload / "launch.py"), "--version"], check=True)
        # Exercise the same launcher a user double-clicks, including a normal window close.
        ui = WinDLL("user32", use_last_error=True)
        ui.FindWindowW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR)
        ui.FindWindowW.restype = wintypes.HWND
        ui.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        ui.GetWindowThreadProcessId.argtypes = (wintypes.HWND, POINTER(wintypes.DWORD))
        api = WinDLL("kernel32", use_last_error=True)
        api.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        api.OpenProcess.restype = wintypes.HANDLE
        api.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        api.CloseHandle.argtypes = (wintypes.HANDLE,)
        title = f"FC Manager {manifest['version']} · Python"
        if ui.FindWindowW(None, title):
            raise RuntimeError("Close the running FC Manager before starting this smoke test.")
        subprocess.run(["cmd.exe", "/d", "/c", "Start_FC_Manager.cmd"], cwd=root, check=True, timeout=20)
        deadline = time.monotonic() + 40
        while not (window := ui.FindWindowW(None, title)):
            if time.monotonic() >= deadline:
                raise RuntimeError("The portable app did not open its main window.")
            time.sleep(0.2)
        pid = wintypes.DWORD()
        ui.GetWindowThreadProcessId(window, byref(pid))
        handle = api.OpenProcess(0x00100000, False, pid.value)  # SYNCHRONIZE
        if not handle:
            raise RuntimeError("Could not monitor the portable app during shutdown.")
        try:
            ui.PostMessageW(window, 0x0010, 0, 0)  # WM_CLOSE
            if api.WaitForSingleObject(handle, 30000) != 0:
                raise RuntimeError("The portable app did not close.")
        finally:
            api.CloseHandle(handle)
    print("Portable Windows launch and close passed.")


if __name__ == "__main__":
    main()
