import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from PIL import Image

from fc_manager.extras import (
    BP, BPProtection, KST, Maintenance, Markers, RewardFlow, _mission_step, _recovery_step,
)
from fc_manager.extras_launcher import _select_start
from fc_manager.runtime import assets_directory


class ExtrasTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.time = datetime(2026, 11, 27, 14, 45, tzinfo=timezone.utc)
        self.clock = 0
        self.screen = set()
        self.ctx = Mock(data=Path(tmp.name), assets=Path(tmp.name))
        self.ctx.stop_requested.return_value = False
        self.ctx.capture.return_value = Image.new("RGB", (960, 540))
        self.ctx.click.return_value = True
        self.flow = RewardFlow(self.ctx, clock=lambda: self.clock, now=lambda: self.time)
        self.flow.markers.read = lambda image: self.screen.__contains__

    def step(self, *markers, match=1):
        self.clock += 1
        self.screen = set(markers)
        return self.flow.tick(match)

    def test_daily_reservations_survive_restart_and_respect_kst_window(self):
        # 23:45 KST. Separate per-day reservations give one attempt per kind.
        self.assertTrue(self.flow.start_if_due(0, club=True, mission=True))
        self.assertEqual(self.flow.kind, "mission")
        self.flow.reset()
        self.assertTrue(self.flow.start_if_due(0, club=True, mission=True))
        self.assertEqual(self.flow.kind, "club")
        self.flow.reset()
        self.assertFalse(self.flow.start_if_due(0, club=True, mission=True))
        self.time += timedelta(days=1)
        self.assertFalse(self.flow.start_if_due(0, club=True, mission=True, blocked=True))
        self.assertTrue(self.flow.start_if_due(0, club=True, mission=True))
        self.flow.reset()
        self.time += timedelta(minutes=15)
        self.assertFalse(self.flow.start_if_due(0, club=True, mission=True))
        self.time += timedelta(hours=23, minutes=30)
        self.assertTrue(self.flow.start_if_due(0, club=True, mission=True))

    def test_manual_flow_verifies_claim_and_donation_in_order(self):
        self.time -= timedelta(hours=5)
        self.assertTrue(self.flow.toggle_next(0))
        self.assertFalse(self.flow.toggle_next(0))
        self.assertTrue(self.flow.toggle_next(0))
        self.assertFalse(self.flow.start_if_due(0, club=False, mission=False))
        self.assertTrue(self.flow.start_if_due(1, club=False, mission=False))
        self.step("Lobby", "MissionNav")
        hub = ("LobbyHub", "LobbyHubClose", "LobbyHubCenter")
        self.step(*hub, "LobbyClaimEnabled")
        self.step(*hub, "LobbyClaimEnabled")
        self.step(*hub, "LobbyClaimDisabled")
        self.assertEqual(self.step("Lobby"), "done")
        self.assertTrue(self.flow.start_if_due(1, club=False, mission=False))
        self.step("Lobby", "Exit")
        self.step("Menu", "ClubTab")
        self.step("Club", "Enabled")
        self.step("Club", "Enabled")
        self.step("Reward", "Confirm")
        self.step("Club", "Disabled", "Zero", "Close")
        self.step("Menu", "ClubTab")
        self.assertEqual(self.step("Lobby", "Exit"), "done")
        self.assertEqual([call.args for call in self.ctx.click.call_args_list],
                         [(228, 525), (740, 106), (827, 37), (827, 37), (604, 525),
                          (782, 294), (543, 390), (827, 37), (208, 161)])
        self.assertFalse(self.flow.start_if_due(1, club=True, mission=True))

    def test_match_hub_layout_requires_unambiguous_enabled_state(self):
        base = {"Hub", "HubCenter", "HubClose"}
        read = lambda *names: (base | set(names)).__contains__
        self.assertIsNone(_mission_step("Claim", read("ClaimDisabled", "ClaimEnabled")))
        self.assertEqual(_mission_step("Claim", read("ClaimEnabled")), ("Verify", 740, 119))
        self.assertEqual(_mission_step("Verify", read("ClaimDisabled")), ("Return", 827, 50))

    def test_midnight_blocks_automatic_claim_then_prevents_recovery_click_repeats(self):
        self.assertTrue(self.flow.start_if_due(1, club=False, mission=True))
        self.step("Lobby", "MissionNav")
        self.time += timedelta(minutes=15)
        self.step("Hub", "HubClose", "HubCenter", "ClaimEnabled")
        self.assertEqual(self.flow.kind, "recovery")
        self.step("Hub", "HubClose", "HubCenter", "Confirm")
        self.step("Hub", "HubClose", "HubCenter")
        self.step("Hub", "HubClose", "HubCenter")
        self.assertEqual(self.step("Lobby", "Exit"), "done")
        self.assertEqual([call.args for call in self.ctx.click.call_args_list], [(228, 525), (827, 50)])
        self.flow.toggle_next(0)
        self.assertFalse(self.flow.start_if_due(1, club=True, mission=True))
        self.assertTrue(self.flow.start_if_due(2, club=True, mission=True))

    def test_incomplete_recovery_never_clicks_unverified_screens(self):
        for screen in ({"Hub", "Close"}, {"LobbyHub", "LobbyHubClose"}, {"Club"},
                       {"Menu", "ClubTab", "Reward"}):
            self.assertIsNone(_recovery_step(screen.__contains__))
        self.assertTrue(self.flow.start_if_due(1, club=True, mission=False))
        self.clock = 16
        self.assertEqual(self.step(), "busy")
        self.assertEqual(self.flow.kind, "recovery")
        self.clock += 15
        self.assertEqual(self.step(), "failed")
        self.assertFalse(self.flow.busy)
        self.ctx.click.assert_not_called()

    def test_focus_loss_pauses_until_return_but_recovery_has_hard_deadline(self):
        self.assertTrue(self.flow.start_if_due(1, club=True, mission=False))
        self.ctx.capture.side_effect = RuntimeError("foreground changed")
        self.clock = 20
        self.assertEqual(self.step(), "busy")
        self.assertEqual(self.flow.kind, "club")
        self.ctx.capture.side_effect = None
        self.step("Lobby", "Exit")
        self.assertEqual(self.flow.kind, "club")
        self.clock += 2
        self.step("Lobby", "Exit")
        self.assertEqual(self.flow.kind, "recovery")
        self.ctx.capture.side_effect = RuntimeError("foreground changed")
        self.clock += 16
        self.assertEqual(self.step(), "failed")
        self.ctx.click.assert_not_called()

    def test_failed_donation_is_never_retried(self):
        self.assertTrue(self.flow.start_if_due(1, club=True, mission=False))
        self.step("Lobby", "Exit")
        self.step("Menu", "ClubTab")
        self.ctx.click.side_effect = RuntimeError("focus")
        self.step("Club", "Enabled")
        self.assertEqual(self.flow.kind, "recovery")
        self.step("Club", "Enabled")
        self.assertEqual(self.ctx.click.call_count, 3)

    def test_BP_verify_and_no_action_after_stop(self):
        screens = [
            {"Open", "Label"}, {"Slider", "ZeroTab"}, {"Slider", "Tab", "Max", "Empty"},
            {"Slider", "Tab", "Max", "Checked", "Confirm"}, {"Open", "Label", "Applied"},
        ]
        bp = BPProtection(self.ctx, clock=lambda: self.clock, pause=lambda _: None)
        bp.markers.read = lambda _: screens.pop(0).__contains__
        self.assertTrue(bp.run())
        self.assertEqual([call.args for call in self.ctx.click.call_args_list],
                         [(516, 477), (610, 305), (698, 333), (548, 426)])
        self.ctx.stop_requested.return_value = True
        self.assertFalse(bp.run())
        self.assertEqual(self.ctx.click.call_count, 4)

    def test_BP_times_out_on_unknown_screen(self):
        def wait(duration):
            self.clock += duration

        bp = BPProtection(self.ctx, clock=lambda: self.clock, pause=wait)
        bp.markers.read = lambda _: set().__contains__
        self.assertFalse(bp.run())
        self.ctx.click.assert_not_called()
        self.ctx.report.assert_called_once()

    def test_release_markers_respect_margin_and_client_aspect(self):
        with assets_directory() as root:
            markers = Markers(root, "bp")
            frame = Image.new("RGB", (960, 540))
            x, y, _ = BP["Label"]
            with Image.open(root / "templates" / "bp_Label.png") as template:
                frame.paste(template, (x + 2, y - 2))
            self.assertTrue(markers.read(frame)("Label"))
            self.assertFalse(markers.read(frame)("Open"))
            self.assertIsNone(markers.read(Image.new("RGB", (800, 600))))

    def test_maintenance_launch_throttle_resume_and_expiry(self):
        game = Mock(return_value=False)
        launch = Mock(return_value=True)
        task = Maintenance(self.ctx, game_present=game, launch=launch, now=lambda: self.time)
        task.markers.read = lambda _: self.screen.__contains__
        local = self.time.astimezone(KST).replace(tzinfo=None) + timedelta(minutes=1)
        task.arm(local)
        self.assertEqual(task.tick(), "wait")
        self.time += timedelta(minutes=1)
        self.assertEqual(task.tick(), "busy")
        self.time += timedelta(seconds=119)
        self.assertEqual(task.tick(), "busy")
        launch.assert_called_once()
        self.time += timedelta(seconds=2)
        task.tick()
        self.assertEqual(launch.call_count, 2)
        game.return_value = True
        self.screen = {"Menu", "ClubTab"}
        self.time += timedelta(seconds=2)
        self.assertEqual(task.tick(), "busy")
        self.ctx.click.assert_called_once_with(208, 161)
        self.time += timedelta(seconds=2)
        self.screen.add("Reward")
        self.assertEqual(task.tick(), "busy")
        self.ctx.click.assert_called_once()
        self.screen = {"Lobby", "Exit"}
        self.time += timedelta(seconds=2)
        self.assertEqual(task.tick(), "resume")
        self.assertTrue(task.armed)
        task.complete()
        self.assertFalse(task.armed)
        task.complete()
        self.ctx.notify.assert_called_once()
        with self.assertRaises(ValueError):
            task.arm(local)
        game.return_value = False
        task.arm(self.time, running=True)
        self.assertTrue(task.interrupted)
        self.time += timedelta(hours=6)
        self.assertEqual(task.tick(running=True), "stop")
        self.assertFalse(task.armed)


