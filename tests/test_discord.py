from collections import defaultdict, deque
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import subprocess
import threading
import unittest

from fc_manager import discord_io as io
from fc_manager.discord import DiscordService
from fc_manager.discord_watch import alert


TOKEN = "t" * 43
ORIGIN = "https://relay.invalid"
LINK = ORIGIN + "/#" + "p" * 43
OWNER = "123456789012345678"
WEBHOOK = "https://discord.com/api/webhooks/123456/example"


class Transport:
    def __init__(self):
        self.requests = []
        self.responses = defaultdict(deque)

    def __call__(self, url, body, token=None, timeout=6):
        records = self.responses[url]
        reply = records.popleft() if records else (200, {})
        self.requests.append((url, body, token, timeout))

        class Fake:
            def __init__(self):
                self.timeout = timeout
                self.cancelled = threading.Event()

            def run(self):
                if callable(reply):
                    return reply(self.cancelled)
                return reply

            def cancel(self):
                self.cancelled.set()

        return Fake()


class DiscordTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.http = Transport()
        self.now = 100
        self.logs = []
        self.commands = []
        self.service = self.client()
        self.addCleanup(self.service.close)

    def client(self):
        return DiscordService(self.root, lambda: "자동 진행 중",
                              lambda cmd: self.commands.append(cmd) or "성공", self.logs.append,
                              protect=lambda value: value.encode().hex(),
                              unprotect=lambda cipher: bytes.fromhex(cipher).decode(),
                              exchange=self.http, clock=lambda: self.now, wall=lambda: self.now,
                              watcher=lambda root: root / "clean")

    def finish(self, lane):
        self.service.jobs[lane].thread.join(1)
        self.assertFalse(self.service.jobs[lane].thread.is_alive())
        self.service.tick()

    def pair(self):
        self.http.responses[ORIGIN + "/pair"].append((200, {"token": TOKEN, "owner": OWNER}))
        self.service.pair(LINK)
        self.finish("pair")

    def test_url_and_device_validation(self):
        self.assertEqual(io.pairing_link(LINK), (ORIGIN, "p" * 43))
        for address in (LINK.replace("https:", "http:"), LINK.replace("relay", "x@relay"),
                        LINK.replace("/#", "/other#"), LINK.replace("/#", "/?arg#"),
                        LINK + "!", LINK.replace("/#", ":99999/#"), LINK.replace("relay", "r\\elay")):
            with self.subTest(address=address), self.assertRaises(ValueError):
                io.pairing_link(address)
        for address in (WEBHOOK + "?foo", WEBHOOK.replace("discord.com", "discord.com.invalid"),
                        WEBHOOK.replace("https", "http")):
            with self.assertRaises(ValueError):
                self.service.save_webhook(address, True)

    def test_pair_poll_ack_and_unauthorized(self):
        cmd = {"id": OWNER, "name": "finish", "expires": 101000}
        self.http.responses[ORIGIN + "/poll"].extend([(200, {"command": cmd}),
                                                     (200, {"command": cmd}), (401, {})])
        self.pair()
        self.assertEqual(self.http.requests[0], (ORIGIN + "/pair", {"code": "p" * 43}, None, 6))
        self.assertNotIn(TOKEN, self.service.device_path.read_text())
        self.finish("poll")
        url, body, token, timeout = self.http.requests[-1]
        self.assertEqual((url, token, timeout), (ORIGIN + "/poll", TOKEN, 6))
        self.assertRegex(body["session"], "^[0-9a-f]{32}$")
        self.assertEqual((body["ack"], body["result"]), (OWNER, "성공"))
        self.finish("poll")
        self.assertEqual(self.commands, ["finish"])
        self.now += 2
        self.service.tick()
        self.finish("poll")
        self.assertIsNone(self.service.device)
        self.assertFalse(self.service.device_path.exists())

    def test_expired_and_unknown_commands(self):
        self.pair()
        self.finish("poll")
        for cmd, msg in [({"id": OWNER, "name": "stop", "expires": 99999}, "유효 시간"),
                         ({"id": str(int(OWNER) + 1), "name": "arbitrary", "expires": 111000}, "지원하지")]:
            self.service._polled(200, {"command": cmd})
            self.assertIn(msg, self.service.result)
        self.assertEqual(self.commands, [])

    def test_530_allows_relocation_only_under_original_owner(self):
        self.http.responses[ORIGIN + "/poll"].append((530, {}))
        self.pair()
        self.finish("poll")
        self.assertTrue(self.service.tunnel_error)
        self.assertEqual(self.service.poll_due, self.now + 5)
        new = "https://new.invalid"
        self.http.responses[new + "/pair"].extend([(200, {"token": TOKEN, "owner": "9" * 18}),
                                                   (200, {"token": "u" * 43, "owner": OWNER})])
        self.service.pair(LINK.replace(ORIGIN, new))
        self.finish("pair")
        self.assertEqual(self.service.device.origin, ORIGIN)
        self.service.pair(LINK.replace(ORIGIN, new))
        self.finish("pair")
        self.assertEqual(self.service.device.origin, new)
        self.assertFalse(self.service.tunnel_error)
        with self.assertRaises(ValueError):
            self.service.pair(LINK)

    def test_legacy_settings_retries_and_clean_shutdown_marker(self):
        self.service.settings_path.write_text(json.dumps({"DiscordEnabled": True, "OperatorDiagnostics": True,
            "EncryptedWebhook": WEBHOOK.encode().hex(), "other": 17}), encoding="utf-8-sig")
        service = self.client()
        self.addCleanup(service.close)
        self.assertTrue(service.enabled and service.operator_enabled)
        service.save_webhook(WEBHOOK, True)
        settings = json.loads(service.settings_path.read_text())
        self.assertEqual(settings["other"], 17)
        self.assertNotIn(WEBHOOK, service.settings_path.read_text())
        self.http.responses[WEBHOOK].extend([(530, {}), (502, {}), (204, {})])
        service.notify("경기 결과", "경기 결과 메시지")
        for backoff in (2, 4, 0):
            service.tick()
            service.jobs["personal"].thread.join(1)
            service.tick()
            self.now += backoff
        self.assertIn("성공", service.personal_status)
        self.assertEqual(len(self.http.requests), 3)
        self.assertIn("경기 결과", self.http.requests[-1][1]["content"])
        service.close()
        self.assertTrue((self.root / "clean").is_file())

    def test_snapshot_consent_manual_report_receipt_and_retry(self):
        self.pair()
        self.finish("poll")
        folder = self.service.folder
        folder.mkdir()
        (folder / "진단정보.txt").write_text(WEBHOOK + "\nBearer credential", encoding="utf-8-sig")
        screenshot = folder / "image.png"
        screenshot.write_bytes(b"\x89PNG\r\n\x1a\n" + bytes(16))
        self.service.report("코치 오류", screenshot)
        snapshot = json.loads((folder / "last-error.json").read_text())
        self.assertIn("png", snapshot)
        self.assertNotIn("credential", snapshot["text"])
        self.assertNotIn(WEBHOOK, snapshot["text"])
        self.assertFalse(self.service.reports)
        self.http.responses[ORIGIN + "/diagnostics"].extend([(429, {}), (200, {"delivered": True})])
        self.service._polled(200, {"command": {"id": OWNER, "name": "upload-error", "expires": 110000}})
        self.service.tick()
        self.assertTrue(self.service.manual_pending)
        self.finish("report")
        self.assertIn("429", self.service.manual_status)
        self.now += 16
        self.service.tick()
        self.finish("report")
        self.assertFalse(self.service.manual_pending)
        self.assertIn("전송했습니다", self.service.manual_status)
        posts = [rec for rec in self.http.requests if rec[0].endswith("/diagnostics")]
        self.assertEqual(posts[0], posts[1])
        self.assertNotEqual(posts[0][1]["id"], snapshot["id"])
        self.assertEqual(posts[0][2:], (TOKEN, 14))

    def test_forget_cancels_active_requests_and_discards_old_commands(self):
        gate = threading.Event()
        def slow(cancel):
            gate.set()
            cancel.wait(1)
            return 200, {"command": {"id": OWNER, "name": "start", "expires": 120000}}
        self.http.responses[ORIGIN + "/poll"].append(slow)
        self.pair()
        self.assertTrue(gate.wait(1))
        job = self.service.jobs["poll"]
        self.service.forget()
        job.thread.join(1)
        self.assertTrue(job.exchange.cancelled.is_set())
        self.assertFalse(job.thread.is_alive())
        self.service.tick()
        self.assertEqual(self.commands, [])
        self.assertFalse(self.service.jobs)

    @unittest.skipUnless(os.name == "nt", "requires Windows current-user DPAPI and Windows PowerShell")
    def test_windows_powershell_dpapi_migration(self):
        def powershell(script, value):
            env = {**os.environ, "FC_DISCORD_TEST_INPUT": value}
            return subprocess.check_output(["powershell.exe", "-NoProfile", "-NonInteractive",
                                            "-Command", script], text=True, env=env).strip()
        legacy = powershell("ConvertFrom-SecureString (ConvertTo-SecureString "
                            "$env:FC_DISCORD_TEST_INPUT -AsPlainText -Force)", WEBHOOK)
        self.assertEqual(io.unprotect(legacy), WEBHOOK)
        converted = io.protect(WEBHOOK)
        self.assertEqual(powershell("([Net.NetworkCredential]::new('test', "
                                    "(ConvertTo-SecureString $env:FC_DISCORD_TEST_INPUT))).Password",
                                    converted), WEBHOOK)

    def test_exit_alert_obeys_current_settings_and_retries(self):
        self.service.save_webhook(WEBHOOK, True)
        self.http.responses[WEBHOOK].extend([(530, {}), (204, {})])
        delays = []
        alert(self.root, 9, unprotect=self.service.unprotect, exchange=self.http, sleep=delays.append)
        self.assertEqual(delays, [2])
        self.assertIn("종료 코드: 9", self.http.requests[-1][1]["content"])
        self.service.save_webhook(WEBHOOK, False)
        alert(self.root, 9, unprotect=self.service.unprotect, exchange=self.http)
        self.assertEqual(len(self.http.requests), 2)


if __name__ == "__main__":
    unittest.main()
