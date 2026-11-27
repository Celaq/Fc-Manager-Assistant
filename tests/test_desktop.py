from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from queue import Queue
from tempfile import TemporaryDirectory
from threading import Event
from time import monotonic, sleep
from unittest import TestCase, skipUnless
import os

from fc_manager.controller import Controller, Options, Presence
from fc_manager.migrate import import_existing, import_neighbor
from fc_manager.runtime import Context, assets_directory


class Game:
    def __init__(self):
        self.score = None

    def game_present(self):
        return False

    def focus_game(self):
        return True

    def focus_game_for_maintenance(self):
        return False

    def poll_hotkeys(self):
        return set()

    def read_tier(self, image):
        return ""

    def read_rank(self, image):
        return self.score

    def capture(self):
        from PIL import Image
        return Image.new("RGB", (1280, 720))

    def pin_client(self):
        return nullcontext()

    def manager_frame_scope(self, game_only):
        return nullcontext()


class IntegrationTests(TestCase):
    def setUp(self):
        self.data = Path(self.enterContext(TemporaryDirectory()))
        self.assets = self.enterContext(assets_directory())
        self.game = Game()
        self.ctx = Context(self.game, self.assets, self.data, Queue(), Event())
        self.controller = Controller(self.ctx)

    def test_double_read_invalidation(self):
        self.game.score = 3000
        self.assertEqual(self.controller._confirm_rank(True), 3000)
        self.game.score = None
        with self.assertRaises(ValueError):
            self.controller._confirm_rank(True)
        with self.assertRaises(ValueError):
            self.controller.manager.start(parking=True, target=4000)

    def test_exit_debounce_and_relaunch(self):
        state = Presence(False)
        for now, exists, exited in [(0, False, False), (2, True, False), (3, False, False),
                                    (5, True, False), (7, False, False), (9, False, False),
                                    (10, False, True), (13, False, False)]:
            self.assertEqual(state.exited(exists, now), exited)

    def test_reservation_worker_ownership_and_cancel(self):
        reserve = datetime.now(timezone.utc) + timedelta(hours=1)
        self.controller.launch("manager", (Options(), reserve))
        try:
            with self.assertRaises(ValueError):
                self.controller.launch("bp")
            until = monotonic() + 2
            while not self.controller.maintenance.armed and monotonic() < until:
                sleep(.01)
            self.assertTrue(self.controller.maintenance.armed)
            self.controller.submit("cancel_reservation")
            self.controller.thread.join(2)
            self.assertFalse(self.controller.busy)
            self.assertFalse(self.controller.manager.running)
        finally:
            self.controller.stop()

    def test_migration_retry_preserves_personal_data(self):
        old = self.data / "FC_Manager_v3.3.4"
        new = self.data / "python"
        old.mkdir()
        new.mkdir()
        (old / "FCManager.ps1").touch()
        (old / "settings.json").write_bytes(b"encrypted-old")
        (old / "stats.json").write_bytes(b"scores")
        icons = old / ".coach-icons"
        icons.mkdir()
        (icons / "training_trait_icon_012.png").write_bytes(b"image")
        self.assertEqual(import_neighbor(new), 3)
        (new / "settings.json").write_bytes(b"encrypted-new")
        self.assertEqual(import_existing(old, new), 0)
        self.assertEqual((new / "settings.json").read_bytes(), b"encrypted-new")
        self.assertEqual((old / "settings.json").read_bytes(), b"encrypted-old")

    @skipUnless(os.environ.get("DISPLAY") or os.name == "nt", "graphical desktop required")
    def test_gui_commands_through_normal_entry_points(self):
        import tkinter as tk
        from fc_manager.app import Desktop
        root = tk.Tk()
        app = Desktop(root, self.game, self.assets, self.data)
        try:
            root.update_idletasks()
            self.assertEqual(len(app.coach.boxes), 7)
            self.assertIn("먼저", app.remote_command("start"))
            app.start_button.invoke()
            self.assertEqual(app.active, "manager")
            app.stop_button.invoke()
            app.control.thread.join(2)
            self.assertFalse(app.control.busy)
        finally:
            app.close()
            root.mainloop()
