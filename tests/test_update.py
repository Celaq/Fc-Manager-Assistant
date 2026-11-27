import hashlib
import io
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from fc_manager import update


class UpdateTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / update.LAUNCHER).write_text("original launcher")
        (self.root / update.ACTIVE).write_text("3.3.4")
        runtime = self.root / "payloads/3.3.4/runtime"
        runtime.mkdir(parents=True)
        (runtime / "pythonw.exe").touch()
        self.personal = self.root / "config.json"
        self.personal.write_text("my settings")

    def bundle(self, extra=None, version="3.3.5"):
        contents = io.BytesIO()
        with zipfile.ZipFile(contents, "w") as archive:
            archive.writestr(update.ACTIVE, version)
            archive.writestr(update.LAUNCHER, "stable")
            for name in update.REQUIRED:
                archive.writestr(f"payloads/{version}/{name}", b"new")
            if extra:
                archive.writestr(extra, b"bad")
        return contents.getvalue()

    def release(self, data):
        return update.Release("3.3.5", update.asset_url("3.3.5"), hashlib.sha256(data).hexdigest(), "")

    def prepare(self, data):
        release = self.release(data)
        with patch.object(update, "_open_url", return_value=io.BytesIO(data)):
            return release, update.prepare_update(release, self.root)

    def test_manifest_and_channel(self):
        release = self.release(b"example")
        fields = dict(schemaVersion=1, channel=update.CHANNEL, **vars(release))
        def check(current="3.3.4"):
            with patch.object(update, "_open_url", return_value=io.BytesIO(json.dumps(fields).encode())):
                return update.check_for_update(current)
        self.assertEqual(release, check())
        self.assertIsNone(check("3.3.5"))
        fields["channel"] = "powershell"
        with self.assertRaises(ValueError):
            check()
        fields["channel"] = update.CHANNEL
        fields["url"] += "?mirror=1"
        with self.assertRaises(ValueError):
            check()

    def test_atomic_switch_preserves_user_data_and_previous_runtime(self):
        release, _ = self.prepare(self.bundle())
        update.activate_update(release, self.root)
        self.assertEqual(update._installed_version(self.root), "3.3.5")
        self.assertTrue((self.root / "payloads/3.3.4/runtime/pythonw.exe").exists())
        self.assertEqual(self.personal.read_text(), "my settings")
        self.assertEqual((self.root / update.LAUNCHER).read_text(), "original launcher")
        with self.assertRaises(ValueError):
            update.activate_update(release, self.root)

    def test_interrupted_activation_can_retry(self):
        release, _ = self.prepare(self.bundle())
        with patch.object(os, "replace", side_effect=OSError("simulated interruption")):
            with self.assertRaises(OSError):
                update.activate_update(release, self.root)
        self.assertEqual(update._installed_version(self.root), "3.3.4")
        update.activate_update(release, self.root)
        self.assertEqual(update._installed_version(self.root), "3.3.5")

    def test_bad_archive_keeps_old_version(self):
        for bad in ["config.json", "payloads/3.3.5/../config.json", "payloads/3.3.5/a:stream",
                    "payloads/3.3.5/nul.txt", "payloads/3.3.5/RUNTIME/python.exe"]:
            with self.subTest(name=bad):
                release, _ = self.prepare(self.bundle(bad))
                with self.assertRaises(ValueError):
                    update.activate_update(release, self.root)
                self.assertEqual(update._installed_version(self.root), "3.3.4")
                self.assertEqual(self.personal.read_text(), "my settings")
        release, _ = self.prepare(self.bundle(version="3.3.6"))
        with self.assertRaises(ValueError):
            update.activate_update(release, self.root)

    def test_digest_rechecked_before_install(self):
        release, zip_path = self.prepare(self.bundle())
        zip_path.write_bytes(b"broken")
        with self.assertRaises(ValueError):
            update.activate_update(release, self.root)
        self.assertEqual(update._installed_version(self.root), "3.3.4")


if __name__ == "__main__":
    unittest.main()
