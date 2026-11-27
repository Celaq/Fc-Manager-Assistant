"""Cooperative controller for normal and parking manager matches."""

from dataclasses import dataclass
from datetime import datetime
from time import monotonic

import numpy as np

from .manager_stats import MatchStats, append_csv
from .manager_vision import REWARD, Detector, is_reward, read_points, read_score, reference_frame


@dataclass
class ResultSampling:
    score: tuple | None = None
    score_attempts: int = 0
    rank_attempts: int = 0
    last_rank: int | str | None = None


class Manager:
    def __init__(self, ctx, on_lobby=None, read_rank=None, read_tier=None, clock=monotonic):
        self.ctx, self.on_lobby = ctx, on_lobby
        self.read_rank, self.read_tier, self.clock = read_rank, read_tier, clock
        self.detector = Detector(ctx.assets)
        self.stats = MatchStats(ctx.data, ctx.log)
        self.running = self.paused = self.parking = self.finish_after = False
        self.action_count = self.pause_count = self.run_count = 0
        self.limit = 0
        self.current_rank = self.starting_rank = self.rank_read_at = None
        self.status = "대기 중"
        self.result_counted = False
        self.tier_disabled = False
        self.tier_samples = 0
        self.last_tier_sample = None
        self.last_alert = -float("inf")

    def confirm_rank(self, points):
        if not isinstance(points, int) or not 1000 <= points <= 9999:
            raise ValueError("챔피언스 시작점수를 확인하지 못했습니다.")
        self.starting_rank, self.rank_read_at = points, self.clock()

    def validate_start(self, parking=False, target=0, minimum=None, limit=0):
        if parking:
            if self.rank_read_at is None or self.clock() - self.rank_read_at > 300:
                raise ValueError("먼저 챔피언스 감독 랭킹 화면에서 시작점수를 읽고 5분 안에 시작해 주세요.")
            if target <= self.starting_rank:
                raise ValueError("목표점수는 시작점수보다 커야 합니다.")
            if minimum is not None and minimum >= self.starting_rank:
                raise ValueError("최저점수는 시작점수보다 작아야 합니다.")
        if limit < 0:
            raise ValueError("진행 판수는 0 이상이어야 합니다.")

    def start(self, parking=False, target=0, minimum=None, limit=0):
        if self.running:
            return
        self.validate_start(parking, target, minimum, limit)
        self.running, self.parking = True, parking
        self.finish_after = self.required_finish = self.resync = False
        self.current_rank = self.starting_rank if parking else None
        self.rank_read_at = None
        self.target, self.minimum, self.limit = target, minimum, limit
        self.run_count = 0
        self.last_tier_notice = None
        self._reset_transient()
        self.due = self.clock() + 0.5
        self.status = "화면 인식 중"
        self.ctx.log("화면 인식 자동 진행을 시작했습니다.")

    def _reset_transient(self):
        self.last = {}
        self.pending = self.resync_first = self.resume_at = None
        self.previous = self.last_button = None
        self.repeats = 0
        self.last_change = self.last_progress = self.repeated_since = self.due = self.clock()
        self.stuck = self.paused = False

    def resume_after_maintenance(self):
        if self.running:
            self._reset_transient()
            self.resync = self.parking
            self.status = "점검 후 감독모드 로비 확인"

    def stop(self, reason="사용자 중지"):
        if self.running:
            self.running = self.finish_after = self.paused = False
            self.pending = None
            self.status = f"중지 · {reason}"
            self.ctx.log(f"자동 진행을 중지했습니다. ({reason})")

    def toggle_finish(self):
        if self.running and not self.required_finish:
            self.finish_after = not self.finish_after
            self.ctx.log(
                "이번 판 완료 후 중지를 예약했습니다." if self.finish_after else "중지 예약을 취소했습니다."
            )
        return self.finish_after

    def summary(self):
        s = self.stats.session
        info = (
            f"이번 실행 {s['Count']}판 ({s['Win']}승 {s['Draw']}무 {s['Loss']}패 · 미분류 {s['Unclassified']})"
            f" · 오늘 {self.stats.today['Count']}판 · 전체 {self.stats.total['Count']}판"
        )
        if self.parking and self.current_rank is not None:
            info += f" · 랭킹 점수 {self.current_rank}점"
        return info

    def _game_frame(self):
        game = getattr(self.ctx, "game", None)
        if game is None:
            # Standalone contexts supply only verified FC captures.
            return True
        return game.captured_game()

    def _tier(self, image):
        if self.read_tier is None or self.tier_disabled or not self._game_frame():
            return ""
        from .windows_ocr import OcrUnavailable

        try:
            return self.read_tier(image) or ""
        except OcrUnavailable as exc:
            self.tier_disabled = True
            self.ctx.log(f"한국어 OCR 인식 불가: {exc}")
            self.ctx.notify("승급·강등 OCR 설정 필요", "Windows 한국어 광학 문자 인식을 설치해 주세요.")
            return ""

    def _stall(self, reason, image):
        now = self.clock()
        if now - self.last_alert < 600:
            return
        self.last_alert, self.stuck = now, True
        self.ctx.notify("FC 감독모드 멈춤 감지", reason + "\n" + self.summary())
        self.ctx.log(reason)
        if not self._game_frame():
            return
        directory = self.stats.diagnostics / "captures"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"stuck_{datetime.now():%Y%m%d_%H%M%S}.png"
        image.save(path)
        if hasattr(self.ctx, "report"):
            self.ctx.report(reason, path)

    def _progress(self, button, image):
        now = self.clock()
        self.last_progress = now
        if self.last_button != button.file:
            if self.stuck:
                self.ctx.notify("FC 감독모드 정상 작동 재개", button.label + " · " + self.summary())
            self.stuck = False
            self.last_button, self.repeated_since, self.repeats = button.file, now, 1
            return
        self.repeats += 1
        if self.repeats >= 3:
            if button.file in {"skip", "s", "esc"}:
                if now - self.last_change >= 180:
                    self._stall(
                        "경기 진행 안내가 반복되는 동안 FC 화면이 3분 이상 변하지 않았습니다.", image
                    )
            elif now - self.repeated_since >= 15:
                self._stall(f"15초 이상 '{button.label}' 화면에서 같은 조작이 반복됐습니다.", image)

    def _check_limits(self):
        if self.current_rank is None:
            return False
        if self.current_rank >= self.target:
            reason = "주차 목표점수 도달"
        elif self.minimum is not None and self.current_rank <= self.minimum:
            reason = "주차 최저점수 도달"
        else:
            return False
        self.ctx.notify(reason, f"현재 {self.current_rank}점")
        self.ctx.log(reason)
        return True

    def _park_result(self, reading):
        if reading == "league":
            self.current_rank, self.resync = None, False
            self.ctx.log("챔피언스 아래 등급 · 승점 합산 제외")
        elif reading is None:
            self.current_rank, self.resync = None, True
            self.finish_after = self.required_finish = True
            self.ctx.notify("주차 점수 판독 실패", "연속 판독에 실패해 이번 결과 후 중지합니다.")
            self.ctx.log("주차 점수 재판독 실패")
            if hasattr(self.ctx, "report"):
                self.ctx.report("주차 점수 판독 실패")
        elif self.current_rank is None:
            self.resync = True
            self.ctx.log("챔피언스 점수 복귀 · 다음 경기 전 로비 점수 재확인")
        else:
            self.current_rank += reading
            append_csv(
                self.stats.diagnostics / "rank_points.csv",
                [],
                [
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    self.stats.session["Count"],
                    reading,
                    self.current_rank,
                ],
            )
            self.ctx.log(f"주차 점수 {reading:+d}점 · 현재 {self.current_rank}점")
            if self._check_limits():
                self.finish_after = self.required_finish = True

    def _sample_result(self, image, ref):
        sample = self.pending
        if not self._game_frame() or not is_reward(ref):
            self.pending = None
            self.ctx.log("결과 화면이 변경되어 경기 집계를 보류했습니다.")
            return
        if sample.score is None and sample.score_attempts < 13:
            sample.score = read_score(ref)
            sample.score_attempts += 1
            if sample.score is None and sample.score_attempts < 13:
                self.due = self.clock() + 0.25
                return
        reading = None
        if self.parking:
            reading = read_points(ref)
            sample.rank_attempts += 1
            confirmed = reading is not None and reading == sample.last_rank
            if not confirmed and sample.rank_attempts < 5:
                sample.last_rank = reading
                self.due = self.clock() + 0.3
                return
            if not confirmed:
                reading = None
        result = self.stats.register(sample.score, image)
        self.result_counted, self.pending = True, None
        self.run_count += 1
        if self.parking:
            self._park_result(reading)
        if self.limit and self.run_count >= self.limit:
            self.finish_after = self.required_finish = True
        score = f"{sample.score[0]}:{sample.score[1]}" if sample.score else "미분류"
        self.ctx.log(f"경기 완료 {score} ({result}) · {self.summary()}")
        if self.stats.session["Count"] % 20 == 0:
            self.ctx.notify("FC 감독모드 20판 진행 알림", self.summary())
        # Verify 'Next' is still visible in the settled result before advancing.
        match = self.detector.detect(image, ref, self.clock(), self.last)
        if match and match.button == REWARD:
            self._act(match, image)

    def _lobby(self, image):
        if not self._game_frame():
            self.resync_first = None
            return self.parking and self.resync
        if self.parking and self.resync:
            rank = self.read_rank(image) if self.read_rank else None
            if not self._game_frame():
                self.resync_first = None
                return True
            if rank is None:
                raise ValueError("등급 복귀 후 현재점수 재확인 실패")
            if self.resync_first is None:
                self.resync_first = rank
                self.due = self.clock() + 0.35
                return True
            if self.resync_first != rank:
                raise ValueError("등급 복귀 후 현재점수 연속 판독 불일치")
            self.current_rank, self.resync, self.resync_first = rank, False, None
            self.ctx.log(f"등급 복귀 후 현재 {rank}점 재확인")
            if self._check_limits():
                self.stop("재확인 점수 조건 도달")
                return True
        return not self.finish_after and self.on_lobby is not None and self.on_lobby()

    def _act(self, match, image):
        button = match.button
        if (button.file.startswith("tier_") or button == REWARD) and not self._game_frame():
            return
        if button.file == "click_1" and self._lobby(image):
            return
        if self.ctx.stop_requested():
            self.stop()
            return
        if button == REWARD and not self.result_counted:
            self.pending = ResultSampling()
            self.status = "경기 결과 화면 안정화 중"
            self.due = self.clock() + 3.5
            return
        if match.tier in {"강등", "승급"} and self.last_tier_notice != self.stats.session["Count"]:
            self.last_tier_notice = self.stats.session["Count"]
            self.ctx.notify("감독모드 " + match.tier, self.summary())
        if (
            button.file == "tier_result"
            and self.read_tier
            and not match.tier
            and not self.tier_disabled
            and self.last_tier_sample != self.stats.session["Count"]
            and self.tier_samples < 5
        ):
            directory = self.stats.diagnostics / "captures"
            directory.mkdir(parents=True, exist_ok=True)
            image.save(directory / f"tier_result_unknown_{datetime.now():%Y%m%d_%H%M%S_%f}.png")
            self.tier_samples += 1
            self.last_tier_sample = self.stats.session["Count"]
        if button.action == "click":
            self.ctx.click(match.x, match.y)
        elif button.action != "none":
            self.ctx.key(button.action)
        if button.action != "none":
            if button != REWARD:
                self.result_counted = False
            self.action_count += 1
            self._progress(button, image)
        self.last[button.file] = self.clock()
        self.status = f"{button.label} · {button.action}"
        self.ctx.log(f"화면 인식 {self.status}")
        if button == REWARD and self.finish_after:
            self.stop(
                f"설정 {self.limit}판 완료"
                if self.limit and self.run_count >= self.limit
                else "이번 판 완료"
            )

    def _tick(self):
        now = self.clock()
        image = self.ctx.capture()
        if image is None:
            self._pause()
            return
        if self.paused:
            if self.resume_at is None:
                self.resume_at = now + 1.5
            if now < self.resume_at:
                self.due = min(now + 0.15, self.resume_at)
                return
            self.paused = False
            self.resume_at = None
            self.previous = None
            self.last.clear()
            self.last_progress = self.last_change = now
            self.ctx.log("FC ONLINE 창으로 돌아와 화면 인식을 재개합니다.")
            self.due = now + 0.3
            return
        self.due = now + 0.65
        ref = reference_frame(image)
        if self.pending:
            self._sample_result(image, ref)
            return
        vis = ref[113:592:16, 194:1062:16].astype(np.int16)
        if self.previous is None or np.mean(np.abs(self.previous - vis).sum(axis=2) > 65) >= 0.01:
            self.last_change = now
        self.previous = vis
        match = self.detector.detect(image, ref, now, self.last, self._tier)
        if match is None or match.button.file != "click_1":
            self.resync_first = None
        if match:
            self._act(match, image)
        else:
            self.status = "등록 화면 대기 중"
            if now - self.last_progress >= 1200:
                self._stall("20분 동안 다음 조작 화면을 인식하지 못했습니다.", image)

    def _pause(self):
        if not self.paused:
            self.ctx.log("FC 창 대기 · 다른 창에 대한 입력을 멈췄습니다.")
            self.pause_count += 1
        self.paused = True
        self.resume_at = self.pending = self.resync_first = None
        self.status = "FC 창 대기 중"
        self.due = self.clock() + 0.15

    def tick(self):
        if not self.running:
            return
        if self.ctx.stop_requested():
            self.stop()
            return
        if self.clock() < self.due:
            return
        from .windows import WindowUnavailable

        try:
            self._tick()
        except WindowUnavailable:
            self._pause()
