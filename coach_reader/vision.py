"""FC Manager 3.3.4 coach panel vision on a normalized 960 × 540 RGB image."""

from dataclasses import dataclass
from itertools import permutations, product
from math import ceil, floor

import numpy as np
from PIL import Image

SAMPLE_GRID = (np.arange(12) + 0.5) / 12 - 0.5
OFFSETS = tuple(product((0.9, 1.0, 1.1), (-1.25, 0, 1.25), (-1.25, 0, 1.25)))
REGIONS = {
    "Tab": (150, 75, 10), "Cost300": (288, 314, 10), "Empty": (192, 285, 5),
    "Prompt": (440, 297, 9), "Approve300": (644, 253, 10),
    "Enabled": (482, 382, 10), "Success": (422, 277, 9),
}


def normalized_image(image):
    image = image.convert("RGB")
    if image.size == (720, 428):  # Save-CoachBoardDiagnostic crop in the Windows release
        canvas = Image.new("RGB", (960, 540))
        canvas.paste(image, (120, 69))
        image = canvas
    elif image.width < 640 or abs(image.width / image.height - 16/9) > 0.035:
        raise ValueError("16:9 게임 화면 또는 720×428 coach-board.png가 필요합니다.")
    elif image.size != (960, 540):
        height = round(image.height * (960 / image.width))
        image = image.resize((960, height), Image.Resampling.BICUBIC)
    return np.asarray(image, dtype=np.int16)


def grayscale(rgb):
    return (rgb[..., 0] + 2 * rgb[..., 1] + rgb[..., 2]) / 4.0


def signature(gray, x, y, width, height, variance, polarity=1):
    xx, yy = x + width * SAMPLE_GRID, y + height * SAMPLE_GRID
    ix, iy = xx.astype(int), yy.astype(int)
    fx, fy = xx - ix, (yy - iy)[:, None]
    def row(offset):
        return (1 - fx) * gray[np.ix_(iy + offset, ix)] + fx * gray[np.ix_(iy + offset, ix + 1)]
    values = ((1 - fy) * row(0) + fy * row(1)).ravel()
    values -= values.mean()
    energy = values @ values
    if energy < values.size * variance:
        return None
    return values * (polarity / np.sqrt(energy))


def reference(image):
    if not all(12 <= size <= 512 for size in image.size):
        raise ValueError("기준 아이콘 크기 오류")
    rgba = np.asarray(image.convert("RGBA"), dtype=np.int32)
    rgb, alpha = rgba[..., :3], rgba[..., 3]
    gray = grayscale(rgb)
    transparent = alpha[0, 0] < 32
    opaque = gray[alpha > 128]
    matte = 0 if transparent and opaque.size and opaque.mean() > 128 else 255
    ink = alpha > 96
    if not transparent:
        ink &= np.abs(gray - gray[0, 0]) > 24
    ys, xs = np.where(ink)
    if not xs.size or xs.max() - xs.min() < 10 or ys.max() - ys.min() < 10:
        raise ValueError("기준 아이콘 윤곽 오류")
    alpha = alpha[..., None]
    flat = (rgb * alpha + matte * (255 - alpha)) // 255
    sig = signature(grayscale(flat), (xs.max()+xs.min())/2, (ys.max()+ys.min())/2,
                    (xs.max()-xs.min()+1)*0.76, (ys.max()-ys.min()+1)*0.63, 64)
    if sig is None:
        raise ValueError("기준 아이콘 대비 부족")
    return sig


