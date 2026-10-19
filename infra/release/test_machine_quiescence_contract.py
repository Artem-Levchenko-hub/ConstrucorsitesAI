"""Root filesystem/flock and typed controller-state contracts, no Docker daemon."""

import copy
import importlib.util
import json
import os
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

AVAILABLE = importlib.util.find_spec("yleum_orchestrator") is not None
WORKSPACE = "11111111-1111-4111-8111-111111111111"
COMMAND = "22222222-2222-4222-8222-222222222222"
PAUSE = "33333333-3333-4333-8333-333333333333"


def _checker_module():
    spec = importlib.util.spec_from_file_location(
        "quiescence_contract",
        Path(__file__).with_name("generation-publication-drain.py"),
    )
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    return checker


class PortableQuiescenceSourcesTests(unittest.TestCase):
    """Bundle authority is pure: no root, controller imports, or Docker needed."""

    def setUp(self):
        self.checker = _checker_module()
        self.old = dict(self.checker._PORTABLE_QUIESCENCE_SOURCES)
        self.new = dict(self.old)
        self.new.update(
            {
                "services.machine_adapter": "944528d2805e971120b32336d42adc6544538043fc60ff4c6219de10aabc2611",
                "routers.workspace": "f82e0bb183f1258cccf393d58e9ee047a6b15cf74b2c3ffdb98fd9111c525e29",
            }
        )

    def test_live_and_reviewed_successor_are_complete_supported_bundles(self):
        self.assertEqual(len(self.old), 9)
        self.assertEqual(
            (
                self.old["services.machine_adapter"],
                self.old["routers.workspace"],
            ),
            (
                "7a8fee13333fee8a79478bc9f64b895a67b1deed284335355158b8a95103d426",
                "4ad3af972da6e73af3e15eb407cff041f15321cf9af83466502c14c2f6dd0dc6",
            ),
        )
        for sources in (self.old, self.new):
            with self.subTest(sources=sources):
                before = copy.deepcopy(sources)
                self.checker._require_reviewed_portable_sources(sources)
                self.assertEqual(sources, before)
        # Receipts continue binding the actual observed bundle, not a common alias.
        self.assertNotEqual(self.checker._digest(self.old), self.checker._digest(self.new))

    def test_mixed_live_and_successor_modules_are_never_reviewed(self):
        for changed in ("services.machine_adapter", "routers.workspace"):
            sources = dict(self.old)
            sources[changed] = self.new[changed]
            with self.subTest(changed=changed):
                with self.assertRaisesRegex(
                    self.checker.PhysicalDrainError, "quiescence_unsupported_source"
                ):
                    self.checker._require_reviewed_portable_sources(sources)

    def test_every_unknown_source_hash_is_denied_for_both_bundles(self):
        for original in (self.old, self.new):
            for changed in original:
                sources = dict(original)
                sources[changed] = "0" * 64
                with self.subTest(changed=changed, original=original):
                    with self.assertRaisesRegex(
                        self.checker.PhysicalDrainError, "quiescence_unsupported_source"
                    ):
                        self.checker._require_reviewed_portable_sources(sources)

    def test_missing_or_extra_source_cannot_be_an_accepted_bundle(self):
        for changed in self.old:
            sources = dict(self.old)
            del sources[changed]
            with self.subTest(missing=changed):
                with self.assertRaisesRegex(
                    self.checker.PhysicalDrainError, "quiescence_unsupported_source"
                ):
                    self.checker._require_reviewed_portable_sources(sources)
        sources = {**self.new, "services.unreviewed_producer": "0" * 64}
        with self.assertRaisesRegex(
            self.checker.PhysicalDrainError, "quiescence_unsupported_source"
        ):
            self.checker._require_reviewed_portable_sources(sources)


