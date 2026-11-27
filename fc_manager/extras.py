"""Screen-confirmed club, mission, BP protection, and scheduled start workflows."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import monotonic, sleep

import numpy as np
from PIL import Image

from .extras_launcher import invoke_nexon_start

KST = timezone(timedelta(hours=9))
CLUB = {
    "LobbyHub": (452, 29, 17), "LobbyHubClose": (818, 27, 20),
    "LobbyHubCenter": (588, 235, 15), "LobbyClaimEnabled": (698, 96, 13),
    "LobbyClaimDisabled": (693, 94, 5), "MissionNav": (211, 520, 16),
    "Hub": (451, 40, 16), "HubClose": (818, 41, 16), "HubCenter": (529, 236, 11),
    "ClaimEnabled": (701, 110, 13), "ClaimDisabled": (701, 110, 13),
    "Lobby": (120, 63, 20), "Exit": (816, 27, 29), "Menu": (160, 153, 34),
    "ClubTab": (598, 521, 25), "Club": (465, 27, 30), "Enabled": (752, 287, 21),
    "Disabled": (752, 287, 17), "Zero": (678, 465, 15), "Close": (819, 27, 30),
    "Reward": (459, 145, 18), "Confirm": (504, 383, 20),
}
BP = {
    "Open": (492, 472, 12), "Label": (139, 343, 8), "Applied": (313, 340, 9),
    "Tab": (438, 103, 12), "ZeroTab": (438, 103, 12), "Slider": (436, 276, 11),
    "Max": (637, 295, 10), "Empty": (692, 326, 10), "Checked": (692, 326, 12),
    "Confirm": (526, 417, 12),
}


class Markers:
    """Test named 3.3.4 markers within their small client-coordinate search regions."""

    def __init__(self, assets, group):
        self.directory = Path(assets) / "templates"
        self.group = group
        self.regions, self.margin = (BP, 2) if group == "bp" else (CLUB, 3)
        self.templates = {}

    def read(self, image):
        if image is None or image.width < 640 or abs(image.width / image.height - 16 / 9) > .035:
            return None
        height = round(image.height * 960 / image.width)
        image = image.convert("RGB").resize((960, height), Image.Resampling.BICUBIC)
        frame = np.asarray(image, dtype=np.int16)
        readings = {}

        def present(name):
            if name not in readings:
                readings[name] = self._find(frame, name)
            return readings[name]

        return present

    def _find(self, frame, name):
        if name not in self.templates:
            with Image.open(self.directory / f"{self.group}_{name}.png") as image:
                self.templates[name] = np.asarray(image.convert("RGB"), dtype=np.int16)
        template = self.templates[name]
        x, y, threshold = self.regions[name]
        h, w = template.shape[:2]
        margin = self.margin
        # The original search rectangle uses 960x540 fractions even for a
        # slightly taller/shorter 16:9-compatible client.
        top = max(0, int(np.floor((y - margin) * frame.shape[0] / 540)))
        bottom = min(frame.shape[0] - h, int(np.ceil((y + h + margin) * frame.shape[0] / 540)) - h)
        left, right = max(0, x - margin), min(960 - w, x + margin)
        sy, sx = max(2, h // 6), max(2, w // 12)
        sample = template[::sy, ::sx]

        def difference(px, py):
            return int(np.abs(frame[py:py+h:sy, px:px+w:sx] - sample).sum())

        best = int(np.ceil(threshold * 2.5 * sample.size))
        location = None
        for py in range(top, bottom + 1, 2):
            for px in range(left, right + 1, 2):
                score = difference(px, py)
                if score < best:
                    best, location = score, (px, py)
        if location is None:
            return False
        bx, by = location
        for py in range(max(top, by-3), min(bottom, by+3) + 1):
            for px in range(max(left, bx-3), min(right, bx+3) + 1):
                score = difference(px, py)
                if score < best:
                    best, location = score, (px, py)
        px, py = location
        return np.abs(frame[py:py+h:2, px:px+w:2] - template[::2, ::2]).mean() <= threshold


def reward_window(now):
    local = now.astimezone(KST)
    return local.hour == 23 and local.minute >= 30


def _club_step(stage, mark):
    if stage in ("Lobby", "Home") and mark("Lobby") and mark("Exit"):
        return "done" if stage == "Home" else ("Open", 827, 37)
    if stage in ("Open", "Return") and mark("Menu") and mark("ClubTab"):
        return ("Donate", 604, 525) if stage == "Open" else ("Home", 208, 161)
    if stage == "Verify" and mark("Reward") and mark("Confirm"):
        return ("Finish", 543, 390)
    if stage in ("Donate", "Verify", "Finish") and mark("Club"):
        if mark("Disabled") and mark("Zero") and mark("Close"):
            return ("Return", 827, 37)
        if stage == "Donate" and mark("Enabled") and not mark("Zero"):
            return ("Verify", 782, 294)
    return None


def _mission_step(stage, mark):
    if stage == "Open" and mark("Lobby") and mark("MissionNav"):
        return ("Claim", 228, 525)
    if stage in ("Claim", "Verify"):
        lobby = mark("LobbyHub") and mark("LobbyHubCenter") and mark("LobbyHubClose")
        if not lobby and not (mark("Hub") and mark("HubCenter") and mark("HubClose")):
            return None
        prefix = "Lobby" if lobby else ""
        enabled, disabled = mark(prefix + "ClaimEnabled"), mark(prefix + "ClaimDisabled")
        if enabled and not disabled and stage == "Claim":
            return ("Verify", 740, 106 if lobby else 119)
        if disabled and not enabled:
            return ("Return", 827, 37 if lobby else 50)
    if stage in ("Home", "Return") and not (mark("Hub") or mark("LobbyHub")):
        if mark("Lobby"):
            return "done"
        if stage == "Return" and mark("Menu") and mark("ClubTab"):
            return ("Home", 208, 161)
    return None


def _recovery_step(mark):
    if mark("Confirm") or mark("Reward"):
        return None
    if mark("LobbyHub") and mark("LobbyHubClose") and mark("LobbyHubCenter"):
        return ("CloseLobbyHub", 827, 37)
    if mark("Hub") and mark("HubClose") and mark("HubCenter"):
        return ("CloseMatchHub", 827, 50)
    if mark("LobbyHub") or mark("Hub"):
        return None
    if mark("Club"):
        return ("CloseClub", 827, 37) if mark("Close") else None
    if mark("Lobby") and mark("Exit"):
        return "done"
    if mark("Menu") and mark("ClubTab"):
        return ("ReturnManager", 208, 161)
    return None


def _utcnow():
    return datetime.now(timezone.utc)


class RewardFlow:
    """A daily check is reserved atomically before entering the reward screen."""

    def __init__(self, ctx, *, clock=monotonic, now=_utcnow):
        self.ctx, self.clock, self.now = ctx, clock, now
        self.markers = Markers(ctx.assets, "club")
        self.reset()

    def reset(self):
        self.after_match = None
        self.pending = []
        self.kind = self.stage = self.last_recovery = ""
        self.recovered_at_match = None
        self.manual = False
        self.deadline = self.next_probe = 0.0
        self.focus_lost = False
        self.resume_at = None

    @property
    def busy(self):
        return bool(self.kind)

    def toggle_next(self, match_count):
        if self.busy or self.pending:
            return False
        self.after_match = match_count + 1 if self.after_match is None else None
        return self.after_match is not None

    def _reserve(self, kind, now):
        if not reward_window(now):
            return False
        directory = Path(self.ctx.data) / ".reward-checks"
        day = now.astimezone(KST).date()
        try:
            directory.mkdir(parents=True, exist_ok=True)
            (directory / f"{day}-{kind}").touch(exist_ok=False)
        except FileExistsError:
            return False
        except OSError as exc:
            self.ctx.log(f"보상 확인 기록 저장 실패 ({kind}): {exc}")
            return False
        return True

    def start_if_due(self, match_count, *, club, mission, blocked=False):
        if blocked or self.busy or self.recovered_at_match == match_count:
            return False
        if self.after_match is not None and match_count >= self.after_match:
            self.after_match = None
            self.pending = ["mission", "club"]
        now = self.now()
        self.manual = bool(self.pending)
        if self.pending:
            self.kind = self.pending.pop(0)
            self._reserve(self.kind, now)
        elif mission and self._reserve("mission", now):
            self.kind = "mission"
        elif club and self._reserve("club", now):
            self.kind = "club"
        else:
            return False
        self.stage = "Open" if self.kind == "mission" else "Lobby"
        self.deadline = self.clock() + 15
        self.next_probe = 0
        self.ctx.log("미션 허브 보상 확인 중" if self.kind == "mission" else "클럽 기부 확인 중")
        return True

    def _recover(self, reason):
        label = "미션 허브" if self.kind == "mission" else "클럽 기부"
        message = f"{self.stage} · {reason} · 감독모드 로비 복귀 시도"
        self.ctx.log(f"{label} 확인 실패 · {message}")
        self.ctx.report(f"{label} 확인 실패 · {self.stage} · {reason}")
        self.ctx.notify(f"{label} 확인 실패", message)
        self.kind = "recovery"
        self.last_recovery = ""
        self.deadline = self.clock() + 15
        self.next_probe = 0

    def _mission_diagnostic(self, image):
        try:
            if image is not None:
                directory = Path(self.ctx.data) / "오류확인용_보내기" / "captures"
                directory.mkdir(parents=True, exist_ok=True)
                name = f"mission_{self.now():%Y%m%d_%H%M%S_%f}_{self.stage}.png"
                image.save(directory / name)
                self.ctx.log(f"미션 허브 진단 화면 저장: {name}")
        except (OSError, RuntimeError, ValueError) as exc:
            self.ctx.log(f"미션 허브 진단 화면 저장 실패: {exc}")

    def tick(self, match_count):
        if not self.busy:
            return "idle"
        clock = self.clock()
        if self.kind == "recovery" and clock >= self.deadline:
            self.ctx.report("감독모드 로비 복귀 실패")
            self.ctx.notify("감독모드 로비 복귀 실패", "15초 안에 로비로 복귀하지 못해 자동 진행을 중지합니다.")
            self.ctx.log("보상 확인 후 감독모드 복귀 실패")
            self.reset()
            return "failed"
        if clock < self.next_probe or self.ctx.stop_requested():
            return "busy"
        self.next_probe = clock + .65
        try:
            image = self.ctx.capture()
        except (OSError, RuntimeError):
            image = None
        if image is None:
            self.focus_lost = True
            self.resume_at = None
            return "busy"
        if self.focus_lost:
            if self.resume_at is None:
                self.resume_at = clock + 1.5
            if clock < self.resume_at:
                return "busy"
            self.focus_lost = False
            self.resume_at = None
        if self.kind != "recovery" and clock >= self.deadline:
            if self.kind == "mission":
                self._mission_diagnostic(image)
            self._recover("화면 확인 제한시간 초과")
            return "busy"
        mark = self.markers.read(image)
        if mark is None:
            return "busy"
        if self.kind == "recovery":
            action = _recovery_step(mark)
        elif self.kind == "club":
            action = _club_step(self.stage, mark)
        else:
            action = _mission_step(self.stage, mark)
        if action == "done":
            if self.kind == "recovery":
                self.recovered_at_match = match_count
            self.kind = self.stage = ""
            self.ctx.log("감독모드 로비 복귀 확인")
            return "done"
        if not action:
            return "busy"
        stage, x, y = action
        if self.kind == "recovery" and stage == self.last_recovery:
            return "busy"
        if stage == "Verify" and not self.manual and not reward_window(self.now()):
            self._recover("자동 보상 확인 시간 종료")
            return "busy"
        if self.ctx.stop_requested():
            return "busy"
        # Enter Verify before issuing any claim or donation. A delayed click
        # must never cause a second attempt.
        previous = self.stage
        if self.kind != "recovery":
            self.stage = stage
            self.deadline = clock + 15
        self.next_probe = clock + .9
        try:
            clicked = self.ctx.click(x, y) is not False
        except (RuntimeError, OSError):
            clicked = False
        if self.kind == "recovery":
            if clicked:
                self.last_recovery = stage
        elif not clicked:
            self.stage = previous
            self._recover("화면 입력 실패")
        return "busy"


def _bp_step(stage, mark):
    if mark("Slider") and (mark("Tab") or mark("ZeroTab")):
        if stage == "Verify":
            return None
        if not mark("Max"):
            return ("MaxRequested", 610, 305) if stage in ("Start", "Opened") else None
        if mark("Checked") and mark("Confirm"):
            return ("Verify", 548, 426)
        if mark("Empty") and not mark("Checked") and stage != "Checked":
            return ("Checked", 698, 333)
    elif mark("Open") and mark("Label"):
        if stage == "Start":
            return ("Opened", 516, 477)
        if stage == "Verify" and mark("Applied"):
            return "done"
    return None


class BPProtection:
    def __init__(self, ctx, *, clock=monotonic, pause=sleep):
        self.ctx, self.clock, self.pause = ctx, clock, pause
        self.markers = Markers(ctx.assets, "bp")

    def _fail(self, message):
        self.ctx.log(message)
        self.ctx.report(message)
        return False

    def run(self):
        stage = "Start"
        deadline = self.clock() + 8
        self.pause(.5)
        while not self.ctx.stop_requested():
            if self.clock() >= deadline:
                return self._fail(f"강화 보호 중지 · 화면 확인 시간 초과 ({stage})")
            try:
                mark = self.markers.read(self.ctx.capture())
                if mark is None:
                    return self._fail("강화 보호 중지 · FC 온라인 16:9 게임 화면을 확인하세요.")
                action = _bp_step(stage, mark)
                if action == "done":
                    self.ctx.log("BP 강화 등급 보호 100% 적용 확인")
                    return True
                if not action:
                    self.pause(.3)
                    continue
                stage, x, y = action
                if self.ctx.stop_requested():
                    break
                if self.ctx.click(x, y) is False:
                    return self._fail("강화 보호 중지 · 게임 입력 실패")
            except InterruptedError:
                break
            except (OSError, RuntimeError) as exc:
                return self._fail(f"강화 보호 중지 · {exc}")
            deadline = self.clock() + 8
            self.pause(.55)
        self.ctx.log("BP 강화 보호 중지")
        return False


def _maintenance_view(mark):
    if mark is None or any(mark(key) for key in ("Club", "Confirm", "Reward", "Hub", "LobbyHub")):
        return None
    if mark("Lobby") and mark("Exit"):
        return "Lobby"
    if mark("Menu") and mark("ClubTab"):
        return "Menu"
    return None


class Maintenance:
    """Reserve a six-hour start window. The manager verifies parking rank before complete()."""

    def __init__(self, ctx, *, game_present, launch=None, focus_game=None, now=_utcnow):
        self.ctx, self.now, self.game_present = ctx, now, game_present
        self.launch = launch or (lambda: invoke_nexon_start(self._launch_blocked))
        self.focus_game = focus_game
        self.markers = Markers(ctx.assets, "club")
        self.start_at = None
        self.interrupted = False
        self.status = "예약 없음"

    @property
    def armed(self):
        return self.start_at is not None

    def _launch_blocked(self):
        return not self.armed or self.ctx.stop_requested() or self.game_present()

    def arm(self, kst_datetime, running=False):
        if self.armed:
            raise ValueError("이미 자동 시작이 예약되어 있습니다.")
        selected = kst_datetime
        if selected.tzinfo is None:
            selected = selected.replace(tzinfo=KST)
        start = selected.astimezone(timezone.utc).replace(second=0, microsecond=0)
        if start + timedelta(minutes=1) <= self.now():
            raise ValueError("현재 또는 이후의 점검 종료 예정 시각을 입력하세요.")
        self.interrupted = bool(running and not self.game_present())
        self.launch_error_logged = False
        self.next_probe = self.next_launch = self.next_click = start
        self.start_at = start
        self.status = f"예약됨 · 한국 시간 {start.astimezone(KST):%Y-%m-%d %H:%M}"

    def cancel(self):
        interrupted = self.interrupted
        self.start_at = None
        self.interrupted = False
        self.status = "자동 시작 예약이 취소되었습니다."
        return interrupted

    def complete(self):
        if self.armed:
            self.start_at = None
            self.interrupted = False
            self.status = "감독모드 로비 확인 · 자동 진행 시작"
            self.ctx.log(self.status)
            self.ctx.notify("점검 후 감독모드 시작", self.status)

    def tick(self, running=False):
        if not self.armed:
            return "wait"
        now = self.now()
        if now >= self.start_at + timedelta(hours=6):
            interrupted = self.cancel()
            self.status = "예약 만료 · 감독모드 로비를 확인하지 못했습니다."
            self.ctx.log(self.status)
            self.ctx.report("점검 후 자동 시작 실패")
            self.ctx.notify("점검 후 자동 시작 실패", self.status)
            return "stop" if interrupted else "expired"
        if now < self.next_probe:
            return "busy" if self.interrupted else "wait"
        self.next_probe = now + timedelta(seconds=2)
        if not self.game_present():
            if now >= self.next_launch:
                self.next_launch = now + timedelta(seconds=20)
                try:
                    if self.launch():
                        self.next_launch = now + timedelta(minutes=2)
                        self.interrupted = running
                        self.status = "FC 온라인 실행 요청 · 게임 창 대기 중"
                        self.ctx.log(self.status)
                    else:
                        self.status = "넥슨플러그 FC 온라인 게임시작 버튼 활성화 대기 중"
                except Exception as exc:  # UI Automation errors differ between providers.
                    self.status = "넥슨플러그 버튼 확인 실패 · 다시 확인합니다."
                    if not self.launch_error_logged:
                        self.ctx.log(f"{self.status} ({type(exc).__name__})")
                        self.launch_error_logged = True
            return "busy"

        try:
            image = self.ctx.capture()
        except (OSError, RuntimeError):
            image = None
        if image is None and self.focus_game:
            # The supplied focus hook only foregrounds FC from the assistant or Nexon Plug.
            self.focus_game()
        view = _maintenance_view(self.markers.read(image))
        if view == "Lobby":
            return "resume"
        if view == "Menu":
            if now >= self.next_click:
                self.next_click = now + timedelta(seconds=15)
                self.interrupted = running
                try:
                    self.ctx.click(208, 161)
                except (OSError, RuntimeError):
                    pass
                self.status = "감독모드 메뉴 진입 · 로비 확인 중"
            return "busy"
        self.status = "FC 온라인 실행됨 · 감독모드 로비 확인 중"
        return "busy" if self.interrupted or not running else "wait"
