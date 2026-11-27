"""훈련코치 모집. Every paid click requires a new client capture and a verified price."""

import io
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from urllib.request import urlopen

from PIL import Image

from coach_reader import vision
from coach_reader.__main__ import CARDS, TRAITS, load_templates


ICON_URL = "https://fco.dn.nexoncdn.co.kr/live/externalAssets/common/trainingTrait/"
ICON_MAX_BYTES = 262144
PREFERENCES = "coach-preferences.json"
TRAIT_IDS = {name: trait for trait, name in TRAITS.items()}


def combination_issue(traits):
    if len(traits) > 3 or any(type(t) is not int or t not in TRAITS for t in traits):
        return "특성 목록을 확인하세요."
    if len(set(traits)) != len(traits):
        return "같은 특성은 한 번만 선택할 수 있습니다."
    keepers = [TRAITS[t].endswith("(GK 전용)") for t in traits]
    if any(keepers) and not all(keepers):
        return "GK 전용 특성은 GK 전용 특성끼리만 선택할 수 있습니다."
    if 28 in traits and 44 in traits:
        return "침투 요청에 즉각 반응(공격)과 재정비(수비)는 함께 모집할 수 없습니다."
    return None


def _validate_dimensions(rows, limit):
    if not isinstance(rows, (tuple, list)) or len(rows) not in (4, 7):
        raise ValueError("4개 또는 7개 조합이 필요합니다.")
    if type(limit) is not int or not 300 <= limit <= 1_000_000:
        raise ValueError("CP 한도는 300에서 1,000,000 사이의 정수를 입력하세요.")
    for index, row in enumerate(rows, 1):
        if not isinstance(row, (list, tuple)) or len(row) > 3:
            raise ValueError(f"조합 {index}의 특성 목록 오류")


def validate_preferences(rows, limit):
    _validate_dimensions(rows, limit)
    for index, row in enumerate(rows, 1):
        issue = combination_issue(row)
        if issue:
            raise ValueError(f"조합 {index} · {issue}")


def load_preferences(data_dir):
    path = Path(data_dir) / PREFERENCES
    if not path.exists():
        return [[] for _ in range(7)], 300
    saved = json.loads(path.read_text(encoding="utf-8-sig"))
    try:
        if not isinstance(saved["rows"], list):
            raise ValueError("저장된 조합 목록 오류")
        rows = []
        for row in saved["rows"]:
            if not isinstance(row["traits"], list):
                raise ValueError("저장된 특성 목록 오류")
            rows.append([TRAIT_IDS[name] for name in row["traits"]])
        limit = saved["limit"]
        _validate_dimensions(rows, limit)
    except (TypeError, KeyError) as exc:
        raise ValueError("저장된 설정에서 코치 특성 또는 한도를 읽지 못했습니다.") from exc
    return rows + [[] for _ in range(7 - len(rows))], limit


def _replace_file(path, contents):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        tmp_path.write_bytes(contents)
        os.replace(tmp_path, path)
    finally:
        tmp_path.unlink(missing_ok=True)


def save_preferences(data_dir, rows, limit):
    validate_preferences(rows, limit)
    encoded = [{"traits": [TRAITS[t] for t in row]} for row in rows]
    encoded.extend({"traits": []} for _ in range(7 - len(encoded)))
    data = json.dumps({"rows": encoded, "limit": limit}, ensure_ascii=False, indent=2)
    _replace_file(Path(data_dir) / PREFERENCES, data.encode("utf-8"))


class _Stopped(Exception):
    pass


def _check_stop(ctx):
    if ctx.stop_requested():
        raise _Stopped()


def _reference(contents):
    if len(contents) > ICON_MAX_BYTES or not contents.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("기준 PNG 형식 오류")
    if not all(12 <= int.from_bytes(contents[i:i+4], "big") <= 512 for i in (16, 20)):
        raise ValueError("기준 PNG 크기 오류")
    with Image.open(io.BytesIO(contents)) as im:
        return vision.reference(im)


def _download_icon(ctx, filename):
    # URL opening can block even before a response exists. The worker stays cancellable,
    # and this read-only thread can neither save a file nor issue game input.
    done = threading.Event()
    data = None
    failure = None

    def fetch():
        nonlocal data, failure
        try:
            with urlopen(ICON_URL + filename, timeout=10) as response:
                data = response.read(ICON_MAX_BYTES + 1)
        except Exception as exc:
            failure = exc
        finally:
            done.set()

    deadline = time.monotonic() + 10
    threading.Thread(target=fetch, name="FC coach icon", daemon=True).start()
    while not done.wait(0.05):
        _check_stop(ctx)
        if time.monotonic() >= deadline:
            raise TimeoutError("기준 아이콘 다운로드 시간 초과")
    _check_stop(ctx)
    if failure:
        raise failure
    return data


