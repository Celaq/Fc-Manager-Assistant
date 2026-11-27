"""Verify frame targeting and OCR parsing without sending real desktop input."""

import ctypes
import os
import threading
import unittest
from dataclasses import replace
from unittest.mock import MagicMock, patch

from PIL import Image, ImageDraw, ImageFont

from fc_manager import windows, windows_ocr


class WindowTests(unittest.TestCase):
    def setUp(self):
        self.native = MagicMock()
        self.client = windows._Client(123, 45, -1500, 130, 1280, 720)
        self.native.client.return_value = self.client
        self.native.window_at.return_value = self.client.hwnd
        self.native.key_state.return_value = 0
        self.native.click_pair.return_value = 2
        self.pos = (20, 40)
        self.native.cursor.side_effect = lambda: self.pos
        self.native.move.side_effect = lambda *p: setattr(self, "pos", p)
        self.native.register_f8.return_value = False
        with patch.object(windows, "_Win32", return_value=self.native):
            self.game = windows.WindowsGame()
        self.addCleanup(self.game.close)
        self.grab = self.enter_patch(patch.object(windows.ImageGrab, "grab",
                                                 return_value=Image.new("RGB", (1280, 720))))
        self.sleep = self.enter_patch(patch.object(windows.time, "sleep"))

    def enter_patch(self, context):
        self.addCleanup(context.stop)
        return context.start()

    def test_capture_physical_client_and_input_on_negative_display(self):
        self.assertEqual(self.game.capture().size, (1280, 720))
        self.grab.assert_called_once_with(bbox=(-1500, 130, -220, 850), all_screens=True)
        self.game.click(480, 270)
        self.assertEqual(self.native.move.call_args_list[0].args, (-860, 490))
        self.native.click_pair.assert_called_once()
        self.assertEqual(self.pos, (20, 40))

    def test_reject_foreground_or_geometry_change_during_capture(self):
        self.native.client.side_effect = [self.client, replace(self.client, top=100)]
        with self.assertRaises(windows.WindowUnavailable):
            self.game.capture()
        self.native.client.side_effect = None
        with self.assertRaises(windows.WindowUnavailable):
            self.game.key("space")
        self.native.key_event.assert_not_called()

    def test_opt_out_matches_only_its_own_visible_foreground_frame(self):
        other = replace(self.client, hwnd=888, pid=80000)
        current = other

        def active(game_only=True):
            return current if not game_only or current == self.client else None

        self.native.client.side_effect = active
        with self.assertRaises(windows.WindowUnavailable):
            self.game.capture()
        with self.game.manager_frame_scope(game_only=False):
            self.game.capture()
            self.assertFalse(self.game.foreground_game())
            self.assertFalse(self.game.captured_game())
            self.game.key("space")
            current = self.client
            self.assertFalse(self.game.captured_game())
            with self.assertRaises(windows.WindowUnavailable):
                self.game.key("space")
            self.game.capture()
            self.assertTrue(self.game.captured_game())
        with self.assertRaises(windows.WindowUnavailable):
            self.game.key("space")
        current = other
        with self.assertRaises(windows.WindowUnavailable):
            self.game.capture()
        with self.game.manager_frame_scope(game_only=True):
            with self.assertRaises(windows.WindowUnavailable):
                self.game.capture()

    def test_opt_out_never_extends_to_pinned_owner_or_another_thread(self):
        current = self.client
        self.native.client.side_effect = lambda game_only=True: current if not game_only or current == self.client else None
        with self.game.pin_client(), self.game.manager_frame_scope(False):
            self.game.capture()
            current = replace(self.client, hwnd=77)
            with self.assertRaises(windows.WindowUnavailable):
                self.game.capture()
        with self.game.manager_frame_scope(False):
            self.game.capture()
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=1) as worker:
                with self.assertRaises(windows.WindowUnavailable):
                    worker.submit(self.game.capture).result()

    def test_pinned_session_never_adopts_a_relaunched_game(self):
        with self.game.pin_client():
            self.game.capture()
            self.native.client.return_value = replace(self.client, pid=900)
            with self.assertRaises(windows.WindowUnavailable):
                self.game.capture()
            with self.assertRaises(windows.WindowUnavailable):
                self.game.key("space")
        self.game.capture()
        self.game.key("space")
        self.assertEqual(self.native.key_event.call_count, 2)

    def test_click_does_not_hit_replaced_target(self):
        self.game.capture()
        self.native.window_at.side_effect = [self.client.hwnd, 999]
        with self.assertRaises(windows.WindowUnavailable):
            self.game.click(480, 270)
        self.native.click_pair.assert_not_called()

    def test_click_fallback_or_partial_send(self):
        self.game.capture()
        self.native.click_pair.return_value = 0
        self.game.click(100, 200)
        self.assertEqual([call.args for call in self.native.mouse_event.call_args_list], [(2,), (4,)])
        self.native.mouse_event.reset_mock()
        self.native.click_pair.return_value = 1
        self.game.click(100, 200)
        self.native.mouse_event.assert_called_once_with(4)

    def test_both_s_pulses_and_holds(self):
        self.game.capture()
        self.game.key("S")
        self.assertEqual(
            [(call.args[0], call.kwargs["virtual"], call.kwargs["release"])
             for call in self.native.key_event.call_args_list],
            [(31, False, False), (31, False, True), (83, True, False), (83, True, True)]
        )
        self.assertEqual([call.args[0] for call in self.sleep.call_args_list], [.14, .08, .10])

    def test_release_key_then_abandon_second_pulse_on_focus_loss_or_stop(self):
        for cause in ("focus", "stop"):
            with self.subTest(cause=cause):
                self.native.client.return_value = self.client
                self.game.stop_requested = None
                self.native.key_event.reset_mock()
                self.game.capture()

                def interrupt(_):
                    if cause == "focus":
                        self.native.client.return_value = None
                    else:
                        self.game.stop_requested = lambda: True

                self.sleep.side_effect = interrupt
                with self.assertRaises(windows.WindowUnavailable):
                    self.game.key("S")
                self.assertEqual([c.kwargs["release"] for c in self.native.key_event.call_args_list], [False, True])

    def test_hotkeys_emit_rising_edges_and_input_guard_relays_f12_press(self):
        states = {0x77: 0x8000, 0x7B: 0}
        self.native.key_state.side_effect = lambda vk: states.get(vk, 0)
        self.assertEqual(self.game.poll_hotkeys(), {"F8"})
        self.assertEqual(self.game.poll_hotkeys(), set())
        self.game.capture()
        states[0x7B] = 0x8000
        with self.assertRaises(windows.WindowUnavailable):
            self.game.move(123, 20)
        self.native.move.assert_not_called()
        states.clear()
        self.assertEqual(self.game.poll_hotkeys(), {"F12"})
        self.assertEqual(self.game.poll_hotkeys(), set())

    def test_registered_f8_is_consumed_on_own_thread_and_unregistered_on_close(self):
        owner_threads = []
        messages = iter([True])
        native = self.native

        def register():
            owner_threads.append(threading.get_ident())
            return True

        def unregister():
            owner_threads.append(threading.get_ident())

        native.register_f8.side_effect = register
        native.unregister_f8.side_effect = unregister
        native.consume_registered_f8.side_effect = lambda: next(messages, False)
        native.key_state.side_effect = lambda vk: 0x8000 if vk == 0x77 else 0
        with patch.object(windows, "_Win32", return_value=native):
            registered = windows.WindowsGame()
        self.addCleanup(registered.close)
        self.assertTrue(registered._f8.pending.wait(timeout=2))
        self.assertEqual(registered.poll_hotkeys(), {"F8"})
        self.assertEqual(registered.poll_hotkeys(), set())
        self.assertNotIn((0x77,), [c.args for c in native.key_state.call_args_list])
        registered.close()
        self.assertEqual(len(owner_threads), 2)
        self.assertEqual(owner_threads[0], owner_threads[1])
        self.assertNotEqual(owner_threads[0], threading.get_ident())

    def test_unattended_focus_guard(self):
        self.native.client.return_value = None
        self.native.pid.return_value = 80000
        self.native.process_name.return_value = "notepad.exe"
        self.native.find_game_window.return_value = self.client.hwnd
        self.assertFalse(self.game.focus_game_for_maintenance())
        self.native.activate.assert_not_called()
        self.native.client.side_effect = [None, self.client]
        self.native.process_name.return_value = "nexonplug.exe"
        self.assertTrue(self.game.focus_game_for_maintenance())
        self.native.activate.assert_called_once_with(self.client.hwnd)

    def test_send_input_abi_sizes(self):
        self.assertEqual(ctypes.sizeof(windows._Input), 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)


