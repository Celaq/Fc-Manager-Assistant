"""One automation owner for game input. Tk and Discord submit commands from the UI thread."""
from dataclasses import dataclass
from queue import Empty, Queue
from threading import Thread
from time import monotonic

from .coach import run_coach
from .extras import BPProtection, Maintenance, RewardFlow
from .manager import Manager


@dataclass(frozen=True)
class Options:
    parking: bool = False
    target: int = 4000
    minimum: int | None = None
    limit: int = 0
    club: bool = False
    mission: bool = False
    game_only: bool = True


class Presence:
    def __init__(self, present):
        self.observed = present
        self.missing_since = None

    def exited(self, present, now):
        if present:
            self.observed, self.missing_since = True, None
        elif self.observed:
            if self.missing_since is None:
                self.missing_since = now
            if now - self.missing_since >= 3:
                self.observed = False
                return True
        return False


class Controller:
    def __init__(self, ctx):
        self.ctx, self.game = ctx, ctx.game
        self.thread = None
        self.kind = ""
        self.options = Options()
        self.commands = Queue()
        self.rewards = RewardFlow(ctx)
        self.maintenance = Maintenance(ctx, game_present=self.game.game_present,
                                       focus_game=self.game.focus_game_for_maintenance)
        self.manager = Manager(ctx, on_lobby=self._reward_lobby, read_rank=self.game.read_rank,
                               read_tier=self.game.read_tier)
        self.ctx.summary = self.manager.summary

    @property
    def busy(self):
        return self.thread is not None and self.thread.is_alive()

    def snapshot(self):
        stats = self.manager.stats
        return {"status": self.manager.status, "running": self.manager.running,
                "finish": self.manager.finish_after, "count": self.manager.run_count,
                "limit": self.manager.limit, "samples": stats.point_count,
                "rewards_busy": bool(self.rewards.busy or self.rewards.pending),
                "reward_pending": self.rewards.after_match is not None,
                "rank": self.manager.starting_rank if self.manager.rank_read_at is not None else self.manager.current_rank, "session": dict(stats.session),
                "today": dict(stats.today), "total": dict(stats.total),
                "daily": {day: dict(row) for day, row in stats.daily.items()},
                "maintenance": self.maintenance.status, "armed": self.maintenance.armed,
                "actions": self.manager.action_count, "pauses": self.manager.pause_count}

    def launch(self, kind, payload=None):
        if self.busy:
            raise ValueError("진행 중인 작업을 먼저 중지하세요.")
        self.kind = kind
        self.commands = Queue()
        self.ctx.cancel.clear()
        self.thread = Thread(target=self._work, args=(kind, payload), name="FC automation", daemon=True)
        self.thread.start()

    def stop(self):
        if self.busy:
            self.ctx.cancel.set()

    def submit(self, action, value=None):
        if not self.busy or self.kind != "manager" or self.ctx.stop_requested():
            raise ValueError("감독모드 실행 또는 예약 중에 사용할 수 있습니다.")
        self.commands.put((action, value))

    def _reward_lobby(self):
        return self.rewards.start_if_due(self.manager.stats.session["Count"],
                                         club=self.options.club, mission=self.options.mission,
                                         blocked=self.manager.finish_after)

    def _confirm_rank(self, focus=False):
        self.manager.rank_read_at = self.manager.starting_rank = self.manager.current_rank = None
        if focus:
            self.game.focus_game(on_wait=self.ctx.log)
        with self.game.pin_client():
            first = self.game.read_rank(self.ctx.capture())
            if first is None or self.ctx.cancel.wait(0.35):
                raise ValueError("챔피언스 감독 랭킹 시작점수를 읽지 못했습니다.")
            second = self.game.read_rank(self.ctx.capture())
            if second != first:
                raise ValueError("두 번 읽은 시작점수가 일치하지 않습니다.")
            self.manager.confirm_rank(first)
        return first

    def _start_manager(self, *, focus=False):
        o = self.options
        if focus:
            self.game.focus_game(on_wait=self.ctx.log)
            if self.ctx.stop_requested():
                raise InterruptedError("감독모드 시작을 중지했습니다.")
        self.manager.start(parking=o.parking, target=o.target, minimum=o.minimum, limit=o.limit)
        if not self.manager.running:
            raise ValueError(self.manager.status)

    def _work(self, kind, payload):
        message = ""
        try:
            if kind == "manager":
                self.options, reservation = payload
                message = self._run_manager(reservation)
            elif kind == "rank":
                message = f"시작점수 {self._confirm_rank(focus=True)}점"
            elif kind == "capture":
                self.game.focus_game(on_wait=self.ctx.log)
                message = f"화면 저장 · {self.ctx.save_capture().name}"
            elif kind in ("coach", "bp"):
                self.game.focus_game(on_wait=self.ctx.log)
                with self.game.pin_client():
                    if kind == "coach":
                        message = run_coach(self.ctx, *payload)
                    else:
                        success = BPProtection(self.ctx).run()
                        message = "BP 강화 보호 100% 확인 완료" if success else "BP 강화 보호 중지"
        except Exception as exc:
            if kind == "manager" and self.manager.running and not self.ctx.stop_requested():
                self.ctx.notify("FC 감독모드 프로그램 오류", f"{exc} · {self.manager.summary()}")
            operation = {"coach": "코치 모집", "bp": "BP 강화 보호", "capture": "화면 저장", "rank": "시작점수 판독"}
            message = ("작업을 중지했습니다." if self.ctx.stop_requested() else
                       f"{operation[kind]} · {exc}" if kind in operation else str(exc))
            if not self.ctx.stop_requested():
                self.ctx.log(f"작업 중지 · {message}")
                self.ctx.report(message)
        finally:
            if self.manager.running:
                self.manager.stop("사용자 중지" if self.ctx.stop_requested() else message)
            if self.maintenance.armed:
                self.maintenance.cancel()
            self.rewards.reset()
            self.ctx.events.put(("snapshot", self.snapshot()))
            self.ctx.events.put(("done", (kind, message or "작업 종료")))

    def _controls(self):
        while not self.ctx.stop_requested():
            try:
                action, value = self.commands.get_nowait()
            except Empty:
                return
            try:
                if action == "start" and self.maintenance.armed and not self.manager.running:
                    self.options = value
                    self._start_manager(focus=self.game.game_present())
                    self.maintenance.interrupted = not self.game.game_present()
                elif action == "finish" and self.manager.running:
                    self.manager.toggle_finish()
                elif action == "finish_remote" and self.manager.running and not self.manager.finish_after:
                    self.manager.toggle_finish()
                elif action == "reward" and self.manager.running and not (self.rewards.busy or self.rewards.pending):
                    enabled = self.rewards.toggle_next(self.manager.stats.session["Count"])
                    self.ctx.log("다음 경기 후 기부·보상 확인 예약" if enabled else "기부·보상 예약 해제")
                elif action == "arm":
                    self.maintenance.arm(value, running=self.manager.running)
                elif action == "cancel_reservation":
                    if self.maintenance.cancel() and self.manager.running:
                        self.manager.stop("점검 후 시작 예약 취소")
                elif action == "capture":
                    self.game.focus_game(on_wait=self.ctx.log)
                    self.ctx.save_capture()
            except (OSError, ValueError, RuntimeError) as exc:
                self.ctx.log(str(exc))

    def _run_manager(self, reservation):
        present = self.game.game_present()
        presence = Presence(present)
        next_presence = next_snapshot = next_rank = 0
        if reservation:
            self.maintenance.arm(reservation, running=False)
        else:
            self._start_manager(focus=True)
        while not self.ctx.stop_requested() and (self.manager.running or self.maintenance.armed):
            now = monotonic()
            self._controls()
            if now >= next_presence:
                present = self.game.game_present()
                next_presence = now + 1
                if presence.exited(present, now):
                    self.ctx.log("FC 온라인 프로세스 종료 감지")
                    self.ctx.notify("FC 온라인 종료 감지", self.manager.summary())
                    self.rewards.reset()
                    if self.maintenance.armed:
                        self.maintenance.interrupted = self.manager.running
                    else:
                        self.manager.stop("FC 온라인 종료")
                        break
            if self.ctx.stop_requested():
                break
            action = self.maintenance.tick(running=self.manager.running) if self.maintenance.armed else "wait"
            if action == "stop":
                self.manager.stop("점검 후 자동 시작 예약 만료")
            elif action == "resume":
                if self.manager.running:
                    self.manager.resume_after_maintenance()
                    self.maintenance.complete()
                elif self.options.parking:
                    if now >= next_rank:
                        next_rank = now + 20
                        try:
                            self._confirm_rank()
                        except (ValueError, RuntimeError, OSError) as exc:
                            self.ctx.log(f"점검 후 주차 시작점수 확인 대기 · {exc}")
                        else:
                            try:
                                self._start_manager()
                            except ValueError as exc:
                                self.ctx.notify("점검 후 주차 시작 취소", str(exc))
                                self.maintenance.cancel()
                            else:
                                self.maintenance.complete()
                else:
                    self._start_manager()
                    self.maintenance.complete()
            elif action == "wait" and present and self.manager.running:
                if self.rewards.busy:
                    if self.rewards.tick(self.manager.stats.session["Count"]) == "failed":
                        self.manager.stop("보상 확인 후 감독모드 로비 복귀 실패")
                else:
                    with self.game.manager_frame_scope(self.options.game_only):
                        self.manager.tick()
                    if not self.manager.running and self.maintenance.armed:
                        self.maintenance.cancel()
            if now >= next_snapshot:
                self.ctx.events.put(("snapshot", self.snapshot()))
                next_snapshot = now + 0.4
            self.ctx.cancel.wait(0.04)
        if self.ctx.stop_requested():
            return "사용자 중지"
        return self.manager.status if self.manager.run_count or not reservation else self.maintenance.status
