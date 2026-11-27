import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from coach_reader import vision
from fc_manager import coach


def stamp(frame, patch, x, y):
    h, w = patch.shape[:2]
    frame[y:y+h, x:x+w] = patch


def pattern(x, y, slot):
    if slot == 0:
        return 128 + 90 * np.cos(x*4 + y*2)
    if slot == 1:
        return 128 + 90 * np.sin(y*6)
    return 128 + 90 * np.cos((x-y)*8)


class RecruitmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.templates = coach.load_templates(None)
        cls.empty = np.zeros((540, 960, 3), dtype=np.int16)
        stamp(cls.empty, cls.templates["Tab"], 150, 75)
        for card in range(6):
            patch = cls.templates["Empty" if card < 3 else "LowerEmpty"]
            stamp(cls.empty, patch, 192+238*(card % 3), 285+96*(card // 3))
        cls.empty[472:487, 765:783] = [8, 222, 96]
        stamp(cls.empty, cls.templates["RefreshText"], 706, 471)
        stamp(cls.empty, cls.templates["Refresh50"], 780, 472)
        cls.card = 5
        cls.board = cls.empty.copy()
        # The lower-right candidate replaces an empty slot.
        cls.board[365:400, 660:745] = 0
        stamp(cls.board, cls.templates["Cost300"], 288+2*238, 314+96)
        cls.confirm = np.zeros_like(cls.board)
        cls.success = np.zeros_like(cls.board)
        for name in ("Prompt", "Approve300", "Enabled"):
            stamp(cls.confirm, cls.templates[name], *vision.REGIONS[name][:2])
        stamp(cls.success, cls.templates["Success"], *vision.REGIONS["Success"][:2])
        for slot in range(3):
            for dialog, frame in ((False, cls.board), (True, cls.confirm)):
                if dialog:
                    x, y, w, h = 617.5+25*slot, 196, 15, 14
                else:
                    x, y = vision.board_center(cls.card, slot)
                    w, h = 18, 17
                left, top = int(x)-12, int(y)-12
                yy, xx = np.mgrid[top:top+25, left:left+25]
                px = pattern((xx-x)/w, (yy-y)/h, slot)
                frame[top:top+25, left:left+25] = (255-px if dialog else px)[..., None]
        rng = np.random.default_rng(327)
        cls.catalog = {}
        for t in coach.TRAITS:
            values = rng.normal(size=144)
            cls.catalog[t] = values / np.linalg.norm(values)
        gray = vision.grayscale(cls.board)
        for slot, t in enumerate((12, 41, 42)):
            cls.catalog[t] = vision.glyph(gray, cls.card, slot)
        cls.rows = [[] for _ in range(6)] + [[42, 12, 41]]

    def play(self, scenes, *, limit=350, catalog=None, stop_on_input=False):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        ctx = Mock(data=Path(directory.name), assets=Path(directory.name))
        sequence = iter(scenes)
        current = None
        def capture():
            nonlocal current
            current = next(sequence, current)
            return Image.fromarray(current.astype("uint8"))
        ctx.capture.side_effect = capture
        ctx.stop_requested.side_effect = lambda: stop_on_input and ctx.move.called
        clock = 0
        def advance(seconds):
            nonlocal clock
            clock += seconds
        with patch.object(coach, "load_templates", return_value=self.templates), \
             patch.object(coach, "prepare_catalog", return_value=catalog or self.catalog), \
             patch.object(coach.time, "sleep", advance), patch.object(coach.time, "monotonic", lambda: clock):
            result = coach.run_coach(ctx, self.rows, limit)
        clicks = [call.args for call in ctx.click.call_args_list]
        return result, clicks, ctx

    def test_refresh_once_then_confirm_lower_right_seventh_combination(self):
        result, clicks, ctx = self.play([self.empty]*6 + [self.board]*2 + [self.confirm]*3 + [self.success])
        self.assertEqual(clicks, [(759, 479), (781, 416), (526, 389)])
        self.assertIn("조합 7", result)
        self.assertIn("C 아래", result)
        self.assertIn("350 CP", result)
        ctx.notify.assert_called_once()
        ctx.report.assert_not_called()

    def test_unverified_dialog_or_changed_board_stops_before_payment(self):
        wrong = self.confirm.copy()
        wrong[180:212, 600:700] = 0
        for after, expected, clicks in ((wrong, "확인창", [(781, 416)]), (None, "목록", [])):
            with self.subTest(after=expected):
                frames = [self.board]*3 + [after] if after is not None else [self.board]*2 + [self.empty]
                result, actual, ctx = self.play(frames)
                self.assertIn(expected, result)
                self.assertEqual(actual, clicks)
                path = ctx.report.call_args.args[1]
                with Image.open(path) as im:
                    self.assertEqual(im.size, (720, 428))
                ctx.notify.assert_not_called()

    def test_budget_cancellation_and_delayed_refresh(self):
        result, clicks, _ = self.play([self.empty], limit=349)
        self.assertIn("한도", result)
        self.assertEqual(clicks, [])
        result, clicks, ctx = self.play([self.empty], stop_on_input=True)
        self.assertIn("중지", result)
        self.assertEqual(clicks, [])
        ctx.report.assert_not_called()
        result, clicks, ctx = self.play([self.empty])
        self.assertIn("후보 변경 미확인", result)
        self.assertEqual(clicks, [(759, 479)])
        ctx.notify.assert_not_called()

    def test_ambiguity_or_150_cp_does_not_refresh(self):
        catalog = dict(self.catalog)
        catalog[33] = catalog[12]
        result, clicks, _ = self.play([self.board], catalog=catalog)
        self.assertIn("특성 이미지 구분 불가", result)
        self.assertIn("조합 7", result)
        self.assertEqual(clicks, [])
        invalid = self.empty.copy()
        invalid[475:484, 781:783] = 10
        result, clicks, _ = self.play([invalid])
        self.assertIn("가격 숫자 50", result)
        self.assertEqual(clicks, [])


class PreferencesCatalogTests(unittest.TestCase):
    def test_legacy_bom_preset_and_seven_row_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / coach.PREFERENCES
            legacy = [{"traits": []} for _ in range(3)] + [{"traits": [coach.TRAITS[12]]}]
            path.write_text(json.dumps({"rows": legacy, "limit": 351}), encoding="utf-8-sig")
            rows, limit = coach.load_preferences(tmp)
            self.assertEqual(rows, [[], [], [], [12], [], [], []])
            self.assertEqual(limit, 351)
            rows[-1] = [41, 42]
            coach.save_preferences(tmp, rows, limit)
            self.assertEqual(coach.load_preferences(tmp), (rows, limit))
            for invalid in ([1, 12], [28, 44], [12, 12], [54]):
                rows[-1] = invalid
                with self.assertRaises(ValueError):
                    coach.save_preferences(tmp, rows, limit)
            self.assertEqual(coach.load_preferences(tmp)[0][-1], [41, 42])
            # Older releases allowed mixed rows. Show the saved choices so the user can fix one row.
            legacy[1]["traits"] = [coach.TRAITS[t] for t in (1, 12)]
            path.write_text(json.dumps({"rows": legacy, "limit": 300}), encoding="utf-8-sig")
            rows, limit = coach.load_preferences(tmp)
            self.assertEqual(rows[1], [1, 12])
            with self.assertRaisesRegex(ValueError, "조합 2"):
                coach.validate_preferences(rows, limit)

    def test_catalog_recovers_invalid_files_reuses_cache_and_honors_cancellation(self):
        rng = np.random.default_rng(65)
        array = rng.integers(0, 256, size=(32, 32, 3), dtype="uint8")
        buffer = io.BytesIO()
        Image.fromarray(array).save(buffer, "PNG")
        payload = buffer.getvalue()
        with tempfile.TemporaryDirectory() as tmp, patch.object(coach, "TRAITS", {0: "test"}):
            ctx = Mock(data=Path(tmp), stop_requested=lambda: False)
            cache = Path(tmp) / ".coach-icons" / "training_trait_icon_000.png"
            cache.parent.mkdir()
            cache.write_bytes(b"damaged")
            with patch.object(coach, "urlopen", return_value=io.BytesIO(payload)) as fetch:
                first = coach.prepare_catalog(ctx)
                second = coach.prepare_catalog(ctx)
                self.assertEqual(fetch.call_count, 1)
                np.testing.assert_equal(first[0], second[0])
            cache.write_bytes(b"damaged")
            ctx.stop_requested = Mock(side_effect=[False, True])
            with patch.object(coach, "urlopen", return_value=io.BytesIO(payload)):
                with self.assertRaises(coach._Stopped):
                    coach.prepare_catalog(ctx)
            self.assertEqual(cache.read_bytes(), b"damaged")
            ctx.stop_requested = lambda: False
            with patch.object(coach, "urlopen", return_value=io.BytesIO(b"x"*262145)):
                with self.assertRaises(RuntimeError):
                    coach.prepare_catalog(ctx)
            self.assertEqual(cache.read_bytes(), b"damaged")

    def test_stop_during_blocked_cdn_connection(self):
        entered = threading.Event()
        release = threading.Event()

        def stalled(*args, **kwargs):
            entered.set()
            release.wait(2)
            return io.BytesIO(b"unfinished")

        with tempfile.TemporaryDirectory() as tmp, patch.object(coach, "urlopen", stalled):
            ctx = Mock(data=Path(tmp), stop_requested=entered.is_set)
            try:
                with self.assertRaises(coach._Stopped):
                    coach.prepare_catalog(ctx)
            finally:
                release.set()
            self.assertFalse((Path(tmp) / ".coach-icons").exists())