def prepare_catalog(ctx):
    directory = Path(ctx.data) / ".coach-icons"
    catalog = {}
    for trait in TRAITS:
        _check_stop(ctx)
        filename = f"training_trait_icon_{trait:03}.png"
        path = directory / filename
        if path.is_file():
            try:
                with path.open("rb") as stream:
                    catalog[trait] = _reference(stream.read(ICON_MAX_BYTES + 1))
                continue
            except (OSError, ValueError):
                ctx.log(f"기준 아이콘 다시 받기 · {filename}")
        ctx.log(f"기준 아이콘 준비 · {len(catalog)+1} / {len(TRAITS)}")
        try:
            contents = _download_icon(ctx, filename)
            catalog[trait] = _reference(contents)
            _replace_file(path, contents)
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"기준 아이콘 준비 실패 · {filename} (넥슨 CDN 연결 또는 캐시 파일 확인)") from exc
    return catalog


def _ambiguity(card, row, slots, wanted):
    details = [f"{CARDS[card]} 조합 {row} (요청 ID {','.join(map(str, wanted))})"]
    for index, slot in enumerate(slots, 1):
        if slot.confirmed is None:
            ranking = sorted(slot.scores, key=slot.scores.get, reverse=True)[:2]
            scores = " / ".join(f"ID {t} {slot.scores[t]:.2f}" for t in ranking)
            requested = ", ".join(f"ID {t} {slot.scores[t]:.2f}" for t in wanted)
            details.append(f"특성 {index} [{scores}, 요청 {requested}]")
    return ", ".join(details)


