"""Read FC ranking and tier result regions with Windows OCR."""

from __future__ import annotations

import asyncio
import re

from PIL import Image


class OcrUnavailable(RuntimeError):
    """The OCR projection or the required Windows language is unavailable."""


async def _recognize(engine, bitmap):
    result = await engine.recognize_async(bitmap)
    return [line.text for line in result.lines]


def ocr_lines(image: Image.Image, language="ko-KR") -> list[str]:
    try:
        from winrt.runtime import ApartmentType, init_apartment, uninit_apartment
        from winrt.windows.globalization import Language
        from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
        from winrt.windows.media.ocr import OcrEngine
        from winrt.windows.storage.streams import DataWriter
    except ImportError as exc:
        raise OcrUnavailable("Windows OCR Python 구성 요소를 설치해 주세요.") from exc

    initialized = False
    try:
        init_apartment(ApartmentType.MULTI_THREADED)
        initialized = True
    except OSError as exc:
        # Tk or UI Automation may already own an STA on this thread.
        if getattr(exc, "winerror", None) != -2147417850:  # RPC_E_CHANGED_MODE
            raise
    try:
        engine = OcrEngine.try_create_from_language(Language(language))
        if engine is None and language == "en-US":
            engine = OcrEngine.try_create_from_user_profile_languages()
        if engine is None:
            raise OcrUnavailable(f"Windows 설정에서 {language} 광학 문자 인식(OCR)을 설치해 주세요.")
        with DataWriter() as writer:
            writer.write_bytes(image.convert("L").tobytes())
            buffer = writer.detach_buffer()
            with SoftwareBitmap.create_copy_from_buffer(
                buffer, BitmapPixelFormat.GRAY8, image.width, image.height
            ) as bitmap:
                return asyncio.run(_recognize(engine, bitmap))
    finally:
        if initialized:
            uninit_apartment()


_RANK = re.compile(r"(?<!\d)(?:\d,\d{3}|\d{4})(?!\d)")
_OUTCOME = r"(승급|승격|강등|잔류)"
_SHORT_TIER = re.compile(r"(?:등급)?" + _OUTCOME + r"(?:성공|확정|완료)?")
_FULL_TIER = re.compile(
    r"(?:(?:슈퍼챔피언스|챔피언스|슈퍼챌린저|챌린저|월드클래스|프로|세미프로|유망주|아마추어)"
    r"(?:[1-3]부)?(?:등급)?(?:으)?로)?" + _OUTCOME +
    r"(?:을)?(?:축하합니다|했습니다|하였습니다|하셨습니다|되었습니다|됐습니다|되셨습니다)"
)


def parse_rank(text: str) -> int | None:
    matches = _RANK.findall(text)
    if len(matches) == 1:
        points = int(matches[0].replace(",", ""))
        if 1000 <= points <= 9999:
            return points
    return None


def parse_tier(lines) -> str:
    outcomes = set()
    for line in lines:
        cleaned = re.sub(r"[\s.!。·「」\[\]()]", "", line)
        match = _SHORT_TIER.fullmatch(cleaned) or _FULL_TIER.fullmatch(cleaned)
        if match:
            outcome = match[1]
            outcomes.add("승급" if outcome == "승격" else outcome)
    return outcomes.pop() if len(outcomes) == 1 else ""


def read_rank(frame: Image.Image) -> int | None:
    # The original landmarks use a 1296px outer window with an 8px left
    # border and 31px title bar. Capture now returns just the client.
    client = frame.convert("RGB").resize((1280, 720), Image.Resampling.BILINEAR)
    width_scaled_height = round(frame.height * 1296 / frame.width)
    layouts = [(client, 8, 31)]
    if width_scaled_height >= 720:
        # Borderless source captures also occur in the original release.
        borderless = frame.convert("RGB").resize((1296, width_scaled_height), Image.Resampling.BILINEAR)
        layouts.append((borderless, 0, 0))
    for image, dx, dy in layouts:
        tab = image.getpixel((250 - dx, 135 - dy))
        r, g, b = image.getpixel((500 - dx, 245 - dy))
        if min(tab) < 225 or b < max(r, g) + 25:
            continue
        crop = image.crop((235 - dx, 274 - dy, 355 - dx, 330 - dy))
        enlarged = crop.resize((480, 224), Image.Resampling.BICUBIC)
        points = parse_rank(" ".join(ocr_lines(enlarged, "en-US")))
        if points is not None:
            return points
    return None


def read_tier(frame: Image.Image) -> str:
    width, height = frame.size
    left, top = round(width * 0.15), round(height * 0.19)
    crop = frame.crop((left, top, left + round(width * 0.70), top + round(height * 0.66)))
    enlarged_width = min(2000, 2 * crop.width)
    enlarged_height = max(1, round(crop.height * enlarged_width / crop.width))
    enlarged = crop.resize((enlarged_width, enlarged_height), Image.Resampling.BILINEAR)
    return parse_tier(ocr_lines(enlarged, "ko-KR"))