@unittest.skipUnless(
    AVAILABLE and os.name == "posix" and os.geteuid() == 0,
    "requires root Linux fixture and cached orchestrator dependencies",
)
class MachineQuiescenceTests(unittest.TestCase):
    def setUp(self):
        from yleum_orchestrator.core.cell_resources import CellResourceNames
        from yleum_orchestrator.services.cell_state import CellOperationRecord

        self.checker = _checker_module()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.state, self.proc, self.groups = (
            root / "state",
            root / "proc",
            root / "groups",
        )
        self.receipts = root / "receipts"
        for name, value in (
            ("_PROC_ROOT", self.proc),
            ("_CGROUP_ROOT", self.groups),
            ("_QUIESCENCE_ROOT", self.receipts),
        ):
            patcher = patch.object(self.checker, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.group("init.scope")
        self.process(1, "init.scope")
        (self.proc / "sys/kernel/random").mkdir(parents=True)
        (self.proc / "sys/kernel/random/boot_id").write_text(WORKSPACE)
        self.names = CellResourceNames.for_workspace(UUID(WORKSPACE))
        pause = CellOperationRecord(
            operation_id=UUID(PAUSE),
            kind="pause",
            status="completed",
            phase="completed",
            request_digest="a" * 64,
            fencing_epoch=8,
            bundle_state="resources_paused",
        )
        self.cell = {
            "workspace_id": WORKSPACE,
            "project_id": WORKSPACE,
            "owner_id": WORKSPACE,
            "profile_version": "docker-owner-cell-resources-v2",
            "phase": "completed",
            "bundle_state": "resources_paused",
            "fencing_epoch": 8,
            "active_generation_run_id": None,
            "active_generation_fencing_epoch": None,
            "last_operation_id": PAUSE,
            "provider_ref": "docker:" + WORKSPACE,
            "resource_names": asdict(self.names),
            "operations": [pause.to_dict()],
        }
        self.cell_path = self.state / "project-cells" / (WORKSPACE + ".json")
        self.write(self.cell_path, {"version": 1, "workspace": self.cell})
        self.machine_path = self.state / "project-machines" / WORKSPACE / "machine.json"
        self.machine = {
            "workspace_id": WORKSPACE,
            "epoch": 7,
            "ready_epoch": 7,
            "cancelled_epoch": 0,
            "operations": {
                COMMAND: {
                    "digest": "b" * 64,
                    "state": "starting",
                    "epoch": 7,
                    "kind": "command",
                    "started_at": "2026-10-18T06:07:56+00:00",
                    "heartbeat_at": "2026-10-18T06:07:56+00:00",
                    "log_bytes": 0,
                }
            },
        }
        self.write(self.machine_path, self.machine)
        self.write(self.machine_path.with_name("docker.json"), {"epoch": 7})
        self.lock_path = self.state / "locks" / ("workspace-" + WORKSPACE + ".lock")
        self.lock_path.parent.mkdir(parents=True)
        self.lock_path.write_text("0")
        self.lock_path.chmod(0o600)
        self.containers = []
        self.info = {
            "ID": "fixture-daemon",
            "CgroupDriver": "systemd",
            "CgroupVersion": "2",
        }
        self.client = SimpleNamespace(
            info=lambda: copy.deepcopy(self.info),
            containers=SimpleNamespace(list=lambda **kwargs: list(self.containers)),
            api=SimpleNamespace(
                exec_inspect=lambda _: (_ for _ in ()).throw(
                    AssertionError("unexpected exec")
                )
            ),
        )

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, default=str))
        path.chmod(0o600)

    def save_cell(self):
        self.write(self.cell_path, {"version": 1, "workspace": self.cell})

    def group(self, group):
        directory = self.groups / group
        directory.mkdir(parents=True, exist_ok=True)
        for item in [
            self.groups,
            *directory.relative_to(self.groups).parents,
            directory,
        ]:
            item = item if item.is_absolute() else self.groups / item
            item.mkdir(parents=True, exist_ok=True)
            for name in ("cgroup.procs", "cgroup.threads"):
                (item / name).write_text("")

    def process(self, pid, group, *, tid=None):
        directory = self.proc / str(pid)
        thread = directory / "task" / str(tid or pid)
        thread.mkdir(parents=True, exist_ok=True)
        (directory / "cgroup").write_text("0::/" + group + "\n")
        (thread / "cgroup").write_text("0::/" + group + "\n")

    def record(self):
        return self.checker.machine_quiescence(
            self.state, self.client, WORKSPACE, record=True
        )

    def validate(self):
        return self.checker.machine_quiescence(self.state, self.client, WORKSPACE)

    def test_gate_requires_separate_attestation_and_never_rewrites_unknown(self):
        before = self.machine_path.read_bytes()
        with self.assertRaises(self.checker.PhysicalDrainError):
            self.checker.require_physical_writers_drained(self.state, self.client)
        self.assertEqual(set(self.record()), {COMMAND})
        result = self.checker.require_physical_writers_drained(self.state, self.client)
        self.assertEqual(result["physical_journals"], 1)
        self.assertEqual(self.machine_path.read_bytes(), before)
        receipts = list(self.receipts.glob("*/*.json"))
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0].stat().st_mode & 0o777, 0o600)
        self.record()
        self.assertEqual(len(list(self.receipts.glob("*/*.json"))), 1)

    def test_missing_receipt_namespace_is_created_private_without_reowning_parents(
        self,
    ):
        self.receipts = self.receipts / "not-yet-installed" / "quiescence"
        with patch.object(self.checker, "_QUIESCENCE_ROOT", self.receipts):
            self.record()
            self.validate()
        self.assertEqual(self.receipts.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.receipts.stat().st_mode & 0o777, 0o700)

    def set_destroyed(self):
        self.cell["bundle_state"] = "retained"
        self.cell["operations"][-1].update(kind="destroy", bundle_state="retained")
        self.save_cell()

    def test_completed_destroy_attests_current_retirement_without_rewriting_command(self):
        self.set_destroyed()
        before = self.machine_path.read_bytes()
        self.assertEqual(set(self.record()), {COMMAND})
        self.validate()
        result = self.checker.require_physical_writers_drained(self.state, self.client)
        self.assertEqual(result["physical_journals"], 1)
        self.assertEqual(self.machine_path.read_bytes(), before)
        receipt = json.loads(next(self.receipts.glob("*/*.json")).read_bytes())
        self.assertEqual(receipt["binding"]["destroy_operation_id"], PAUSE)
        self.assertNotIn("pause_operation_id", receipt["binding"])

    def test_retirement_kind_and_bundle_must_be_an_exact_pair(self):
        for kind, bundle in (
            ("destroy", "resources_paused"),
            ("pause", "retained"),
            ("ensure", "retained"),
            ("release", "retained"),
        ):
            with self.subTest(kind=kind, bundle=bundle):
                self.cell["bundle_state"] = bundle
                self.cell["operations"][-1].update(kind=kind, bundle_state=bundle)
                self.save_cell()
                with self.assertRaises(self.checker.PhysicalDrainError):
                    self.record()

    def test_destroy_does_not_allow_auxiliary_containers_or_fresh_wake(self):
        self.set_destroyed()
        self.record()
        self.cell["bundle_state"] = "resources_ready"
        self.save_cell()
        with self.assertRaises(self.checker.PhysicalDrainError):
            self.validate()
        self.set_destroyed()
        labels = {
            "omnia.project_cell": "true",
            "omnia.resource_kind": "postgres",
            **{
                "omnia." + key: WORKSPACE
                for key in ("workspace_id", "project_id", "owner_id")
            },
        }
        self.containers.append(
            SimpleNamespace(
                id="d" * 64,
                name=self.names.postgres_container,
                reload=lambda: None,
                attrs={"Id": "d" * 64, "Config": {"Labels": labels}, "ExecIDs": []},
            )
        )
        with self.assertRaisesRegex(
            self.checker.PhysicalDrainError, "workspace_writer_present"
        ):
            self.validate()

    def test_destroy_still_requires_generation_absence_and_higher_terminal_fence(self):
        self.set_destroyed()
        original = copy.deepcopy(self.cell)
        for field, value in (
            ("active_generation_run_id", COMMAND),
            ("active_generation_fencing_epoch", 7),
            ("fencing_epoch", 7),
            ("phase", "planned"),
        ):
            with self.subTest(field=field):
                self.cell = {**original, field: value}
                self.save_cell()
                with self.assertRaises(self.checker.PhysicalDrainError):
                    self.record()
        self.cell = copy.deepcopy(original)
        self.cell["operations"][0]["status"] = "indeterminate"
        self.save_cell()
        with self.assertRaises(self.checker.PhysicalDrainError):
            self.record()
        self.cell = copy.deepcopy(original)
        self.cell["fencing_epoch"] = 7
        self.cell["operations"][0]["fencing_epoch"] = 7
        self.save_cell()
        with self.assertRaisesRegex(
            self.checker.PhysicalDrainError, "quiescence_retirement_fence"
        ):
            self.record()
        self.assertFalse(self.receipts.exists())

    def test_destroy_rejects_canonical_cell_names_with_missing_or_wrong_labels(self):
        self.set_destroyed()
        for name in (
            self.names.postgres_container,
            self.names.redis_container,
            self.names.draft_container_name(),
        ):
            for labels in ({}, {"omnia.workspace_id": COMMAND}):
                with self.subTest(name=name, labels=labels):
                    self.containers[:] = [SimpleNamespace(
                        id="d" * 64,
                        name=name,
                        reload=lambda: None,
                        attrs={"Id": "d" * 64, "Config": {"Labels": labels}},
                    )]
                    with self.assertRaisesRegex(
                        self.checker.PhysicalDrainError, "workspace_writer_present"
                    ):
                        self.record()
        self.assertFalse(self.receipts.exists())

    def test_pause_without_empty_generation_authority_or_higher_fence_is_denied(self):
        original = copy.deepcopy(self.cell)
        for field, value in (
            ("active_generation_run_id", COMMAND),
            ("active_generation_fencing_epoch", 7),
            ("fencing_epoch", 7),
            ("bundle_state", "resources_ready"),
            ("phase", "planned"),
        ):
            with self.subTest(field=field):
                self.cell = {**original, field: value}
                self.save_cell()
                with self.assertRaises(self.checker.PhysicalDrainError):
                    self.record()
        self.assertFalse(self.receipts.exists())

    def test_nonterminal_or_wrong_lifecycle_receipt_is_denied(self):
        original = copy.deepcopy(self.cell)
        for field, value in (
            ("kind", "reconcile"),
            ("status", "indeterminate"),
            ("request_digest", "bad"),
            ("generation_run_id", COMMAND),
        ):
            with self.subTest(field=field):
                self.cell = copy.deepcopy(original)
                self.cell["operations"][0][field] = value
                self.save_cell()
                with self.assertRaises(self.checker.PhysicalDrainError):
                    self.record()

    def test_receipt_invalidated_by_state_journal_backend_owner_or_boot_change(self):
        self.record()
        cases = [
            self.cell_path,
            self.machine_path,
            self.machine_path.with_name("docker.json"),
            self.proc / "sys/kernel/random/boot_id",
        ]
        for path in cases:
            old = path.read_bytes()
            with self.subTest(path=path.name):
                if path.name == "boot_id":
                    path.write_text(COMMAND)
                else:
                    path.write_bytes(old + b" ")
                with self.assertRaises(FileNotFoundError):
                    self.validate()
                path.write_bytes(old)
        self.cell["owner_id"] = COMMAND
        self.save_cell()
        with self.assertRaises(FileNotFoundError):
            self.validate()

    def test_no_attestation_for_request_adaptation_running_or_incomplete_scope(self):
        original = copy.deepcopy(self.machine)
        for field, value in (
            ("kind", "request"),
            ("kind", "adaptation_recovery"),
            ("state", "running"),
            ("exec_id", "d" * 64),
            ("epoch", True),
            ("result", {"exit_code": 0}),
        ):
            with self.subTest(field=field, value=value):
                machine = copy.deepcopy(original)
                machine["operations"][COMMAND][field] = value
                self.write(self.machine_path, machine)
                with self.assertRaises(self.checker.PhysicalDrainError):
                    self.record()

    def test_noncanonical_daemon_and_daemon_identity_change_are_denied(self):
        self.record()
        self.info["ID"] = "another-daemon"
        with self.assertRaises(FileNotFoundError):
            self.validate()
        self.info["CgroupDriver"] = "cgroupfs"
        with self.assertRaises(self.checker.PhysicalDrainError):
            self.record()

    def test_unsupported_producer_source_cannot_create_attestation(self):
        original = importlib.util.find_spec
        changed = self.state / "changed-producer.py"
        changed.write_text("# not the reviewed producer contract\n")

        def lookup(name):
            if name == "yleum_orchestrator.services.project_machine":
                return SimpleNamespace(origin=str(changed))
            return original(name)

        with patch.object(importlib.util, "find_spec", lookup):
            with self.assertRaisesRegex(
                self.checker.PhysicalDrainError, "unsupported_source"
            ):
                self.record()
        self.assertFalse(self.receipts.exists())

    def test_saved_receipt_does_not_hide_fresh_orphan_or_helper(self):
        self.record()
        scope = "system.slice/docker-" + "f" * 64 + ".scope"
        self.group(scope)
        with self.assertRaisesRegex(self.checker.PhysicalDrainError, "orphan_cgroup"):
            self.validate()
        import shutil

        shutil.rmtree(self.groups / "system.slice")
        self.containers.append(
            SimpleNamespace(
                id="d" * 64,
                name="unexpected-helper",
                reload=lambda: None,
                attrs={
                    "Id": "d" * 64,
                    "Config": {
                        "Labels": {
                            "omnia.workspace_id": WORKSPACE,
                            "omnia.helper": "true",
                        }
                    },
                },
            )
        )
        with self.assertRaisesRegex(
            self.checker.PhysicalDrainError, "workspace_writer_present"
        ):
            self.validate()

    def test_binding_change_during_physical_observation_is_denied(self):
        original = self.checker._require_no_orphan_docker_processes

        def mutate(known):
            result = original(known)
            self.machine_path.with_name("docker.json").write_text(
                '{"epoch":7,"changed":true}'
            )
            return result

        with patch.object(self.checker, "_require_no_orphan_docker_processes", mutate):
            with self.assertRaisesRegex(
                self.checker.PhysicalDrainError, "binding_changed"
            ):
                self.record()
        self.assertFalse(self.receipts.exists())

    def test_saved_proof_does_not_cover_other_pending_journals(self):
        self.record()
        for family in ("source-write-operations", "command-operations"):
            path = self.state / family / WORKSPACE / (PAUSE + ".json")
            self.write(
                path,
                {"workspace_id": WORKSPACE, "binding": {"workspace_id": WORKSPACE}},
            )
            with (
                self.subTest(family=family),
                self.assertRaises(self.checker.PhysicalDrainError),
            ):
                self.checker.require_physical_writers_drained(self.state, self.client)
            path.unlink()
        machine = copy.deepcopy(self.machine)
        machine["operations"][PAUSE] = {"kind": "request", "state": "starting"}
        self.write(self.machine_path, machine)
        with self.assertRaises(self.checker.PhysicalDrainError):
            self.checker.require_physical_writers_drained(self.state, self.client)

    def test_receipt_link_or_fsync_failure_never_publishes_partial_final_json(self):
        for operation in ("link", "fsync"):
            with self.subTest(operation=operation):
                with patch.object(
                    os, operation, side_effect=OSError("fixture failure")
                ):
                    with self.assertRaises(OSError):
                        self.record()
                self.assertFalse(list(self.receipts.glob("*/*.json")))

    def test_orphan_scope_and_nested_task_membership_are_denied(self):
        scope = "system.slice/docker-" + "f" * 64 + ".scope"
        self.group(scope + "/nested")
        with self.assertRaisesRegex(self.checker.PhysicalDrainError, "orphan_cgroup"):
            self.record()
        import shutil

        shutil.rmtree(self.groups / "system.slice")
        self.process(99, "init.scope")
        self.process(99, "init.scope", tid=100)
        (self.proc / "99/task/100/cgroup").write_text("0::/" + scope + "/nested\n")
        with self.assertRaisesRegex(self.checker.PhysicalDrainError, "orphan_process"):
            self.record()

    def test_kernel_read_failure_is_not_an_empty_host(self):
        (self.proc / "1/task/1/cgroup").unlink()
        with self.assertRaises(FileNotFoundError):
            self.record()

    def test_locked_workspace_and_unsafe_state_or_receipt_are_denied(self):
        import fcntl

        fd = os.open(self.lock_path, os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                self.record()
        finally:
            os.close(fd)
        self.cell_path.chmod(0o644)
        with self.assertRaises(self.checker.PhysicalDrainError):
            self.record()
        self.cell_path.chmod(0o600)
        self.record()
        receipt = next(self.receipts.glob("*/*.json"))
        receipt.chmod(0o666)
        with self.assertRaises(self.checker.PhysicalDrainError):
            self.validate()

    def test_target_portable_container_even_with_missing_labels_is_denied(self):
        container = SimpleNamespace(
            id="d" * 64,
            name="omnia-machine-" + WORKSPACE.replace("-", "") + "-dev",
            reload=lambda: None,
            attrs={"Id": "d" * 64, "Config": {"Labels": {}}},
        )
        self.containers.append(container)
        with self.assertRaisesRegex(self.checker.PhysicalDrainError, "machine_present"):
            self.record()

    def test_known_auxiliary_cell_database_does_not_hide_an_orphan(self):
        identity = "d" * 64
        labels = {
            "omnia.project_cell": "true",
            "omnia.resource_kind": "postgres",
            **{
                "omnia." + key: WORKSPACE
                for key in ("workspace_id", "project_id", "owner_id")
            },
        }
        self.containers.append(
            SimpleNamespace(
                id=identity,
                name=self.names.postgres_container,
                reload=lambda: None,
                attrs={"Id": identity, "Config": {"Labels": labels}, "ExecIDs": []},
            )
        )
        self.group("system.slice/docker-" + identity + ".scope")
        self.process(99, "system.slice/docker-" + identity + ".scope")
        self.record()
        self.validate()

    def test_changing_roster_and_tampered_receipt_do_not_pass(self):
        self.record()
        receipt = next(self.receipts.glob("*/*.json"))
        payload = json.loads(receipt.read_bytes())
        payload["binding"]["commands"][COMMAND] = "f" * 64
        self.write(receipt, payload)
        with self.assertRaises(self.checker.PhysicalDrainError):
            self.validate()
        self.client.containers.list = lambda **kwargs: (
            [] if not self.containers else self.containers.pop()
        )
        self.containers.extend(
            [[], [SimpleNamespace(id="bad", reload=lambda: None, attrs={})]]
        )
        with self.assertRaises(self.checker.PhysicalDrainError):
            self.record()


if __name__ == "__main__":
    unittest.main()