class OcrTests(unittest.TestCase):
    def test_rank_requires_one_plausible_score(self):
        for text, expected in [("1,234", 1234), ("1234", 1234), ("12345", None),
                               ("0984", None), ("1234 5678", None), ("125", None)]:
            with self.subTest(text=text):
                self.assertEqual(windows_ocr.parse_rank(text), expected)

    def test_rank_accepts_only_recognized_lobby(self):
        image = Image.new("RGB", (1280, 720))
        with patch.object(windows_ocr, "ocr_lines", return_value=["3,540"]) as ocr:
            self.assertIsNone(windows_ocr.read_rank(image))
            ocr.assert_not_called()
            image.putpixel((242, 104), (255, 255, 255))
            image.putpixel((492, 214), (12, 2, 82))
            self.assertEqual(windows_ocr.read_rank(image), 3540)
            crop, language = ocr.call_args.args
            self.assertEqual((crop.size, language), ((480, 224), "en-US"))

    def test_tier_classification_ignores_noise_and_refuses_conflicts(self):
        for lines, expected in [(["등 급 승격 성공!"], "승급"), (["슈퍼챔피언스로 승급하셨습니다."], "승급"),
                                (["강등되었습니다."], "강등"), (["잔류확정"], "잔류"),
                                (["강등", "승급"], ""), (["승급까지 2승"], "")]:
            with self.subTest(lines=lines):
                self.assertEqual(windows_ocr.parse_tier(lines), expected)

    def test_tier_crop_excludes_edges_and_caps_width(self):
        image = Image.new("RGB", (3840, 2160), "white")
        ImageDraw.Draw(image).rectangle((575, 409, 3265, 1840), fill="blue")
        with patch.object(windows_ocr, "ocr_lines", return_value=["승급"]) as ocr:
            self.assertEqual(windows_ocr.read_tier(image), "승급")
        crop, language = ocr.call_args.args
        self.assertEqual((crop.width, language), (2000, "ko-KR"))
        self.assertEqual(crop.getextrema(), ((0, 0), (0, 0), (255, 255)))

    @unittest.skipUnless(os.name == "nt", "Windows OCR language pack required")
    def test_installed_windows_engine_reads_synthetic_rank(self):
        image = Image.new("L", (480, 224), 255)
        ImageDraw.Draw(image).text((20, 20), "3,540", font=ImageFont.truetype("arial.ttf", 80), fill=0)
        try:
            lines = windows_ocr.ocr_lines(image, "en-US")
        except windows_ocr.OcrUnavailable as exc:
            self.skipTest(str(exc))
        self.assertEqual(windows_ocr.parse_rank(" ".join(lines)), 3540)


if __name__ == "__main__":
    unittest.main()
