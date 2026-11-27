import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from . import vision
from .__main__ import TRAITS, inspect, load_rows, load_templates


class CoachReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.templates = load_templates(None)

    def make_board(self):
        frame = np.zeros((540, 960, 3), dtype=np.int16)

        def place(name, x, y):
            patch = self.templates[name]
            h, w = patch.shape[:2]
            frame[y:y+h, x:x+w] = patch

        place("Tab", 150, 75)
        for card in range(6):
            dx, dy = card % 3*238, card//3*96
            if card == 0:
                place("Cost300", 288+dx, 314+dy)
            else:
                place("LowerEmpty" if card >= 3 else "Empty", 192+dx, 285+dy)
        # Deterministic, distinct icons. Keep these fixtures independent of game account data.
        rng = np.random.default_rng(75)
        for slot in range(3):
            x = 256+28*slot
            frame[269:298, x-12:x+13] = rng.integers(0, 256, (29, 25, 1))
        frame[479, 766] = [8, 222, 96]
        frame[473:486, 770:783] = [8, 222, 96]
        place("RefreshText", 706, 471)
        place("Refresh50", 780, 472)
        return frame

    def test_crop_and_seventh_row(self):
        frame = self.make_board()
        image = Image.fromarray(frame.astype('uint8'))
        crop = vision.normalized_image(image.crop((120, 69, 840, 497)))
        gray = vision.grayscale(frame)
        rng = np.random.default_rng(6)
        catalog = {i: rng.normal(size=144) for i in TRAITS}
        catalog = {i: ref / np.linalg.norm(ref) for i, ref in catalog.items()}
        chosen = [12, 41, 42]
        for slot, trait in enumerate(chosen):
            catalog[trait] = vision.glyph(gray, 0, slot)
        rows = [[] for _ in range(6)] + [chosen]
        for sample in (frame, crop):
            result = inspect(sample, self.templates, catalog, rows)
            self.assertEqual((result["assessment"], result["card"], result["combination"]), ("match", "A 위", 7))
            self.assertEqual([c["state"] for c in result["cards"]], ["candidate"]+["empty"]*5)
            self.assertIsNone(result["refresh_50_issue"])
        del catalog[2]
        partial = inspect(crop, self.templates, catalog, rows)
        self.assertEqual(partial["assessment"], "incomplete_catalog")
        self.assertTrue(all(s["confirmed"] is None for s in partial["cards"][0]["slots"]))

    def test_reported_duplicate_leaders(self):
        scores = [{23:.92, 22:.82, 12:.40, 41:.55, 42:.43},
                  {23:.91, 22:.78, 12:.34, 41:.59, 42:.47},
                  {43:.72, 36:.45, 12:.15, 41:.29, 42:.27}]
        slots = vision.resolve_duplicates([vision.Slot.from_scores(s) for s in scores])
        self.assertEqual(vision.combination([[12, 41, 42]], slots), 0)
        self.assertEqual(vision.combination([[23, 22, 41]], slots), -1)

    def test_uncertain_slots_and_distinct_assignment(self):
        scores = [{19:.92, 17:.91}, {24:.86, 20:.85}, {37:.72, 28:.51}]
        slots = [vision.Slot.from_scores(s) for s in scores]
        self.assertEqual(vision.combination([[19, 24, 28]], slots), -1)
        self.assertEqual(vision.combination([[12, 41, 42]], slots), 0)
        self.assertEqual(vision.combination([[19, 17, 44]], slots), 0)

    def test_refresh_150_and_missing_currency(self):
        frame = self.make_board()
        frame[475:484, 781:783] = 10
        self.assertEqual(vision.refresh_issue(frame, self.templates), "가격 숫자 50")
        frame = self.make_board()
        frame[472:487, 796:809] = [8, 222, 96]
        self.assertEqual(vision.refresh_issue(frame, self.templates), "CP 아이콘")

    def test_preset_formats_and_empty_screen(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "coach-preferences.json"
            for count in (4, 7):
                rows = [{"traits": []} for _ in range(count)]
                rows[-1]["traits"] = [TRAITS[12]]
                path.write_text(json.dumps({"rows": rows}), encoding="utf-8-sig")
                self.assertEqual(load_rows(path), [[] for _ in range(count-1)] + [[12]])
        black = np.zeros((540, 960, 3), dtype=np.int16)
        self.assertEqual(inspect(black, self.templates, {}, []), {"screen": "unknown"})
        self.assertEqual(vision.normalized_image(Image.new("RGB", (1366, 768))).shape, (540, 960, 3))
        nonstandard = vision.normalized_image(Image.new("RGB", (1280, 725)))
        self.assertEqual(vision.refresh_issue(nonstandard, self.templates), "버튼 기준 이미지")
        with self.assertRaises(ValueError):
            vision.normalized_image(Image.new("RGB", (640, 480)))


if __name__ == "__main__":
    unittest.main()
