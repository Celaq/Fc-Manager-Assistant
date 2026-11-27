"""Capture the FC ONLINE client and send input to a verified foreground window."""

from __future__ import annotations

import ctypes as C
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass

import psutil
from PIL import Image, ImageGrab

from .windows_ocr import read_rank, read_tier


class WindowUnavailable(RuntimeError):
    """The captured client is no longer a safe input destination."""


class _Point(C.Structure):
    _fields_ = [("x", C.c_int32), ("y", C.c_int32)]


class _Message(C.Structure):
    _fields_ = [("hwnd", C.c_void_p), ("message", C.c_uint32), ("wparam", C.c_size_t),
                ("lparam", C.c_ssize_t), ("time", C.c_uint32), ("point", _Point),
                ("private", C.c_uint32)]


class _Rect(C.Structure):
    _fields_ = [(name, C.c_int32) for name in ("left", "top", "right", "bottom")]


class _Mouse(C.Structure):
    _fields_ = [("dx", C.c_int32), ("dy", C.c_int32), ("data", C.c_uint32),
                ("flags", C.c_uint32), ("time", C.c_uint32), ("extra", C.c_size_t)]


class _Keyboard(C.Structure):
    _fields_ = [("vk", C.c_uint16), ("scan", C.c_uint16), ("flags", C.c_uint32),
                ("time", C.c_uint32), ("extra", C.c_size_t)]


class _InputData(C.Union):
    _fields_ = [("mouse", _Mouse), ("keyboard", _Keyboard)]


class _Input(C.Structure):
    _fields_ = [("type", C.c_uint32), ("data", _InputData)]


@dataclass(frozen=True)
class _Client:
    hwnd: int
    pid: int
    left: int
    top: int
    width: int
    height: int


