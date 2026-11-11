"""Maintenance receipt publication fails closed on stale or incomplete evidence."""

import importlib.util
import json
import os
import stat
import sys
import tempfile
import unittest
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import UUID

_RUNTIME = "yleum_orchestrator.services.machine_startup"


def _operator():
    spec = importlib.util.spec_from_file_location(
        "source_guard_recovery_operator",
        Path(__file__).with_name("record-source-guard-recovery.py"),
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class SourceGuardRecoveryOperatorTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "linux", "Linux process identity")
    def test_other_controller_checkout_is_denied_before_its_settings_are_read(self):
        gate = _operator()._release_gate()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected, foreign = root / "expected", root / "foreign"
            expected.mkdir()
            foreign.mkdir()
            proc = root / "123"
            proc.mkdir()
            (proc / "cmdline").write_bytes(b"yleum_orchestrator")
            (proc / "cwd").symlink_to(foreign, target_is_directory=True)
            with (
                patch.object(gate, "_PROC_ROOT", root),
                patch.object(gate.subprocess, "run", return_value=SimpleNamespace(
                    returncode=0, stdout="123\n",
                )),
                self.assertRaisesRegex(RuntimeError, "controller checkout identity"),
            ):
                gate.live_publication_root(expected_cwd=expected)

    def test_failed_stale_dry_run_and_record(self):
        operator = _operator()
        workspace = UUID(int=1)
        operation = UUID(int=2)
        state_root = Path("project-cells")
        snapshot = SimpleNamespace(raw=(b"initial",), identity={}, source="source", database="db")
        receipt = object()
        for mode in ("failed_probe", "changed_volume", "changed_state", "dry_run", "record"):
            with (
                self.subTest(mode=mode),
                patch.object(operator, "_receipt_location", return_value=(Path("receipt"), 1000)),
                patch.object(operator, "_snapshot", return_value=snapshot) as capture,
                patch.object(
                    operator, "_detached_volumes", return_value=("source", "db"),
                ) as volumes,
                patch.object(operator, "_read_copy_catalog", return_value={}) as probe,
                patch.object(operator, "_receipt", return_value=(receipt, 1)),
                patch.object(operator, "_require_live_release"),
                patch.object(operator, "_validate_receipt") as validate,
                patch.object(operator, "_record_receipt", return_value="recorded") as publish,
            ):
                if mode == "failed_probe":
                    probe.side_effect = RuntimeError("unavailable")
                if mode == "changed_volume":
                    volumes.side_effect = [("source", "db"), ("different", "db")]
                if mode == "changed_state":
                    capture.side_effect = [snapshot, SimpleNamespace(raw=(b"changed",))]
                args = (Mock(), Mock(), state_root, workspace, operation, 1000, "sha", "image")
                if mode in {"failed_probe", "changed_volume", "changed_state"}:
                    with self.assertRaises(RuntimeError):
                        operator._recover_locked(*args, record=True)
                    validate.assert_not_called()
                    publish.assert_not_called()
                else:
                    result = operator._recover_locked(*args, record=mode == "record")
                    validate.assert_called_once_with(state_root, workspace, receipt, 1000, 1000)
                    if mode == "record":
                        publish.assert_called_once()
                    else:
                        publish.assert_not_called()
                    self.assertEqual(result, ("recorded" if mode == "record" else "verified", 1))

    def test_one_unmatched_applied_sql_prevents_receipt_construction(self):
        operator = _operator()
        snapshot = SimpleNamespace(source="source", database_identity="a" * 64)
        catalog = {
            "database_identity": snapshot.database_identity, "journal": True, "has_relations": True,
            "applied": {"drizzle/0001.sql": "b" * 64, "drizzle/0002.sql": "c" * 64},
        }
        runtime = SimpleNamespace(SourceGuardRecoveryReceipt=Mock())
        with (
            patch.dict(sys.modules, {_RUNTIME: runtime}),
            patch.object(operator, "_source_digest", side_effect=["b" * 64, "d" * 64]),
            self.assertRaisesRegex(operator.RecoveryError, "applied_source_mismatch"),
        ):
            operator._receipt(snapshot, UUID(int=1), UUID(int=2), ("e" * 64, "f" * 64), catalog)
        runtime.SourceGuardRecoveryReceipt.assert_not_called()

    @unittest.skipUnless(sys.platform == "linux", "Linux secure file contract")
    def test_record_is_private_idempotent_and_never_overwrites(self):
        operator = _operator()
        gate = operator._release_gate()

        @dataclass(frozen=True)
        class StubReceipt:
            value: str
            fencing_epoch: int = 1

            def model_dump_json(self):
                return json.dumps(asdict(self))

            def model_copy(self, *, update):
                return replace(self, **update)

            @classmethod
            def model_validate_json(cls, data):
                return cls(**json.loads(data))

        runtime = SimpleNamespace(SourceGuardRecoveryReceipt=StubReceipt)
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(sys.modules, {_RUNTIME: runtime}),
        ):
            path = Path(directory) / "receipt.json"
            uid, gid = os.getuid(), os.getgid()
            if uid == 0:
                uid, gid = 1001, 1001
                os.chown(directory, uid, gid)
            receipt = StubReceipt("first")
            self.assertEqual(operator._record_receipt(gate, path, receipt, uid, gid), "recorded")
            observed = path.stat()
            self.assertEqual((observed.st_uid, observed.st_gid, observed.st_nlink), (uid, gid, 1))
            self.assertEqual(stat.S_IMODE(observed.st_mode), 0o600)
            self.assertEqual(
                operator._record_receipt(gate, path, receipt, uid, gid), "already_recorded",
            )
            self.assertEqual(
                operator._record_receipt(gate, path, StubReceipt("first", 2), uid, gid),
                "already_recorded",
            )
            with self.assertRaisesRegex(operator.RecoveryError, "receipt_collision"):
                operator._record_receipt(gate, path, StubReceipt("different"), uid, gid)
            self.assertEqual(list(Path(directory).iterdir()), [path])
            self.assertEqual(path.read_bytes(), receipt.model_dump_json().encode())
            # A contender appearing after the existence check still cannot be replaced.
            staged = Path(directory) / "staged.json"
            staged.write_text("different", encoding="utf-8")
            fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                with self.assertRaises(FileExistsError):
                    operator._publish_noreplace(fd, staged.name, path.name)
            finally:
                os.close(fd)
            self.assertEqual(path.read_bytes(), receipt.model_dump_json().encode())
            self.assertTrue(staged.is_file())


if __name__ == "__main__":
    unittest.main()
