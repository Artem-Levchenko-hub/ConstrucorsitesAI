"""Root CLI read paths cannot repair or create persistent observer state."""

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


@unittest.skipUnless(hasattr(os, "geteuid") and os.geteuid() == 0, "root file ownership")
class ReadonlyObserverTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "readonly_observer", Path(__file__).with_name("generation-reader-inventory.py")
        )
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def invoke(self, root, action="read"):
        with (
            patch.object(self.module, "STATE", root / "state"),
            patch.object(self.module, "DIRECTORY", root / "run"),
            patch("sys.argv", ["observer", action]),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            self.module.main()
            return output.getvalue()

    @staticmethod
    def snapshot(root):
        return {
            str(path.relative_to(root)): (path.stat().st_mode, path.read_bytes() if path.is_file() else None)
            for path in root.rglob("*")
        }

    def setup_files(self, root):
        for directory in (root / "state", root / "run"):
            directory.mkdir(mode=0o755)
        (root / "state/observer.lock").write_bytes(b"")
        (root / "state/config.json").write_text('{"host":"core"}')
        (root / "run/current.json").write_text('{"state":"deny"}')

    def test_missing_state_lock_or_mirror_denies_without_writes(self):
        for case in ("state", "lock", "mirror"):
            for action in ("read", "verify-current", "verify-target", "verify-owner", "collect"):
                with self.subTest(case=case, action=action), tempfile.TemporaryDirectory() as temp:
                    root = Path(temp)
                    if case != "state":
                        self.setup_files(root)
                    if case == "lock":
                        (root / "state/observer.lock").unlink()
                    if case == "mirror":
                        (root / "state/rollout-inhibit.json").write_text(json.dumps({
                            "version": 1, "release_sha": "a" * 40,
                            "owner_nonce": "11111111-1111-4111-8111-111111111111",
                        }))
                    before = self.snapshot(root)
                    with self.assertRaises((ValueError, FileNotFoundError)):
                        self.invoke(root, action)
                    self.assertEqual(self.snapshot(root), before)

    def test_existing_read_is_readonly_and_does_not_restore(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.setup_files(root)
            before = self.snapshot(root)
            with patch.object(self.module, "restore_inhibit", side_effect=AssertionError("write path")):
                self.assertEqual(json.loads(self.invoke(root)), {"state": "deny"})
            self.assertEqual(self.snapshot(root), before)

    def test_retry_preserves_original_capture_and_rejects_foreign_owner(self):
        owner = {"version": 1, "release_sha": "a" * 40,
                 "owner_nonce": "11111111-1111-4111-8111-111111111111"}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.setup_files(root)
            capture = {"owner": owner, "inventory": {"original": True}}
            (root / "state" / self.module.capture_name(owner)).write_text(json.dumps(capture))
            with (patch.object(self.module, "STATE", root / "state"),
                  patch.object(self.module, "DIRECTORY", root / "run"),
                  patch.object(self.module, "supervisor_inventory", side_effect=AssertionError("no recapture"))):
                self.module.capture_owned_release(owner, "core")
                self.assertEqual(self.module.capture_context(owner)["capture_sha256"], self.module.digest(capture))
                before = self.snapshot(root)
                foreign = {**owner, "owner_nonce": "22222222-2222-4222-8222-222222222222"}
                with self.assertRaises(ValueError):
                    self.module.capture_owned_release(foreign, "core")
                self.assertEqual(self.snapshot(root), before)


if __name__ == "__main__":
    unittest.main()