class _Win32:
    def __init__(self):
        if os.name != "nt":
            raise OSError("게임 창 제어는 Windows에서만 사용할 수 있습니다.")
        u = self.user = C.WinDLL("user32", use_last_error=True)
        hwnd, uint, boolean = C.c_void_p, C.c_uint32, C.c_int32
        self.enum_proc = C.WINFUNCTYPE(boolean, hwnd, C.c_ssize_t)
        signatures = {
            "GetForegroundWindow": (hwnd, []),
            "GetWindowThreadProcessId": (uint, [hwnd, C.POINTER(uint)]),
            "IsWindowVisible": (boolean, [hwnd]),
            "IsIconic": (boolean, [hwnd]),
            "ShowWindow": (boolean, [hwnd, C.c_int]),
            "SetForegroundWindow": (boolean, [hwnd]),
            "EnumWindows": (boolean, [self.enum_proc, C.c_ssize_t]),
            "GetClientRect": (boolean, [hwnd, C.POINTER(_Rect)]),
            "ClientToScreen": (boolean, [hwnd, C.POINTER(_Point)]),
            "GetCursorPos": (boolean, [C.POINTER(_Point)]),
            "SetCursorPos": (boolean, [C.c_int, C.c_int]),
            "WindowFromPoint": (hwnd, [_Point]),
            "GetAncestor": (hwnd, [hwnd, uint]),
            "GetAsyncKeyState": (C.c_short, [C.c_int]),
            "RegisterHotKey": (boolean, [hwnd, C.c_int, uint, uint]),
            "UnregisterHotKey": (boolean, [hwnd, C.c_int]),
            "PeekMessageW": (boolean, [C.POINTER(_Message), hwnd, uint, uint, uint]),
            "SendInput": (uint, [uint, C.POINTER(_Input), C.c_int]),
            "mouse_event": (None, [uint, uint, uint, uint, C.c_size_t]),
        }
        for name, (result, args) in signatures.items():
            fn = getattr(u, name)
            fn.restype, fn.argtypes = result, args
        try:
            aware = u.SetProcessDpiAwarenessContext
            aware.argtypes, aware.restype = [C.c_void_p], boolean
            if aware(C.c_void_p(-4)) or C.get_last_error() == 5:
                return
        except AttributeError:
            pass
        u.SetProcessDPIAware()

    def foreground(self):
        return self.user.GetForegroundWindow()

    def pid(self, hwnd):
        pid = C.c_uint32()
        self.user.GetWindowThreadProcessId(hwnd, C.byref(pid))
        return pid.value

    @staticmethod
    def process_name(pid):
        try:
            return psutil.Process(pid).name().casefold() if pid else ""
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            return ""

    @staticmethod
    def game_pids():
        return {p.pid for p in psutil.process_iter(["name"])
                if (p.info["name"] or "").casefold() == "fczf.exe"}

    def client(self, game_only=True):
        hwnd = self.foreground()
        if not hwnd or not self.user.IsWindowVisible(hwnd) or self.user.IsIconic(hwnd):
            return None
        pid = self.pid(hwnd)
        if not pid or (game_only and self.process_name(pid) != "fczf.exe"):
            return None
        rect, origin = _Rect(), _Point()
        if not self.user.GetClientRect(hwnd, C.byref(rect)) or not self.user.ClientToScreen(hwnd, C.byref(origin)):
            return None
        width, height = rect.right - rect.left, rect.bottom - rect.top
        if width < 640 or height < 360:
            return None
        return _Client(hwnd, pid, origin.x, origin.y, width, height)

    def find_game_window(self):
        pids = self.game_pids()
        if not pids:
            return None
        candidates = []

        @self.enum_proc
        def visit(hwnd, _):
            if self.user.IsWindowVisible(hwnd) and self.pid(hwnd) in pids:
                rect = _Rect()
                if self.user.GetClientRect(hwnd, C.byref(rect)):
                    candidates.append((rect.right * rect.bottom, hwnd))
            return True

        self.user.EnumWindows(visit, 0)
        return max(candidates)[1] if candidates else None

    def activate(self, hwnd):
        if self.user.IsIconic(hwnd):
            self.user.ShowWindow(hwnd, 9)  # SW_RESTORE
        self.user.SetForegroundWindow(hwnd)
        return self.foreground() == hwnd

    def cursor(self):
        point = _Point()
        if not self.user.GetCursorPos(C.byref(point)):
            raise C.WinError(C.get_last_error())
        return point.x, point.y

    def move(self, x, y):
        if not self.user.SetCursorPos(x, y):
            raise C.WinError(C.get_last_error())

    def window_at(self, point):
        hwnd = self.user.WindowFromPoint(_Point(*point))
        return self.user.GetAncestor(hwnd, 2) if hwnd else None  # GA_ROOT

    def key_state(self, vk):
        return self.user.GetAsyncKeyState(vk)

    def register_f8(self):
        return bool(self.user.RegisterHotKey(None, 0x4FC8, 0x4000, 0x77))  # MOD_NOREPEAT

    def unregister_f8(self):
        self.user.UnregisterHotKey(None, 0x4FC8)

    def consume_registered_f8(self):
        # Only this thread owns the registration and its windowless message queue.
        msg = _Message()
        pressed = False
        while self.user.PeekMessageW(C.byref(msg), None, 0x0312, 0x0312, 1):  # WM_HOTKEY, PM_REMOVE
            if msg.wparam == 0x4FC8:
                pressed = True
        return pressed

    def key_event(self, code, *, virtual, release):
        event = _Input(type=1)
        event.data.keyboard = _Keyboard(vk=code if virtual else 0, scan=0 if virtual else code,
                                        flags=(0 if virtual else 8) | (2 if release else 0))
        if self.user.SendInput(1, C.byref(event), C.sizeof(_Input)) != 1:
            raise C.WinError(C.get_last_error())

    def click_pair(self):
        pair = (_Input * 2)()
        pair[0].data.mouse.flags, pair[1].data.mouse.flags = 2, 4
        return self.user.SendInput(2, pair, C.sizeof(_Input))

    def mouse_event(self, flag):
        self.user.mouse_event(flag, 0, 0, 0, 0)