class Control:
    def __init__(self, kind, name, *children, enabled=True, visible=True):
        self.element_info = SimpleNamespace(control_type=kind, name=name)
        self._children = children
        self._parent = None
        self._enabled, self._visible = enabled, visible
        for item in children:
            item._parent = self

    def children(self, control_type):
        return [item for item in self._children if item.element_info.control_type == control_type]

    def descendants(self, control_type):
        return self.children(control_type) + [descendant for child in self._children
                                              for descendant in child.descendants(control_type)]

    def parent(self):
        return self._parent

    def is_visible(self):
        return self._visible

    def is_enabled(self):
        return self._enabled


class LauncherTest(unittest.TestCase):
    def test_only_fc_card_and_exactly_one_visible_target(self):
        start = Control("Button", "게임 시작")
        fc = Control("Pane", "", Control("Text", "FC ONLINE"), start)
        other = Control("Pane", "다른 게임", Control("Button", "게임 시작"))
        root = Control("Window", "Nexon Plug", other, fc)
        self.assertIs(_select_start([root]), start)
        ambiguous = Control("Window", "", Control("Button", "FC 온라인 게임시작", enabled=False))
        self.assertIsNone(_select_start([root, ambiguous]))
        self.assertIsNone(_select_start([ambiguous]))
        self.assertIsNone(_select_start([Control("Window", "FC ONLINE", Control("Button", "게임시작"))]))
        self.assertIsNone(_select_start([Control("Window", "", Control("Pane", "FC ONLINE",
                                      Control("Button", "게임시작"), Control("Button", "다른 게임시작")))]))