def marker(frame, templates, name, card=0):
    x, y, tolerance = REGIONS[name]
    x += card % 3 * 238
    y += card // 3 * 96
    ref = templates["LowerEmpty" if name == "Empty" and card >= 3 else name]
    h, w = ref.shape[:2]
    # Match the coarse search and one-pixel refinement in FCTemplateMatcher.Find.
    x0, x1 = floor(960*((x-2)/960)), ceil(960*((x+w+2)/960))-w
    height = frame.shape[0]
    y0, y1 = floor(height*((y-2)/540)), ceil(height*((y+h+2)/540))-h
    sy, sx = max(2, h//6), max(2, w//12)
    def difference(left, top, step_y, step_x):
        patch = frame[top:top+h:step_y, left:left+w:step_x]
        return np.abs(patch - ref[::step_y, ::step_x]).sum()
    best = ceil(tolerance * 2.5 * ref[::sy, ::sx].size)
    pos = None
    for top in range(y0, y1+1, 2):
        for left in range(x0, x1+1, 2):
            value = difference(left, top, sy, sx)
            if value < best:
                best, pos = value, (left, top)
    if pos is None:
        return False
    cx, cy = pos
    for top in range(max(y0, cy-3), min(y1, cy+3)+1):
        for left in range(max(x0, cx-3), min(x1, cx+3)+1):
            value = difference(left, top, sy, sx)
            if value < best:
                best, pos = value, (left, top)
    return bool(difference(*pos, 2, 2) <= tolerance * ref[::2, ::2].size)


def refresh_issue(frame, templates):
    text, price = templates["RefreshText"], templates["Refresh50"]
    if frame.shape[:2] != (540, 960) or text.shape[:2] != (17, 45) or price.shape[:2] != (15, 28):
        return "버튼 기준 이미지"
    red, green, blue = frame[479, 766]
    if green < 170 or green-blue < 50 or green-red < 70:
        return "활성 버튼 배경"

    def correlation(ref, x, y, left, top, w, h, channel):
        a = frame[y+top:y+top+h, x+left:x+left+w, channel].astype(float).ravel()
        b = ref[top:top+h, left:left+w, channel].astype(float).ravel()
        a -= a.mean()
        b -= b.mean()
        aa, bb = a @ a, b @ b
        if aa < a.size*256 or bb == 0:
            return 0.0
        return (a @ b) / np.sqrt(aa*bb)

    shifts = tuple(product(range(-3, 4), repeat=2))
    if not any(correlation(text, 706+dx, 471+dy, 4, 3, 36, 11, 1) >= 0.76 for dy, dx in shifts):
        return "새로 모집 글자"
    digits = False
    for dy, dx in shifts:
        x, y = 780+dx, 472+dy
        if correlation(price, x, y, 3, 2, 6, 11, 1) < 0.83 or correlation(price, x, y, 9, 2, 7, 11, 1) < 0.83:
            continue
        if np.any(frame[y+3:y+12, x-6:x+3, 1] < green*0.73):
            continue
        digits = True
        _, g, b = frame[y+7, x+22]
        if b-g > 25 and correlation(price, x, y, 16, 1, 12, 13, 2) >= 0.73:
            return None
    return "CP 아이콘" if digits else "가격 숫자 50"


def board_center(card, slot):
    return 256 + card % 3*238 + slot*28.25, 283.25 + card//3*96


def glyph(gray, card, slot, dialog=False):
    if dialog:
        return signature(gray, 617.5+25*slot, 196, 15, 14, 100, -1)
    return signature(gray, *board_center(card, slot), 18, 17, 100)


def same_card(original_gray, live_gray, card, dialog=False):
    for slot in range(3):
        first = glyph(original_gray, card, slot)
        second = glyph(live_gray, card, slot, dialog)
        if first is None or second is None or first @ second < (0.72 if dialog else 0.86):
            return False
    return True


def card_state(frame, gray, templates, card):
    if marker(frame, templates, "Cost300", card):
        return "candidate"
    for slot in range(3):
        x = 256 + card % 3*238 + slot*28
        y = 283 + card//3*96
        edges = 0
        for direction, offset in product((-1, 1), (-2, 2)):
            edges += int(gray[y+direction*9, x+offset] - gray[y+direction*15, x+offset] > 55)
            edges += int(gray[y+offset, x+direction*9] - gray[y+offset, x+direction*14] > 55)
        if edges < 3 or glyph(gray, card, slot) is None:
            return "empty" if marker(frame, templates, "Empty", card) else "unknown"
    return "candidate"


@dataclass
class Slot:
    scores: dict
    confirmed: int | None

    @classmethod
    def from_scores(cls, scores):
        ordered = sorted(scores, key=scores.get, reverse=True)
        best = ordered[0]
        runner = scores[ordered[1]] if len(ordered) > 1 else 0
        confirmed = best if scores[best] >= 0.78 and scores[best]-runner >= 0.055 else None
        return cls(scores, confirmed)

    def could_be(self, trait):
        if self.confirmed is not None:
            return self.confirmed == trait
        return max(self.scores.values()) < 0.85 or self.scores.get(trait, 0) >= 0.60


def resolve_duplicates(slots):
    confirmed = [s.confirmed for s in slots]
    for slot in slots:
        if confirmed.count(slot.confirmed) > 1:
            slot.confirmed = None
    return slots


def read_slots(gray, card, catalog):
    ids = list(catalog)
    references = np.stack(list(catalog.values()))
    slots = []
    for slot in range(3):
        x, y = board_center(card, slot)
        samples = [signature(gray, x+dx, y+dy, 18*scale, 17*scale, 100)
                   for scale, dy, dx in OFFSETS]
        samples = [sample for sample in samples if sample is not None]
        scores = np.abs(references @ np.stack(samples).T).max(axis=1) if samples else np.zeros(len(ids))
        slots.append(Slot.from_scores(dict(zip(ids, map(float, scores)))))
    return resolve_duplicates(slots)


def combination(rows, slots):
    """Positive confirmed row, negative first possible row, zero when all are excluded."""
    known = {slot.confirmed for slot in slots}
    possible = 0
    for index, wanted in enumerate(rows, 1):
        if not wanted:
            continue
        if known.issuperset(wanted):
            return index
        if not possible and any(all(slot.could_be(trait) for slot, trait in zip(order, wanted))
                                for order in permutations(slots, len(wanted))):
            possible = -index
    return possible
