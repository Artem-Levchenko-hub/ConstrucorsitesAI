"""Private candidate: completed deploy cycles do not reuse retired evidence."""

import importlib.util
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace


@unittest.skipUnless(hasattr(os, "geteuid") and os.geteuid() == 0, "root-owned fixture")
class CaptureCyclesTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "capture_observer", Path(__file__).with_name("generation-reader-inventory.py")
        )
        self.observer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.observer)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state, self.runtime = self.root / "state", self.root / "run"
        self.state.mkdir(mode=0o755)
        self.runtime.mkdir(mode=0o755)
        for name, value in (("STATE", self.state), ("DIRECTORY", self.runtime)):
            patcher = patch.object(self.observer, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def owner(self, number, release="a"):
        return {"version": 1, "release_sha": release * 40,
                "owner_nonce": f"00000000-0000-4000-8000-{number:012d}"}

    def files(self):
        return {str(path.relative_to(self.root)): path.read_bytes()
                for path in self.root.rglob("*") if path.is_file()}

    def capture(self, owner, inventory):
        with patch.object(self.observer, "supervisor_inventory", return_value=inventory) as collect:
            self.observer.capture_owned_release(owner, "core")
            self.assertEqual(collect.call_count, 1)
        return self.observer.capture_context(owner)

    def test_same_sha_two_completed_cycles_preserve_old_bytes_and_new_capture(self):
        old = self.owner(1)
        first = self.capture(old, {"processes": ["first"]})
        self.observer.clear_owned_inhibit(old)
        history = self.files()
        new = self.owner(2)
        second = self.capture(new, {"processes": ["second"]})
        self.assertNotEqual(first["capture_sha256"], second["capture_sha256"])
        self.observer.clear_owned_inhibit(new)
        self.assertEqual(len(self.files()), 2)
        for name, data in history.items():
            self.assertEqual(self.files()[name], data)

    def test_partial_retry_preserves_exact_owner_capture_hash_without_rescan(self):
        owner = self.owner(1)
        first = self.capture(owner, {"processes": ["old"]})
        before = self.files()
        with patch.object(self.observer, "supervisor_inventory", side_effect=AssertionError("recapture")):
            self.observer.capture_owned_release(owner, "core")
        self.assertEqual(self.observer.capture_context(owner), first)
        self.assertEqual(self.files(), before)

    def test_foreign_nonce_or_mismatched_capture_denies_without_changes(self):
        owner = self.owner(1)
        self.capture(owner, {"processes": ["old"]})
        for call in (self.observer.capture_owned_release, self.observer.capture_context,
                     self.observer.clear_owned_inhibit):
            before = self.files()
            with self.subTest(call=call.__name__), self.assertRaises((ValueError, FileNotFoundError)):
                if call == self.observer.capture_owned_release:
                    call(self.owner(2), "core")
                else:
                    call(self.owner(2))
            self.assertEqual(self.files(), before)
        self.observer.clear_owned_inhibit(owner)
        # Correct path but foreign immutable payload must never be overwritten.
        path = next(self.state.glob("capture-*.json"))
        value = json.loads(path.read_bytes())
        value["owner"] = self.owner(3)
        path.write_text(json.dumps(value))
        before = self.files()
        with self.assertRaises(ValueError):
            self.observer.capture_owned_release(owner, "core")
        self.assertEqual(self.files(), before)

    def test_rollback_older_sha_creates_fresh_cycle_after_later_completed_release(self):
        contexts = []
        history = {}
        for number, release in ((1, "a"), (2, "b"), (3, "a")):
            owner = self.owner(number, release)
            contexts.append(self.capture(owner, {"current_process": number}))
            self.observer.clear_owned_inhibit(owner)
            for name, data in history.items():
                self.assertEqual(self.files()[name], data)
            history = self.files()
        self.assertEqual(len(history), 3)
        self.assertEqual(len({item["capture_sha256"] for item in contexts}), 3)

    def test_verify_target_uses_current_nonce_capture_not_prior_same_sha(self):
        old, current = self.owner(1), self.owner(2)
        self.capture(old, {"generation": "old"})
        self.observer.clear_owned_inhibit(old)
        self.capture(current, {"generation": "current"})
        (self.state / "config.json").write_text(json.dumps({
            "host": "commerce", "release_sha": current["release_sha"],
        }))
        (self.runtime / "current.json").write_text(json.dumps({
            "release_sha": current["release_sha"], "state": "ready",
            "digest": "b" * 64, "epoch": current["owner_nonce"],
        }))
        with (patch.object(self.observer, "verify_retired") as retired,
              patch.object(self.observer, "supervisor_inventory", return_value={"new": True}),
              patch.object(self.observer, "check_live"),
              patch.object(self.observer, "redis_connection"),
              contextlib.redirect_stdout(io.StringIO())):
            self.observer.run_action(SimpleNamespace(
                action="verify-target", host="commerce", release=current["release_sha"],
                owner_nonce=current["owner_nonce"],
            ))
        retired.assert_called_once_with({"generation": "current"}, {"new": True})


if __name__ == "__main__":
    unittest.main()
