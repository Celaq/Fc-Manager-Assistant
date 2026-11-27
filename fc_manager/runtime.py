"""Application paths and the thread boundary between automation and the desktop UI."""
import os
import platform
import sys
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from zipfile import ZipFile

from . import __version__


@contextmanager
def assets_directory(explicit=None):
    directory = explicit or os.environ.get("FC_MANAGER_ASSETS")
    if directory:
        path = Path(directory)
        if not all((path / folder).is_dir() for folder in ("templates", "templates_2560")):
            raise ValueError("templates와 templates_2560이 있는 설치 폴더를 선택하세요.")
        yield path
        return
    # Source checkout uses the exact assets that shipped with the baseline.
    archive = Path(__file__).resolve().parents[1] / "releases" / "FC_Manager_v3.3.4_Coach_Recruitment_Beta.zip"
    with tempfile.TemporaryDirectory(prefix="fc-templates-") as temp, ZipFile(archive) as zip_file:
        path = Path(temp)
        for item in zip_file.infolist():
            parts = item.filename.split("/")
            if (len(parts) == 3 and parts[0] == "FC_Manager_v3.3.4"
                    and parts[1] in ("templates", "templates_2560") and parts[2].endswith(".png")
                    and Path(parts[2]).name == parts[2] and "\\" not in parts[2]):
                target = path / parts[1] / parts[2]
                target.parent.mkdir(exist_ok=True)
                target.write_bytes(zip_file.read(item))
        yield path


def data_directory(explicit=None):
    if explicit:
        return Path(explicit).expanduser().resolve()
    home = os.environ.get("FC_MANAGER_HOME")
    if home:
        return Path(home).resolve()
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "FC-Manager-Assistant"


@contextmanager
def single_instance(data):
    """A second process must not issue competing inputs or update the same stats."""
    data.mkdir(parents=True, exist_ok=True)
    if sys.platform != "win32":
        yield
        return
    import ctypes
    from ctypes import wintypes
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    api.CreateMutexW.restype = wintypes.HANDLE
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    # Input goes to the same desktop even when two installations use separate data folders.
    handle = api.CreateMutexW(None, False, r"Local\FCManagerPythonDesktop")
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if ctypes.get_last_error() == 183:
            raise RuntimeError("이 Windows 세션에 FC Manager가 이미 실행 중입니다.")
        yield
    finally:
        api.CloseHandle(handle)


class Context:
    def __init__(self, game, assets, data, events, cancel):
        self.game, self.assets, self.data = game, assets, data
        self.events, self.cancel = events, cancel
        self.last_size = None
        self.summary = lambda: ""
        self.diagnostic = data / "오류확인용_보내기"
        for folder in ("captures", "logs"):
            (self.diagnostic / folder).mkdir(parents=True, exist_ok=True)
        self.log_lock = threading.Lock()
        self.game.stop_requested = self.stop_requested

    def stop_requested(self):
        return self.cancel.is_set()

    def capture(self):
        image = self.game.capture()
        if image is not None:
            self.last_size = image.size
        return image

    def key(self, name):
        if self.stop_requested():
            raise InterruptedError("중지됨")
        return self.game.key(name)

    def click(self, x, y):
        if self.stop_requested():
            raise InterruptedError("중지됨")
        return self.game.click(x, y)

    def move(self, x, y):
        if self.stop_requested():
            raise InterruptedError("중지됨")
        return self.game.move(x, y)

    def log(self, message):
        now = datetime.now()
        line = f"[{now:%H:%M:%S}] {message}"
        try:
            with self.log_lock:
                with (self.diagnostic / "logs" / f"FCManager_{now:%Y%m%d}.log").open("a", encoding="utf-8-sig") as file:
                    file.write(line + "\n")
        except OSError:
            line += " · 로그 파일 쓰기 실패"
        self.events.put(("log", line))

    def notify(self, title, message):
        self.events.put(("notify", (title, message)))

    def report(self, reason, image=None):
        text = (f"FC Manager Python {__version__}\n생성 시각 {datetime.now():%Y-%m-%d %H:%M:%S}\n"
                f"마지막 상태 {reason}\nWindows {platform.version()}\nPython {platform.python_version()}\n")
        if self.last_size:
            text += f"최근 캡처 {self.last_size[0]}×{self.last_size[1]}\n"
        if sys.platform == "win32":
            import ctypes
            user32 = ctypes.windll.user32
            text += f"주 모니터 {user32.GetSystemMetrics(0)}×{user32.GetSystemMetrics(1)}\n"
            if hasattr(user32, "GetDpiForSystem"):
                text += f"시스템 DPI {user32.GetDpiForSystem()}\n"
        text += self.summary()
        path = image
        try:
            (self.diagnostic / "진단정보.txt").write_text(text, encoding="utf-8-sig")
            if hasattr(image, "save"):
                path = self.diagnostic / "captures" / "last-error.png"
                image.save(path)
        except OSError as exc:
            self.log(f"진단 자료 저장 실패 · {exc}")
            if hasattr(image, "save"):
                path = None
        self.events.put(("report", (reason, path)))

    def save_capture(self):
        image = self.capture()
        path = self.diagnostic / "captures" / f"FC_{datetime.now():%Y%m%d_%H%M%S_%f}.png"
        image.save(path)
        self.log(f"화면 저장 · {path.name}")
        return path