class _Recruitment:
    def __init__(self, ctx, rows, limit):
        self.ctx, self.rows, self.limit = ctx, rows, limit
        self.spent = 0
        self.board = None
        self.candidates = []
        self.selected = None
        self.diagnostic = None

    def transition(self, stage, delay=0.25):
        self.stage = stage
        self.wait_reason = ""
        now = time.monotonic()
        self.deadline = now + 8
        self.next_tick = now + delay

    def click(self, x, y):
        _check_stop(self.ctx)
        self.ctx.click(x, y)

    def tick_board(self, frame, gray):
        present = []
        for card in range(6):
            state = vision.card_state(frame, gray, self.templates, card)
            if state == "candidate":
                present.append(card)
            elif state == "unknown":
                self.wait_reason = f"후보 {CARDS[card]} 특성 아이콘 또는 빈 칸 미인식"
                return

        if (self.board is not None and present == self.candidates and
                all(vision.same_card(self.board, gray, card) for card in present)):
            self.wait_reason = "새로 모집 후 후보 변경 미확인"
            return

        self.selected = None
        uncertain = []
        for card in present:
            slots = vision.read_slots(gray, card, self.catalog)
            row = vision.combination(self.rows, slots)
            if row > 0:
                self.selected = (card, row)
                break
            if row < 0:
                uncertain.append(_ambiguity(card, -row, slots, self.rows[-row-1]))
        if self.selected is None and uncertain:
            self.wait_reason = "특성 이미지 구분 불가 · " + ", ".join(uncertain)
            return
        self.board, self.candidates = gray, present
        self.transition("Decide")

    def tick_decide(self, frame, gray):
        for card in range(6):
            if card in self.candidates:
                same = (vision.card_state(frame, gray, self.templates, card) == "candidate"
                        and vision.same_card(self.board, gray, card))
            else:
                same = vision.marker(frame, self.templates, "Empty", card)
            if not same:
                raise ValueError("후보 목록이 변경되었습니다.")

        if self.selected is not None:
            card, _ = self.selected
            if self.spent + 300 > self.limit:
                raise ValueError("고용 CP 한도에 도달했습니다.")
            if not vision.marker(frame, self.templates, "Cost300", card):
                raise ValueError(f"후보 {CARDS[card]}의 고용 비용 300 CP를 확인하지 못했습니다.")
            self.click(305 + (card % 3) * 238, 320 + (card // 3) * 96)
            self.transition("Confirm", 0.45)
            return

        if self.spent + 350 > self.limit:
            return f"새로 모집 후 고용할 CP 한도가 부족합니다. 사용 {self.spent} CP"
        issue = vision.refresh_issue(frame, self.templates)
        if issue:
            if not self.wait_reason:
                self.ctx.move(665, 452)
            self.wait_reason = f"새로 모집 버튼 판독 시간 초과 · {issue} 미확인"
            return
        for card in self.candidates:
            live = vision.read_slots(gray, card, self.catalog)
            if vision.combination(self.rows, live) != 0:
                raise ValueError("새로 모집 직전 특성 재확인 결과가 달라졌습니다.")
        self.click(759, 479)
        self.spent += 50
        self.ctx.log(f"새로 모집 · 사용 {self.spent} / {self.limit} CP")
        self.transition("Board", 0.95)

    def tick(self, frame):
        def found(name):
            return vision.marker(frame, self.templates, name)
        if self.stage == "Success":
            if found("Success"):
                card, row = self.selected
                chosen = " + ".join(TRAITS[t] for t in self.rows[row-1])
                message = f"조합 {row} · {chosen} · {CARDS[card]} 후보 · 이번 모집 총 {self.spent} CP"
                try:
                    self.ctx.notify("훈련코치 영입 완료", message)
                except Exception as exc:
                    self.ctx.log(f"훈련코치 영입 알림 실패 · {exc}")
                return f"코치 고용 성공 · {message}"
            return
        if self.stage == "Confirm":
            if not found("Prompt"):
                return
            card, _ = self.selected
            if not found("Approve300") or not vision.same_card(self.board, vision.grayscale(frame), card, dialog=True):
                raise ValueError("확인창의 특성 또는 고용 비용이 다릅니다.")
            if found("Enabled"):
                if self.spent + 300 > self.limit:
                    raise ValueError("고용 CP 한도에 도달했습니다.")
                self.click(526, 389)
                self.spent += 300
                self.transition("Success")
            return
        if not found("Tab"):
            self.wait_reason = "훈련 코치 모집 탭 미인식"
            return
        if self.stage == "Prepare":
            self.ctx.move(665, 452)
            self.transition("Board", 0.45)
        elif self.stage == "Board":
            self.tick_board(frame, vision.grayscale(frame))
        elif self.stage == "Decide":
            return self.tick_decide(frame, vision.grayscale(frame))

    def run(self):
        self.templates = load_templates(Path(self.ctx.assets))
        self.catalog = prepare_catalog(self.ctx)
        self.transition("Prepare", 0.55)
        self.ctx.log("후보 특성 이미지 비교 중 · F12 중지")
        while True:
            _check_stop(self.ctx)
            now = time.monotonic()
            if now < self.next_tick:
                time.sleep(min(0.05, self.next_tick - now))
                continue
            if now > self.deadline:
                detail = self.wait_reason or self.stage
                raise ValueError(f"화면 판독 시간 초과 · {detail}")
            self.next_tick = now + 0.35
            image = self.ctx.capture()
            frame = vision.normalized_image(image)
            self.diagnostic = frame
            result = self.tick(frame)
            if result:
                return result


def run_coach(ctx, rows, limit):
    """Run on the automation worker. ctx.capture and ctx input must use the same FC client."""
    validate_preferences(rows, limit)
    if not any(rows):
        raise ValueError("원하는 조합을 하나 이상 입력하세요.")
    run = _Recruitment(ctx, rows, limit)
    try:
        result = run.run()
    except _Stopped:
        result = f"코치 모집 중지 · 사용 {run.spent} CP"
    except Exception as exc:
        if ctx.stop_requested():
            result = f"코치 모집 중지 · 사용 {run.spent} CP"
        else:
            result = f"코치 모집 중지 · {exc} · 사용 {run.spent} CP"
            board = run.diagnostic
            crop = None if board is None else Image.fromarray(board[69:497, 120:840].astype("uint8"))
            capture_path = None
            if crop is not None:
                try:
                    directory = Path(ctx.data) / "오류확인용_보내기" / "captures"
                    directory.mkdir(parents=True, exist_ok=True)
                    buffer = io.BytesIO()
                    crop.save(buffer, format="PNG")
                    target = directory / "coach-board.png"
                    _replace_file(target, buffer.getvalue())
                    capture_path = target
                except OSError as error:
                    ctx.log(f"훈련코치 진단 화면 저장 실패 · {error}")
            ctx.report(result, capture_path)
    ctx.log(result)
    return result