class _F8Hotkey:
    """Own a message queue that Tk's event pump cannot drain."""

    def __init__(self, native):
        self.native = native
        self.ready = threading.Event()
        self.stop = threading.Event()
        self.pending = threading.Event()
        self.lock = threading.Lock()
        self.registered = False
        self.thread = threading.Thread(target=self._run, name="FC-F8-hotkey", daemon=True)
        self.thread.start()
        self.ready.wait()

    def _run(self):
        try:
            self.registered = self.native.register_f8()
        finally:
            self.ready.set()
        if not self.registered:
            return
        try:
            while not self.stop.is_set():
                if self.native.consume_registered_f8():
                    with self.lock:
                        self.pending.set()
                self.stop.wait(0.020)
        finally:
            self.native.unregister_f8()

    def consume(self):
        with self.lock:
            pressed = self.pending.is_set()
            self.pending.clear()
            return pressed

    def close(self):
        self.stop.set()
        self.thread.join()


class WindowsGame:
    """Keep captures and their coordinate systems local to each calling thread."""

    read_rank = staticmethod(read_rank)
    read_tier = staticmethod(read_tier)

    def __init__(self, stop_requested=None):
        self._native = _Win32()
        self._frame = threading.local()
        self.stop_requested = stop_requested
        self._hotkeys_down = set()
        self._f12_pending = False
        self._key_lock = threading.Lock()
        self._f8 = _F8Hotkey(self._native)

    def close(self):
        self._f8.close()

    def game_present(self):
        return bool(self._native.game_pids())

    def foreground_game(self):
        return self._native.client() is not None

    def _focus(self, maintenance):
        if self.foreground_game():
            return True
        hwnd = self._native.find_game_window()
        if not hwnd:
            return False
        if maintenance:
            pid = self._native.pid(self._native.foreground())
            if pid != os.getpid() and self._native.process_name(pid) != "nexonplug.exe":
                return False
        return self._native.activate(hwnd) and self.foreground_game()

    def focus_game(self):
        return self._focus(maintenance=False)

    def focus_game_for_maintenance(self):
        return self._focus(maintenance=True)

    def _poll_one(self, name, vk):
        # The high bit covers a held key. The low bit can also catch a tap
        # completed between two polls. Windows may clear it for another app.
        state = self._native.key_state(vk)
        down = bool(state & 0x8000)
        pressed = (down or bool(state & 1)) and name not in self._hotkeys_down
        if down:
            self._hotkeys_down.add(name)
        else:
            self._hotkeys_down.discard(name)
        return pressed, down

    def poll_hotkeys(self):
        with self._key_lock:
            events = {name for name, vk in (("F9", 0x78), ("F12", 0x7B))
                      if self._poll_one(name, vk)[0]}
            f8 = self._f8.consume() if self._f8.registered else self._poll_one("F8", 0x77)[0]
            if f8:
                events.add("F8")
            if self._f12_pending:
                events.add("F12")
                self._f12_pending = False
        return events

    def f12_pressed(self):
        with self._key_lock:
            pressed, _ = self._poll_one("F12", 0x7B)
            pressed = pressed or self._f12_pending
            self._f12_pending = False
            return pressed

    @contextmanager
    def manager_frame_scope(self, game_only: bool = True):
        """Apply the user's foreground-window choice only during this manager tick."""
        previous = getattr(self._frame, "other_windows", False)
        self._frame.other_windows = game_only is False
        self._frame.client = None
        try:
            yield
        finally:
            self._frame.client = None
            self._frame.other_windows = previous

    def _current_client(self):
        allow_other = getattr(self._frame, "other_windows", False) and not getattr(self._frame, "owner", None)
        return self._native.client(game_only=not allow_other)

    def captured_game(self):
        client = getattr(self._frame, "client", None)
        return client is not None and client == self._native.client()

    @contextmanager
    def pin_client(self):
        """Keep a paid workflow attached to the original FC window and process."""
        client = self._native.client()
        if client is None:
            raise WindowUnavailable("게임 창을 확인할 수 없습니다.")
        previous = getattr(self._frame, "owner", None)
        identity = (client.hwnd, client.pid)
        if previous is not None and previous != identity:
            raise WindowUnavailable("처음 선택한 FC ONLINE 창이 변경되었습니다.")
        self._frame.owner = identity
        self._frame.client = None
        try:
            yield
        finally:
            self._frame.client = None
            self._frame.owner = previous

    def capture(self) -> Image.Image:
        self._frame.client = None
        client = self._current_client()
        if client is None:
            raise WindowUnavailable("FC ONLINE 게임 창을 앞으로 가져오세요.")
        owner = getattr(self._frame, "owner", None)
        if owner is not None and owner != (client.hwnd, client.pid):
            raise WindowUnavailable("처음 선택한 FC ONLINE 창이 변경되었습니다.")
        bounds = (client.left, client.top, client.left + client.width, client.top + client.height)
        image = ImageGrab.grab(bbox=bounds, all_screens=True).convert("RGB")
        if self._current_client() != client or image.size != (client.width, client.height):
            raise WindowUnavailable("화면 캡처 중 게임 창이 변경되었습니다.")
        self._frame.client = client
        return image

    def _check(self):
        with self._key_lock:
            pressed, down = self._poll_one("F12", 0x7B)
            self._f12_pending |= pressed
            stopped = self._f12_pending or down
        if stopped or (self.stop_requested and self.stop_requested()):
            self._frame.client = None
            raise WindowUnavailable("중지 요청으로 게임 입력을 취소했습니다.")
        client = getattr(self._frame, "client", None)
        if client is None or self._current_client() != client:
            self._frame.client = None
            raise WindowUnavailable("게임 창 위치, 크기 또는 포커스가 변경되었습니다.")
        return client

    def _point(self, x, y):
        client = self._check()
        if not (0 <= x < 960 and 0 <= y < 540):
            raise ValueError("게임 입력 좌표가 960×540 기준 화면 밖에 있습니다.")
        sx = client.left + min(client.width - 1, round(x * client.width / 960))
        sy = client.top + min(client.height - 1, round(y * client.height / 540))
        point = sx, sy
        if self._native.window_at(point) != client.hwnd:
            raise WindowUnavailable("게임 입력 위치가 다른 창에 가려져 있습니다.")
        return point

    def move(self, x, y):
        self._native.move(*self._point(x, y))

    def click(self, x, y):
        point = self._point(x, y)
        client = self._frame.client
        old = self._native.cursor()
        self._native.move(*point)
        try:
            time.sleep(0.06)
            if self._point(x, y) != point or self._native.cursor() != point:
                raise WindowUnavailable("클릭 전 게임 입력 위치가 변경되었습니다.")
            sent = self._native.click_pair()
            if sent == 1:
                # A mouse down was delivered; always release it once.
                self._native.mouse_event(4)
            elif sent == 0:
                if self._point(x, y) != point or self._native.cursor() != point:
                    raise WindowUnavailable("호환 클릭 전 게임 입력 위치가 변경되었습니다.")
                self._native.mouse_event(2)
                try:
                    time.sleep(0.08)
                finally:
                    self._native.mouse_event(4)
            time.sleep(0.08)
        finally:
            if self._current_client() == client and self._native.cursor() == point:
                self._native.move(*old)

    def _pulse(self, code, duration, virtual=False):
        self._check()
        self._native.key_event(code, virtual=virtual, release=False)
        try:
            time.sleep(duration)
        finally:
            self._native.key_event(code, virtual=virtual, release=True)

    def key(self, name):
        name = name.lower()
        if name == "s":
            self._pulse(0x1F, 0.140)
            time.sleep(0.080)
            self._pulse(0x53, 0.100, virtual=True)
        else:
            scans = {"space": 0x39, "escape": 0x01, "esc": 0x01, "enter": 0x1C}
            if name not in scans:
                raise ValueError(f"지원하지 않는 게임 키: {name}")
            self._pulse(scans[name], 0.065)
