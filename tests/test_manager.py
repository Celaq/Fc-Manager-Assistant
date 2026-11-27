import csv
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import cv2
import numpy as np
from PIL import Image

from fc_manager import manager_patterns as p
from fc_manager.manager import Manager
from fc_manager.manager_stats import MatchStats
from fc_manager.manager_vision import (
    Detector,
    bits,
    esc_caption,
    is_network_error,
    label_matches,
    read_points,
    read_score,
    reference_frame,
)
from fc_manager.runtime import assets_directory
from fc_manager.windows import WindowUnavailable


def ink(array, x, y, mask):
    region = array[y : y + mask.shape[0], x : x + mask.shape[1]]
    region[mask] = 255


def scoreboard(left=2, right=1):
    arr = np.zeros((759, 1296, 3), dtype=np.uint8)
    for n, x in ((left, 580), (right, 680)):
        glyph = bits(p.DIGITS[n], 1568, "big").reshape(56, 28)
        glyph = cv2.resize(glyph.astype(np.uint8), (42, 84), interpolation=cv2.INTER_NEAREST)
        ink(arr, x, 105, glyph.astype(bool))
    for x, y in ((480, 240), (640, 240), (180, 320), (380, 350), (499, 240), (530, 240), (600, 240)):
        arr[y, x] = 255
    return arr


def put_label(arr, pattern):
    x, y, w, h, encoded = pattern
    ink(arr, x, y, bits(encoded, w * h).reshape(h, w))


def point_frame(n):
    arr = scoreboard()
    put_label(arr, p.RANK)
    for i, char in enumerate(str(abs(n))):
        encoded = p.POINT_DIGITS[int(char)].split(":")[1].split(",")[0]
        glyph = bits(encoded, 240).reshape(20, 12)
        ink(arr, 995 + 13 * i, 324, glyph)
    if n < 0:
        arr[333:335, 986:992] = 255
    return arr


def client(ref):
    return Image.fromarray(ref[31:751, 8:1288])


class Context:
    def __init__(self, root, assets, image):
        self.data, self.assets, self.image = root, assets, image
        self.keys, self.notifications = [], []
        self.interrupted = False

    def capture(self):
        if self.interrupted:
            raise WindowUnavailable("foreground")
        return self.image

    def log(self, message):
        pass

    def notify(self, title, message):
        self.notifications.append(title)

    def key(self, key):
        self.keys.append(key)

    def click(self, x, y):
        self.keys.append((x, y))

    def stop_requested(self):
        return False


class ManagerVisionTests(unittest.TestCase):
    def test_scores_all_glyphs_and_signed_points(self):
        for digit in range(10):
            with self.subTest(digit=digit):
                self.assertEqual(read_score(scoreboard(digit, (digit + 1) % 10)), (digit, (digit + 1) % 10))
                self.assertEqual(read_points(point_frame(digit)), digit)
        self.assertEqual(read_points(point_frame(-12)), -12)
        arr = point_frame(9)
        arr[333, 986:990] = 255
        self.assertIsNone(read_points(arr))
        league = scoreboard()
        put_label(league, p.LEAGUE)
        self.assertEqual(read_points(league), "league")
        self.assertIsNone(read_points(scoreboard()))
        self.assertIsNone(read_score(np.zeros((759, 1296, 3), np.uint8)))

    def test_context_fingerprints_and_skip_caption(self):
        frame = np.zeros((759, 1296, 3), np.uint8)
        frame[200:470, 360:900] = 255
        self.assertFalse(is_network_error(frame))
        body = bits(p.NETWORK_BODIES[0], 216 * 16).reshape(16, 216)
        frame[367:383, 495:711][body] = 0
        self.assertTrue(is_network_error(frame))
        self.assertFalse(label_matches(frame, p.LEVELUP))
        self.assertFalse(esc_caption(frame, 0, 0, 27, 26))
        frame[4:11, 32:52] = 255
        self.assertTrue(esc_caption(frame, 0, 0, 27, 26))

    def test_templates_are_loaded_from_release_and_positions_map_to_client(self):
        with assets_directory() as assets:
            detector = Detector(assets)
            for w, h, folder in ((1280, 720, "templates"), (2560, 1440, "templates_2560")):
                f = Image.new("RGB", (w, h))
                with Image.open(assets / folder / "space_2.png") as template:
                    x, y = w * 3 // 8 + 1, h * 3 // 5 + 1
                    f.paste(template, (x, y))
                    tw, th = template.size
                match = detector.detect(f, reference_frame(f), 10, {})
                self.assertIsNotNone(match)
                self.assertEqual(match.button.file, "space_2")
                self.assertAlmostEqual(match.x, (x + tw / 2) * 960 / w, places=2)
                self.assertAlmostEqual(match.y, (y + th / 2) * 540 / h, places=2)
            f = client(scoreboard())
            with Image.open(assets / "templates" / "click_2.png") as verify:
                f.paste(verify, (600, 440))
            self.assertIsNone(detector.detect(f, reference_frame(f), 1, {}))


class ManagerRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        assets = self.enterContext(assets_directory())
        self.ctx = Context(Path(self.tmp.name), assets, client(point_frame(12)))
        with Image.open(assets / "templates" / "reward_next.png") as next_image:
            self.ctx.image.paste(next_image, (975, 665))
        self.now = 0
        self.manager = Manager(self.ctx, clock=lambda: self.now)

    def tick(self, delta):
        self.now += delta
        self.manager.tick()

    def test_real_reward_settles_scores_counts_once_and_stops_after_space(self):
        m = self.manager
        m.confirm_rank(3000)
        m.start(parking=True, target=3010)
        self.tick(0.6)
        self.assertIsNotNone(m.pending)
        self.tick(3.49)
        self.assertFalse(self.ctx.keys)
        self.tick(0.02)
        self.assertTrue(m.running)
        self.tick(0.31)
        self.assertEqual(self.ctx.keys, ["space"])
        self.assertFalse(m.running)
        self.assertEqual((m.stats.total["Win"], m.run_count, m.current_rank), (1, 1, 3012))
        self.assertIn("주차 목표점수 도달", self.ctx.notifications)
        self.assertEqual(len(list((m.stats.diagnostics / "point_samples" / "full").glob("*.png"))), 1)
        self.assertEqual(MatchStats(self.ctx.data, self.ctx.log).total["Win"], 1)
        with self.assertRaises(ValueError):
            m.start(parking=True, target=3010)
        m.start(limit=1)
        self.tick(0.6)
        self.assertEqual(m.run_count, 0)
        self.assertEqual(self.ctx.keys, ["space", "space"])

    def test_window_loss_during_sampling_and_stable_resume(self):
        m = self.manager
        m.start(limit=1)
        self.tick(0.51)
        self.ctx.interrupted = True
        self.tick(3.51)
        self.assertTrue(m.paused)
        self.assertIsNone(m.pending)
        self.ctx.interrupted = False
        self.tick(0.16)
        self.tick(1.51)
        self.assertFalse(m.paused)
        self.tick(0.31)
        self.assertIsNotNone(m.pending)
        self.tick(3.51)
        self.assertFalse(m.running)
        self.assertEqual(self.ctx.keys, ["space"])

    def test_park_minimum_league_resync_and_unreadable_stop(self):
        m = self.manager
        m.confirm_rank(3000)
        m.start(parking=True, target=3100, minimum=2950)
        m._park_result("league")
        self.assertIsNone(m.current_rank)
        self.assertFalse(m.resync)
        m._park_result(10)
        self.assertTrue(m.resync)
        m.read_rank = lambda _: 2990
        self.assertTrue(m._lobby(self.ctx.image))
        self.assertFalse(m._lobby(self.ctx.image))
        self.assertEqual(m.current_rank, 2990)
        m._park_result(-40)
        self.assertTrue(m.finish_after)
        self.assertTrue(m.toggle_finish())
        self.assertIn("주차 최저점수 도달", self.ctx.notifications)
        m.stop()
        m.confirm_rank(3000)
        m.start(parking=True, target=3100)
        m._park_result(None)
        self.assertTrue(m.finish_after)
        self.assertIsNone(m.current_rank)
        self.assertIn("주차 점수 판독 실패", self.ctx.notifications)

    def test_stalled_lobby_warns_once_and_new_action_recovers(self):
        m = self.manager
        m.start()
        from fc_manager.manager_vision import BUTTONS

        lobby = next(b for b in BUTTONS if b.file == "click_1")
        for when in (1, 4, 17, 20):
            self.now = when
            m._progress(lobby, self.ctx.image)
        self.assertEqual(self.ctx.notifications, ["FC 감독모드 멈춤 감지"])
        self.assertEqual(len(list((m.stats.diagnostics / "captures").glob("stuck*.png"))), 1)
        m._progress(BUTTONS[2], self.ctx.image)
        self.assertIn("FC 감독모드 정상 작동 재개", self.ctx.notifications)

    def test_expired_rank_rejected_before_run_and_reservation_is_reversible(self):
        m = self.manager
        m.confirm_rank(3000)
        self.now = 301
        with self.assertRaises(ValueError):
            m.start(parking=True, target=3200)
        self.assertFalse(m.running)
        m.start()
        self.assertTrue(m.toggle_finish())
        self.assertFalse(m.toggle_finish())

    def test_unrestricted_foreground_blocks_sensitive_actions_on_other_apps(self):
        from fc_manager.manager import ResultSampling
        from fc_manager.manager_vision import BUTTONS, REWARD, Recognition

        m = self.manager
        m.start()
        self.ctx.game = SimpleNamespace(captured_game=lambda: False)
        m.read_rank, m.on_lobby, m.read_tier = Mock(), Mock(), Mock()
        m._act(Recognition(BUTTONS[0], 10, 20, "승급"), self.ctx.image)
        self.assertFalse(self.ctx.keys)
        self.assertFalse(self.ctx.notifications)
        self.assertEqual(m._tier(self.ctx.image), "")
        m.read_tier.assert_not_called()
        lobby = next(b for b in BUTTONS if b.file == "click_1")
        m._act(Recognition(lobby, 10, 20), self.ctx.image)
        m.on_lobby.assert_not_called()
        self.assertEqual(self.ctx.keys, [(10, 20)])
        m.parking = m.resync = True
        m._act(Recognition(lobby, 10, 20), self.ctx.image)
        m.read_rank.assert_not_called()
        self.assertEqual(self.ctx.keys, [(10, 20)])
        m._act(Recognition(REWARD, 10, 20), self.ctx.image)
        self.assertIsNone(m.pending)
        m.pending = ResultSampling()
        m._sample_result(self.ctx.image, reference_frame(self.ctx.image))
        self.assertFalse(m.stats.total["Count"])
        self.assertIsNone(m.pending)
        m._stall("대기", self.ctx.image)
        self.assertFalse((m.stats.diagnostics / "captures").exists())

    def test_champions_rank_boundaries(self):
        for points in (999, 10000):
            with self.assertRaises(ValueError):
                self.manager.confirm_rank(points)
        for points in (1000, 9999):
            self.manager.confirm_rank(points)
            self.assertEqual(self.manager.starting_rank, points)

    def test_legacy_daily_records_and_csv_backup(self):
        path = self.ctx.data / "stats.json"
        path.write_text(json.dumps({"Date": "2024-01-01", "TodayCount": 2, "TotalCount": 6}))
        stats = MatchStats(self.ctx.data, self.ctx.log)
        self.assertEqual(stats.total["Unclassified"], 6)
        self.assertEqual(stats.daily["2024-01-01"]["Unclassified"], 2)
        sampledir = stats.diagnostics / "result_samples"
        sampledir.mkdir(parents=True)
        with (sampledir / "samples.csv").open("w", newline="") as f:
            writer = csv.writer(f)
            writer.writerows(
                [
                    ["time", "result", "file"],
                    ["2024-01-02 00:00", "win", "1.png"],
                    ["2024-01-02 00:00", "win", "1.png"],
                ]
            )
        stats = MatchStats(self.ctx.data, self.ctx.log)
        self.assertEqual(stats.daily["2024-01-02"]["Win"], 1)
        stats.register(None, self.ctx.image)
        self.assertEqual(stats.total["Count"], 7)
        self.assertEqual(json.loads(path.with_suffix(".json.bak").read_text())["TotalCount"], 6)
