"""Recognize v3.3.4 manager screens using the shipped glyphs and button images."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from . import manager_patterns as patterns


@lru_cache(maxsize=128)
def bits(encoded, count, order="little"):
    packed = np.frombuffer(base64.b64decode(encoded), np.uint8)
    return np.unpackbits(packed, bitorder=order)[:count].astype(bool)


def label_matches(frame, label):
    x, y, w, h, encoded = label
    return ink_matches(np.min(frame[y : y + h, x : x + w], axis=2) >= 140, encoded, 40)


def ink_matches(actual, encoded, minimum):
    ink = bits(encoded, actual.size)
    observed = actual.ravel()
    n = ink.sum()
    return n >= minimum and (ink & ~observed).sum() <= 0.12 * n and (~ink & observed).sum() <= 0.35 * n


def reference_frame(image):
    """Map client content back onto the 1296x759 window used for label capture."""
    client = np.asarray(image.convert("RGB"))
    content = cv2.resize(client, (1280, 720), interpolation=cv2.INTER_CUBIC)
    return cv2.copyMakeBorder(content, 31, 8, 8, 8, cv2.BORDER_CONSTANT)


def bright_points(frame, coords, threshold):
    return all(frame[y, x].min() >= threshold for x, y in coords)


def is_reward(frame):
    return bright_points(frame, ((480, 240), (640, 240), (180, 320), (380, 350)), 225)


def is_network_error(frame):
    if not bright_points(frame, ((450, 300), (840, 300), (450, 420), (840, 420)), 235):
        return False
    ink = np.min(frame[367:383, 495:711], axis=2) < 180
    return any(ink_matches(ink, body, 80) for body in patterns.NETWORK_BODIES)


def _components(mask, connectivity):
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=connectivity)
    for i in range(1, n):
        x, y, w, h, size = stats[i]
        yield x, y, w, h, size, labels[y : y + h, x : x + w] == i


def _score_digit(frame, x1, x2):
    h, w = frame.shape[:2]
    roi = frame[int(h * 0.11) : int(h * 0.26), int(w * x1) : int(w * x2)].astype(np.int16)
    lum = roi.sum(axis=2) // 3
    maximum = int(lum.max())
    if maximum < 55:
        return None
    mask = (lum > max(38, int(maximum * 0.62))) & (np.ptp(roi, axis=2) < 50)
    candidates = [
        (ch * 10 + size, c)
        for _, _, cw, ch, size, c in _components(mask, 4)
        if max(35, int(h * 0.055)) <= ch <= int(h * 0.12)
        and max(8, int(w * 0.008)) <= cw <= int(w * 0.06)
        and size >= 80
    ]
    if not candidates:
        return None
    component = max(candidates, key=lambda item: item[0])[1]
    bh, bw = component.shape
    scale = min(26 / bw, 54 / bh)
    nw, nh = max(1, round(bw * scale)), max(1, round(bh * scale))
    glyph = np.zeros((56, 28), dtype=bool)
    oy, ox = (56 - nh) // 2, (28 - nw) // 2
    glyph[oy : oy + nh, ox : ox + nw] = component[
        np.arange(nh)[:, None] * bh // nh, np.arange(nw) * bw // nw
    ]
    distances = []
    for digit, sample in enumerate(patterns.DIGITS):
        difference = np.mean(glyph.ravel() != bits(sample, 1568, "big"))
        if digit in patterns.VARIANTS:
            alt = np.mean(glyph.ravel() != bits(patterns.VARIANTS[digit], 1568, "big"))
            if alt <= 0.08:
                difference = min(alt, difference)
        distances.append((difference, digit))
    (best, digit), (second, _) = sorted(distances)[:2]
    return digit if best <= 0.31 and second - best >= 0.025 else None


def read_score(frame):
    left, right = _score_digit(frame, 0.40, 0.50), _score_digit(frame, 0.50, 0.59)
    return None if left is None or right is None else (left, right)


def read_points(frame):
    """Return a signed delta, 'league' below Champions, or None on an uncertain read."""
    if not bright_points(frame, ((499, 240), (530, 240), (600, 240)), 140):
        return None
    if label_matches(frame, patterns.LEAGUE):
        return "league"
    if not label_matches(frame, patterns.RANK):
        return None
    mask = frame[317:348, 974:1029].min(axis=2) >= 140
    glyphs = []
    for x, _, w, h, size, component in _components(mask, 8):
        if size >= 18 and 13 <= h <= 20 and w <= 12 and 994 <= x + 974 <= 1016:
            glyph = np.zeros((20, 12), dtype=bool)
            glyph[:h, :w] = component
            glyphs.append((x, glyph.ravel()))
    sign_ink = mask[12:23, 10:20].sum()
    if not 1 <= len(glyphs) <= 2 or 2 < sign_ink < 8:
        return None
    number = 0
    for _, glyph in sorted(glyphs, key=lambda item: item[0]):
        distances = []
        for row in patterns.POINT_DIGITS:
            digit, samples = row.split(":")
            distance = min(np.count_nonzero(bits(s, 240) != glyph) for s in samples.split(","))
            distances.append((distance, int(digit)))
        (best, digit), (second, _) = sorted(distances)[:2]
        if best > 12 or second - best < 5:
            return None
        number = number * 10 + digit
    return -number if sign_ink >= 8 else number


@dataclass(frozen=True)
class Button:
    file: str
    action: str
    label: str
    area: tuple
    threshold: float
    scaled_threshold: float
    cooldown: float


BUTTONS = [
    Button("tier_popup", "click", "등급 변경 안내 확인", (0.52, 0.73, 0.66, 0.81), 4, 5, 1.8),
    Button("tier_result", "click", "승급·강등·잔류 결과 확인", (0.75, 0.90, 0.89, 0.99), 4, 5, 1.8),
    Button("space_2", "space", "선택 완료", (0.25, 0.50, 0.65, 0.75), 6, 7, 1.5),
    Button("space_1", "space", "진행", (0.60, 0.70, 1, 1), 6, 8.5, 1.5),
    Button("click_1", "click", "공식경기 (전 등급)", (0.70, 0.72, 1, 1), 7.5, 7.5, 1.8),
    Button("click_2", "click", "확인", (0.10, 0.30, 1, 1), 6, 8, 1.8),
    Button("click_3", "click", "확인 화살표", (0.10, 0.30, 1, 1), 6, 8, 1.8),
    Button("click_4", "click", "준비 완료", (0.40, 0.45, 0.85, 0.80), 6, 8.5, 1.8),
    Button("skip", "s", "후반 종료 SKIP", (0.20, 0.60, 1, 1), 16, 18, 0.9),
    Button("s", "s", "S 안내", (0.89, 0.89, 0.98, 0.99), 12, 16, 0.9),
    Button("esc", "esc", "경기 하이라이트 ESC SKIP", (0.80, 0.915, 0.87, 0.975), 36, 36, 2.2),
    Button("client", "none", "FC 클라이언트", (0, 0, 0.25, 0.25), 10, 12, 3),
]
NETWORK = Button("network_confirm", "click", "연결·게임 오류 확인", (0.49, 0.57, 0.64, 0.65), 5, 5, 1.8)
LEVEL_UP = Button("levelup_confirm", "click", "레벨업 보상 확인", (0.75, 0.90, 0.90, 0.99), 5, 5, 1.8)
REWARD = Button("reward_next", "space", "경기 보상 다음", (0.74, 0.90, 0.89, 0.99), 5, 5, 1.8)


def normalize_green(region):
    r, g, b = cv2.split(region.astype(np.int16))
    out = region.copy()
    out[(g >= 80) & (g - r > 50) & (g - b > 40)] = [0, 220, 80]
    return out


def locate(screen, template, threshold, glyph=False):
    """Locate the button, then apply the original RGB difference acceptance test."""
    h, w = template.shape[:2]
    sh, sw = screen.shape[:2]
    if sh < h or sw < w:
        return None
    if glyph:
        mask = template.min(axis=2) >= 190
        yy, xx = np.indices((h, w))
        mask &= (xx + yy) % 2 == 0
        if mask.sum() < 30:
            return None
        diff = cv2.matchTemplate(screen, template, cv2.TM_SQDIFF, mask=mask.astype(np.uint8))
    else:
        # Integral-image SQDIFF on a reduced ROI finds a candidate quickly even
        # on QHD. Refine on full-resolution RGB before accepting a location.
        factor = 2 if min(h, w) >= 12 else 1
        small = screen[::factor, ::factor]
        sample = template[::factor, ::factor]
        coarse = cv2.matchTemplate(small, sample, cv2.TM_SQDIFF)
        _, _, (cx, cy), _ = cv2.minMaxLoc(coarse)
        cx, cy = cx * factor, cy * factor
        rx, ry = max(0, cx - 3), max(0, cy - 3)
        region = screen[ry : min(sh, cy + h + 3), rx : min(sw, cx + w + 3)]
        diff = cv2.matchTemplate(region, template, cv2.TM_SQDIFF)
    _, _, (x, y), _ = cv2.minMaxLoc(diff)
    if not glyph:
        x, y = x + rx, y + ry
    candidate = screen[y : y + h, x : x + w]
    error = cv2.absdiff(candidate, template)
    mean = error[mask].mean() if glyph else error[::2, ::2].mean()
    if mean > threshold:
        return None
    return x, y


def esc_caption(frame, x, y, width, height):
    left = x + width + max(3, width // 9)
    text = frame[
        y + max(2, height // 7) : y + height - max(2, height // 7), left : left + max(30, width * 13 // 10)
    ]
    bright = (text.min(axis=2) >= 175) if text.size else np.zeros(0, bool)
    return max(18, bright.size // 10) <= bright.sum() <= bright.size // 2


@dataclass(frozen=True)
class Recognition:
    button: Button
    x: float
    y: float
    tier: str = ""


@lru_cache(maxsize=96)
def _template(assets, file, hd, width_scale, green):
    root = "templates_2560" if hd else "templates"
    with Image.open(assets / root / (file + ".png")) as img:
        rgb = np.asarray(img.convert("RGB"))
    h, w = rgb.shape[:2]
    size = (max(1, round(w * width_scale)), max(1, round(h * width_scale)))
    resized = cv2.resize(rgb, size, interpolation=cv2.INTER_CUBIC)
    return normalize_green(resized) if green else resized


class Detector:
    def __init__(self, assets):
        self.assets = Path(assets)

    def detect(self, image, ref, now, last, read_tier=None):
        context = (
            NETWORK
            if is_network_error(ref)
            else LEVEL_UP
            if label_matches(ref, patterns.LEVELUP)
            else REWARD
            if is_reward(ref)
            else None
        )
        # Match UI textures at the native client resolution. The source used
        # fractions of the outer window, including 8px borders and a 31px title.
        full = np.asarray(image.convert("RGB"))
        h, w = full.shape[:2]
        scale = w / 1280
        pad_x, pad_y = round(8 * scale), round(31 * scale)
        outer_w, outer_h = w + 2 * pad_x, h + pad_y + round(8 * scale)
        for btn in [context] if context else BUTTONS:
            if now - last.get(btn.file, -float("inf")) < btn.cooldown:
                continue
            if btn.file == "tier_popup" and not bright_points(
                ref, ((420, 170), (870, 170), (420, 550), (870, 550)), 235
            ):
                continue
            x1, y1, x2, y2 = btn.area
            x0 = max(0, int(x1 * outer_w) - pad_x)
            y0 = max(0, int(y1 * outer_h) - pad_y)
            roi = full[y0 : min(h, round(y2 * outer_h) - pad_y), x0 : min(w, round(x2 * outer_w) - pad_x)]
            green = bool(context) or btn.file == "tier_result"
            if green:
                roi = normalize_green(roi)
            hd = abs(scale - 1) > 0.08 and not (
                btn.file.startswith("tier_") or context or btn.file == "click_1"
            )
            threshold = btn.scaled_threshold if abs(scale - 1) > 0.08 else btn.threshold
            if hd and (self.assets / "templates_2560" / (btn.file + ".png")).is_file():
                scales = (w / 2560,)
            elif context or btn.file.startswith("tier_"):
                hd = False
                scales = (scale,)
            else:
                hd = False
                scales = (scale, outer_w / 1282, outer_h / 752, 1.0)
            checked = set()
            for s in scales:
                tmpl = _template(self.assets, btn.file, hd, min(2, max(0.35, s)), green)
                th, tw = tmpl.shape[:2]
                if (tw, th) in checked:
                    continue
                checked.add((tw, th))
                point = locate(roi, tmpl, threshold, btn.file in {"s", "esc"})
                if point is None:
                    continue
                x, y = x0 + point[0], y0 + point[1]
                if btn.file == "esc" and not esc_caption(full, x, y, tw, th):
                    continue
                tier = ""
                if btn.file.startswith("tier_"):
                    tier = read_tier(image) if read_tier else ""
                    if btn.file == "tier_result" and not (label_matches(ref, patterns.TIERRESULT) or tier):
                        continue
                return Recognition(btn, (x + tw / 2) * 960 / w, (y + th / 2) * 540 / h, tier)
        return None
