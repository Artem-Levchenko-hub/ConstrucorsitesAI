"""Pure privileged-observer falsifiers; no Docker, Redis, SSH or production."""

from __future__ import annotations

import ast
import copy
import importlib.util
import json
import os
import stat
import subprocess
from pathlib import Path
import unittest
import tempfile
from unittest.mock import patch
from types import SimpleNamespace


class ControllerUnitInventoryTests(unittest.TestCase):
    def setUp(self):
        source = Path(__file__).with_name("generation-reader-inventory.py")
        spec = importlib.util.spec_from_file_location("unit_observer", source)
        self.observer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.observer)
        self.template = "apport-coredump-hook@.service"
        self.instance = "apport-coredump-hook@yleum-reader-inspection.service"
        self.unit = "yleum-orchestrator.service"
        self.installed = [self.template, self.unit]
        self.loaded = [self.unit]
        self.rows = {
            self.unit: self.row(
                self.unit,
                ExecStart="{ path=/venv/bin/python ; argv[]=python -m uvicorn yleum_orchestrator.main:app ; }",
                MainPID="42",
                ActiveState="active",
                ControlGroup="/system.slice/yleum-orchestrator.service",
            ),
            self.instance: self.row(
                self.instance, FragmentPath="/usr/lib/systemd/system/" + self.template
            ),
        }
        self.calls = []
        self.bus_properties = {}
        self.extra_properties = {}

    def row(self, name, **values):
        return {
            "Id": name,
            "Names": name,
            "LoadState": "loaded",
            "MainPID": "0",
            "ControlGroup": "",
            "ExecStart": "{ path=/usr/bin/true ; }",
            "ActiveState": "inactive",
            "FragmentPath": "/usr/lib/systemd/system/" + name,
            "DropInPaths": "",
            **values,
        }

    def command(self, argv):
        self.calls.append(argv)
        if argv[0] == "busctl":
            self.assertEqual(
                argv[1:6],
                [
                    "--system",
                    "--json=short",
                    "--auto-start=no",
                    "get-property",
                    "org.freedesktop.systemd1",
                ],
            )
            self.assertEqual(
                argv[6], "/org/freedesktop/systemd1/unit/systemd_2dexit_2eservice"
            )
            return json.dumps(self.bus_properties[argv[-1]])
        if argv[1] == "list-unit-files":
            return "\n".join(name + " enabled enabled" for name in self.installed)
        if argv[1] == "list-units":
            return "\n".join(
                name + " loaded active running fixture" for name in self.loaded
            )
        self.assertEqual(argv[1], "show")
        names = [item for item in argv[2:] if not item.startswith("--")]
        if self.template in names:
            raise ValueError(
                "Failed to get properties: Unit name apport-coredump-hook@.service is neither a valid invocation ID nor unit name."
            )
        return "\n\n".join(
            "\n".join(
                f"{key}={value}"
                for key, value in [
                    *self.rows[name].items(),
                    *self.extra_properties.get(name, []),
                ]
            )
            for name in names
            if name in self.rows
        )

    def inspect(self):
        with patch.object(self.observer, "command", self.command):
            return self.observer.controller_units()

    def test_installed_template_effective_properties_and_repeat_are_complete(self):
        self.rows[self.instance]["DropInPaths"] = (
            "/etc/systemd/system/apport-coredump-hook@.service.d/override.conf"
        )
        self.assertEqual(self.inspect()["Id"], self.unit)
        self.loaded.append(self.instance)
        self.assertEqual(self.inspect()["Id"], self.unit)
        self.assertTrue(any(self.instance in call for call in self.calls))
        self.assertTrue(
            all(
                call[1] in {"show", "list-units", "list-unit-files"}
                for call in self.calls
            )
        )

    def test_template_and_loaded_instance_readers_are_rejected(self):
        for name in (self.instance, "old@worker.service"):
            with self.subTest(name=name):
                original = copy.deepcopy(self.rows)
                if name != self.instance:
                    self.loaded.append(name)
                    self.rows[name] = self.row(name)
                self.rows[name]["ExecStart"] = "python -m yleum_api.workers.generation"
                with self.assertRaises(ValueError):
                    self.inspect()
                self.rows = original
                self.loaded = [self.unit]

    def test_template_collision_instance_dropin_and_partial_output_deny(self):
        for field, value in (
            ("FragmentPath", "/etc/systemd/system/" + self.instance),
            ("DropInPaths", "/etc/systemd/system/" + self.instance + ".d/hide.conf"),
            ("LoadState", "not-found"),
            ("ActiveState", "active"),
            ("Names", "foreign.service"),
        ):
            with self.subTest(field=field):
                original = self.rows[self.instance][field]
                self.rows[self.instance][field] = value
                with self.assertRaises(ValueError):
                    self.inspect()
                self.rows[self.instance][field] = original
        del self.rows[self.instance]
        with self.assertRaises(ValueError):
            self.inspect()

    def test_masked_template_is_only_inert_without_execution(self):
        self.rows[self.instance].update(
            LoadState="masked", FragmentPath="/dev/null", ExecStart=""
        )
        self.assertEqual(self.inspect()["Id"], self.unit)
        self.rows[self.instance]["MainPID"] = "7"
        with self.assertRaises(ValueError):
            self.inspect()

    def test_passive_loaded_tombstone_is_complete_without_service_interface(self):
        name = "old-absent.service"
        self.loaded.append(name)
        self.rows[name] = self.row(name, LoadState="not-found", FragmentPath="")
        del self.rows[name]["ExecStart"]
        self.assertEqual(self.inspect()["Id"], self.unit)
        for field, value in (
            ("MainPID", "1"),
            ("ControlGroup", "/old"),
            ("FragmentPath", "/old"),
            ("DropInPaths", "/old"),
            ("ActiveState", "failed"),
        ):
            with self.subTest(field=field):
                original = self.rows[name][field]
                self.rows[name][field] = value
                with self.assertRaises(ValueError):
                    self.inspect()
                self.rows[name][field] = original
        self.installed.append(name)
        with self.assertRaises(ValueError):
            self.inspect()

    def test_installed_mask_needs_actual_null_symlink_not_missing_defaults(self):
        name = "masked.service"
        self.installed.append(name)
        self.rows[name] = self.row(
            name, LoadState="masked", FragmentPath="/etc/systemd/system/" + name
        )
        del self.rows[name]["ExecStart"]
        with (
            patch.object(
                Path,
                "lstat",
                return_value=SimpleNamespace(st_uid=0, st_mode=stat.S_IFLNK | 0o777),
            ),
            patch.object(Path, "resolve", return_value=Path("/dev/null")),
        ):
            self.assertEqual(self.inspect()["Id"], self.unit)
            self.rows[name]["DropInPaths"] = "/etc/override.conf"
            with self.assertRaises(ValueError):
                self.inspect()
            self.rows[name]["DropInPaths"] = ""
        with (
            patch.object(
                Path,
                "lstat",
                return_value=SimpleNamespace(st_uid=0, st_mode=stat.S_IFLNK | 0o777),
            ),
            patch.object(Path, "resolve", return_value=Path("/foreign")),
        ):
            with self.assertRaises(ValueError):
                self.inspect()

    def test_alias_duplicate_is_complete_but_unknown_controller_denies(self):
        alias = "orchestrator-alias.service"
        self.installed.append(alias)
        self.rows[self.unit]["Names"] += " " + alias
        self.rows[alias] = self.rows[self.unit]
        self.assertEqual(self.inspect()["Id"], self.unit)
        unknown = "old-controller.service"
        self.loaded.append(unknown)
        self.rows[unknown] = self.row(
            unknown,
            ExecStart="uvicorn yleum_orchestrator.main:app",
            MainPID="99",
            ActiveState="active",
        )
        with self.assertRaises(ValueError):
            self.inspect()

    def test_quoted_escaped_loaded_names_preserve_exact_identity(self):
        for label in ("BOOT", "UEFI"):
            name = r"systemd-fsck@dev-disk-by\x2dlabel-" + label + ".service"
            self.loaded.append(name)
            self.rows[name] = self.row(
                name, Names=json.dumps(name), ActiveState="active"
            )
        self.assertEqual(self.inspect()["Id"], self.unit)
        self.rows[name]["Names"] = json.dumps(name + "-foreign")
        with self.assertRaises(ValueError):
            self.inspect()

    def test_loaded_missing_command_requires_typed_empty_list(self):
        name = "systemd-exit.service"
        self.installed.append(name)
        self.rows[name] = self.row(name, ActiveState="active")
        del self.rows[name]["ExecStart"]
        self.bus_properties = {
            "Id": {"type": "s", "data": name},
            "LoadState": {"type": "s", "data": "loaded"},
            "ExecStart": {"type": "a(sasbttttuii)", "data": []},
        }
        self.assertEqual(self.inspect()["Id"], self.unit)
        for key, bad in (
            ("Id", {"type": "s", "data": "foreign.service"}),
            ("LoadState", {"type": "s", "data": "not-found"}),
            ("ExecStart", {"type": "as", "data": []}),
            ("ExecStart", {"type": "a(sasbttttuii)", "data": [["hidden reader"]]}),
            ("ExecStart", {"type": "a(sasbttttuii)", "data": None}),
        ):
            with self.subTest(key=key, bad=bad):
                old = self.bus_properties[key]
                self.bus_properties[key] = bad
                with self.assertRaises(ValueError):
                    self.inspect()
                self.bus_properties[key] = old
        self.rows[name]["Names"] = '"unterminated'
        with self.assertRaises(ValueError):
            self.inspect()

    def test_multiple_exec_start_commands_all_remain_visible(self):
        name = "oneshot.service"
        self.installed.append(name)
        self.rows[name] = self.row(name)
        self.extra_properties[name] = [
            ("ExecStart", "{ path=/usr/bin/true ; argv[]=true second ; }")
        ]
        self.assertEqual(self.inspect()["Id"], self.unit)
        self.extra_properties[name] = [
            ("ExecStart", "python -m yleum_api.workers.run"),
            ("ExecStart", "true"),
        ]
        with self.assertRaises(ValueError):
            self.inspect()
        self.extra_properties[name] = [("Id", name)]
        with self.assertRaises(ValueError):
            self.inspect()


class CompleteInventoryTests(unittest.TestCase):
    def test_privileged_observer_matches_actual_api_reader_contract(self):
        root = Path(__file__).resolve().parents[2]
        sources = (
            "apps/api/src/yleum_api/services/generation/recovery_readers.py",
            "apps/orchestrator/src/yleum_orchestrator/services/generation_reader_inventory.py",
        )
        for relative in sources:
            with self.subTest(source=relative):
                tree = ast.parse((root / relative).read_text(encoding="utf-8"))
                declared = next(
                    node.value
                    for node in tree.body
                    if isinstance(node, ast.Assign)
                    and any(
                        isinstance(target, ast.Name) and target.id == "API_CAPABILITIES"
                        for target in node.targets
                    )
                )
                self.assertEqual(self.observer.API_CAPABILITIES, ast.literal_eval(declared))

    def setUp(self) -> None:
        source = Path(__file__).with_name("generation-reader-inventory.py")
        self.assertTrue(source.is_file(), "authoritative reader observer is absent")
        spec = importlib.util.spec_from_file_location("reader_observer", source)
        assert spec is not None and spec.loader is not None
        self.observer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.observer)

    def fixture(self):
        observations = []
        registrations = {}
        for i, slot in enumerate(sorted(self.observer.SLOTS), 1):
            host, role = slot.split("/")
            identity = {
                "host": host,
                "role": role,
                "host_boot_id": "11111111-1111-4111-8111-111111111111",
                "pid_namespace_inode": i + 10,
                "pid": i,
                "start_ticks": i + 100,
                "startup_nonce": f"00000000-0000-4000-8000-{i:012d}",
            }
            caps = list(
                self.observer.CONTROLLER_CAPABILITIES
                if role == "orchestrator"
                else self.observer.API_CAPABILITIES
            )
            record = {
                "identity": identity,
                "release_sha": "a" * 40,
                "capabilities": caps,
            }
            key = self.observer.digest(identity)
            registrations[key] = {"payload": record, "ttl": 20}
            observations.append(
                {
                    **identity,
                    "slot": slot,
                    "host_pid": i + 1000,
                    "container_id": None if role == "orchestrator" else str(i) * 64,
                    "supervisor_id": "unit" if role == "orchestrator" else "compose",
                    "release_sha": "a" * 40,
                    "capabilities": caps,
                }
            )
        return observations, registrations

    def build(self, observations, registrations):
        return self.observer.build_snapshot(
            observations=observations,
            registrations=registrations,
            release_sha="a" * 40,
            epoch="22222222-2222-4222-8222-222222222222",
            redis_run_id="b" * 40,
            observed_at_ms=1000,
        )

    def test_new_aggregate_cannot_hide_old_reader(self):
        observations, registrations = self.fixture()
        old = copy.deepcopy(observations[0])
        old["host_pid"] += 10
        old["start_ticks"] += 10
        old["release_sha"] = "c" * 40
        observations.append(old)
        with self.assertRaises(ValueError):
            self.build(observations, registrations)

    def test_complete_exact_inventory_is_ready(self):
        observations, registrations = self.fixture()
        result = self.build(observations, registrations)
        self.assertEqual(result["state"], "ready")
        self.assertEqual(len(result["observed"]), 5)
        self.assertEqual(result["registered"], sorted(registrations))

    def test_missing_extra_stale_and_claimed_only_capabilities_deny(self):
        for kind in (
            "missing",
            "unregistered",
            "expired",
            "capability",
            "replacement",
            "foreign_registration",
        ):
            with self.subTest(kind=kind):
                observations, registrations = self.fixture()
                key = next(iter(registrations))
                if kind == "missing":
                    observations.pop()
                elif kind == "unregistered":
                    registrations.pop(key)
                elif kind == "expired":
                    registrations[key]["ttl"] = 0
                elif kind == "capability":
                    observations[0]["capabilities"].remove("reader-standby:1")
                elif kind == "replacement":
                    observations[0]["start_ticks"] += 1
                else:
                    registrations["e" * 64] = registrations[key]
                with self.assertRaises(ValueError):
                    self.build(observations, registrations)

    def test_alias_or_stopped_old_api_container_is_not_omitted(self):
        self.assertTrue(
            hasattr(self.observer, "classify_containers"),
            "Docker supervisor scan absent",
        )
        rows = [
            {
                "id": str(i) * 64,
                "name": name,
                "image": "sha256:" + "a" * 64,
                "configured_image": "omnia-api:" + "b" * 40,
                "service": role,
                "running": True,
                "pid": i + 100,
                "command": ["python", "-m", module],
            }
            for i, (name, role, module) in enumerate(
                [
                    ("yleum-prod-api", "api", "yleum_api.main:app"),
                    ("yleum-prod-worker", "worker", "yleum_api.workers.run"),
                    (
                        "yleum-prod-generation-worker",
                        "generation-worker",
                        "yleum_api.workers.generation",
                    ),
                ],
                1,
            )
        ]
        self.assertEqual(
            set(self.observer.classify_containers(rows, "core")),
            {"api", "worker", "generation-worker"},
        )
        for running in (True, False):
            old = {
                **rows[1],
                "id": "f" * 64,
                "name": "omnia-prod-worker-old",
                "running": running,
            }
            with self.assertRaises(ValueError):
                self.observer.classify_containers([*rows, old], "core")

    def test_dormant_retirement_requires_every_nonrestart_fact(self):
        valid = {
            "status": "exited",
            "running": False,
            "paused": False,
            "dead": False,
            "pid": 0,
            "restart": "no",
        }
        self.assertTrue(self.observer.retired_dormant(valid))
        for field, wrong in (
            ("status", "created"),
            ("running", True),
            ("paused", True),
            ("dead", True),
            ("pid", 7),
            ("restart", "always"),
        ):
            with self.subTest(field=field):
                self.assertFalse(self.observer.retired_dormant({**valid, field: wrong}))
                missing = {k: v for k, v in valid.items() if k != field}
                self.assertFalse(self.observer.retired_dormant(missing))

    @unittest.skipUnless(
        hasattr(os, "geteuid") and os.geteuid() == 0,
        "requires isolated root-owned filesystem",
    )
    def test_inhibit_cannot_be_reowned_or_cleared_by_foreign_release(self):
        self.assertTrue(
            hasattr(self.observer, "write_inhibit"), "persistent rollout barrier absent"
        )
        owner = {
            "version": 1,
            "release_sha": "a" * 40,
            "owner_nonce": "11111111-1111-4111-8111-111111111111",
        }
        foreign = {**owner, "owner_nonce": "22222222-2222-4222-8222-222222222222"}
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(self.observer, "secure_directory"),
            patch.object(self.observer, "fsync_directory"),
        ):
            directory = Path(tmp)
            self.observer.write_inhibit(directory, owner)
            self.observer.write_inhibit(directory, owner)
            before = (directory / "rollout-inhibit.json").read_bytes()
            for action in (self.observer.write_inhibit, self.observer.remove_inhibit):
                with self.assertRaises(ValueError):
                    action(directory, foreign)
                self.assertEqual(
                    (directory / "rollout-inhibit.json").read_bytes(), before
                )
            self.observer.remove_inhibit(directory, owner)
            self.assertFalse((directory / "rollout-inhibit.json").exists())

    def test_registration_expiry_during_later_reads_cannot_authorize_distribution(self):
        observations, records = self.fixture()
        snapshot = self.build(observations, records)
        observer = self.observer
        clock = [0.0]
        keys = list(records)

        class Redis:
            def scan_iter(self, **kwargs):
                return [observer.PREFIX + key for key in keys]

            def get(self, key):
                if key == observer.EPOCH_KEY:
                    return snapshot["epoch"]
                return observer.canonical(
                    records[key[len(observer.PREFIX) :]]["payload"]
                )

            def pttl(self, key):
                if key.endswith(keys[0]):
                    return 1
                clock[0] += 0.01
                return 20000

            def info(self, part):
                return {"run_id": snapshot["redis_run_id"]}

        with (
            patch.object(observer.time, "time_ns", return_value=1001_000_000),
            patch.object(observer.time, "monotonic", side_effect=lambda: clock[0]),
        ):
            with self.assertRaises(ValueError):
                observer.check_live(snapshot, Redis())

    def test_partial_distribution_invalidates_epoch_and_keeps_inhibit(self):
        observations, records = self.fixture()
        snapshot = self.build(observations, records)
        written = []
        remote_calls = []
        invalidations = []

        class Redis:
            def set(self, key, value):
                invalidations.append((key, value))

        def remote_write(value):
            remote_calls.append(value["state"])
            raise OSError("fixture second host unavailable")

        with patch.object(self.observer, "check_live"):
            with self.assertRaises(OSError):
                self.observer.distribute(
                    snapshot,
                    Redis(),
                    lambda value: written.append(value),
                    remote_write,
                    lambda: {},
                )
        self.assertEqual([value["state"] for value in written], ["ready", "deny"])
        self.assertEqual(remote_calls, ["ready", "deny"])
        self.assertEqual(len(invalidations), 1)
        self.assertNotEqual(invalidations[0][1], snapshot["epoch"])
        self.assertEqual(written[-1]["observed_at_ms"], snapshot["observed_at_ms"])
        self.assertEqual(written[-1]["expires_at_ms"], snapshot["expires_at_ms"])

    @unittest.skipUnless(
        hasattr(os, "geteuid") and os.geteuid() == 0,
        "requires isolated root-owned filesystem",
    )
    def test_reboot_restores_persistent_inhibit_and_foreign_runtime_denies(self):
        owner = {
            "version": 1,
            "release_sha": "a" * 40,
            "owner_nonce": "11111111-1111-4111-8111-111111111111",
        }
        foreign = {**owner, "owner_nonce": "22222222-2222-4222-8222-222222222222"}
        with tempfile.TemporaryDirectory() as tmp:
            state, runtime = Path(tmp) / "state", Path(tmp) / "run"
            state.mkdir()
            runtime.mkdir()
            with (
                patch.object(self.observer, "STATE", state),
                patch.object(self.observer, "DIRECTORY", runtime),
            ):
                self.observer.write_inhibit(state, owner)
                self.observer.restore_inhibit()
                self.assertEqual(self.observer.read_inhibit(runtime), owner)
                (runtime / self.observer.INHIBIT).unlink()
                self.observer.write_inhibit(runtime, foreign)
                with self.assertRaises(ValueError):
                    self.observer.restore_inhibit()
                self.assertEqual(self.observer.read_inhibit(runtime), foreign)
                self.assertEqual(self.observer.read_inhibit(state), owner)

    @unittest.skipUnless(
        hasattr(os, "geteuid") and os.geteuid() == 0,
        "requires isolated root-owned filesystem",
    )
    def test_partial_clear_is_idempotent_and_restore_cannot_recreate_released_owner(
        self,
    ):
        owner = {
            "version": 1,
            "release_sha": "a" * 40,
            "owner_nonce": "11111111-1111-4111-8111-111111111111",
        }
        with tempfile.TemporaryDirectory() as tmp:
            state, runtime = Path(tmp) / "state", Path(tmp) / "run"
            state.mkdir()
            runtime.mkdir()
            with (
                patch.object(self.observer, "STATE", state),
                patch.object(self.observer, "DIRECTORY", runtime),
            ):
                self.observer.save_state(
                    self.observer.capture_name(owner), {"owner": owner}
                )
                self.observer.write_inhibit(state, owner)
                self.observer.restore_inhibit()
                self.assertTrue(
                    hasattr(self.observer, "clear_owned_inhibit"),
                    "durable clear boundary absent",
                )
                real_remove = self.observer.remove_inhibit

                def interrupted(directory, expected):
                    if directory == runtime:
                        raise OSError("simulated crash after durable clear")
                    real_remove(directory, expected)

                with (
                    patch.object(self.observer, "remove_inhibit", interrupted),
                    self.assertRaises(OSError),
                ):
                    self.observer.clear_owned_inhibit(owner)
                self.assertIsNone(self.observer.read_inhibit(state))
                self.assertEqual(self.observer.read_inhibit(runtime), owner)
                self.observer.restore_inhibit()
                self.assertIsNone(self.observer.read_inhibit(state))
                self.observer.clear_owned_inhibit(owner)
                self.observer.clear_owned_inhibit(owner)
                self.assertIsNone(self.observer.read_inhibit(runtime))

    def test_retirement_requires_actual_old_process_and_container_absence(self):
        before = {
            "host": "core",
            "boot_id": "boot",
            "retired": [],
            "processes": [{"host_pid": 1, "start_ticks": 10}],
            "containers": [{"id": "old"}],
        }
        after = {
            **before,
            "processes": [{"host_pid": 2, "start_ticks": 11}],
            "containers": [{"id": "new"}],
        }
        self.observer.verify_retired(before, after)
        for field in ("processes", "containers"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.observer.verify_retired(before, {**after, field: before[field]})
        with self.assertRaises(ValueError):
            self.observer.verify_retired(
                {**before, "retired": [{"id": "retired"}]}, after
            )

    def test_billing_process_is_not_a_recovery_state_reader(self):
        self.assertTrue(
            hasattr(self.observer, "process_role"), "process classifier absent"
        )
        self.assertIsNone(
            self.observer.process_role(["python", "-m", "yleum_api.workers.billing"])
        )
        self.assertEqual(
            self.observer.process_role(["uvicorn", "yleum_orchestrator.main:app"]),
            "orchestrator",
        )

    def test_hidden_interpreter_in_reader_cgroup_is_not_omitted(self):
        reader = {
            "host_pid": 3,
            "parent": 2,
            "role": "api",
            "argv": ["python", "-m", "uvicorn", "yleum_api.main:app"],
            "cgroup": "0::/docker/owned",
        }
        launcher = {
            "host_pid": 2,
            "parent": 1,
            "role": None,
            "argv": ["uv", "run"],
            "cgroup": reader["cgroup"],
        }
        processes = {2: launcher, 3: reader}
        self.assertTrue(
            hasattr(self.observer, "require_no_hidden_readers"),
            "hidden-process guard missing",
        )
        self.observer.require_no_hidden_readers(processes, ["/docker/owned"])
        for argv in (
            ["python", "old_alias.py"],
            ["uvicorn", "foreign:app"],
            ["gunicorn", "foreign"],
        ):
            with self.subTest(argv=argv), self.assertRaises(ValueError):
                self.observer.require_no_hidden_readers(
                    {**processes, 4: {**launcher, "host_pid": 4, "argv": argv}},
                    ["/docker/owned"],
                )


class PhysicalWriterTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "publication_drain",
            Path(__file__).with_name("generation-publication-drain.py"),
        )
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def container(self, *, helper=False, exec_ids=None):
        labels = {
            "omnia.project_machine": "true",
            "omnia.resource_kind": "project-postgres",
            **{
                "omnia." + key: "11111111-1111-4111-8111-111111111111"
                for key in ("workspace_id", "project_id", "owner_id")
            },
        }
        if helper:
            labels["omnia.helper"] = "true"
        return SimpleNamespace(
            id="a" * 64,
            name="owned-project",
            reload=lambda: None,
            attrs={
                "Id": "a" * 64,
                "Config": {"Labels": labels},
                "State": {
                    "Status": "running",
                    "Running": True,
                    "Paused": False,
                    "Dead": False,
                    "Pid": 12,
                },
                "HostConfig": {"RestartPolicy": {"Name": "no"}},
                "ExecIDs": exec_ids,
            },
        )

    def test_business_database_without_agent_exec_remains_available(self):
        container = self.container()
        client = SimpleNamespace(
            containers=SimpleNamespace(list=lambda **kwargs: [container])
        )
        with tempfile.TemporaryDirectory() as tmp:
            result = self.module.require_physical_writers_drained(Path(tmp), client)
        self.assertEqual(result["affected_containers"], 1)

    def test_unknown_prospective_command_without_docker_start_blocks_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = "11111111-1111-4111-8111-111111111111"
            directory = root / "command-operations" / workspace
            directory.mkdir(parents=True)
            (directory / "22222222-2222-4222-8222-222222222222.json").write_text(json.dumps({"binding": {"workspace_id": workspace}, "receipt": None, "response": None}))
            client = SimpleNamespace(containers=SimpleNamespace(list=lambda **kwargs: []))
            with self.assertRaises(self.module.PhysicalDrainError):
                self.module.require_physical_writers_drained(root, client)

    def test_hidden_helper_blocks_even_when_no_sql_or_command_journal_exists(self):
        container = self.container(helper=True)
        client = SimpleNamespace(
            containers=SimpleNamespace(list=lambda **kwargs: [container])
        )
        with (
            tempfile.TemporaryDirectory() as tmp,
            self.assertRaisesRegex(self.module.PhysicalDrainError, "helper is active"),
        ):
            self.module.require_physical_writers_drained(Path(tmp), client)

    def test_unknown_machine_kind_is_not_treated_as_business_runtime(self):
        container = self.container()
        container.attrs["Config"]["Labels"]["omnia.resource_kind"] = "unknown-writer"
        client = SimpleNamespace(
            containers=SimpleNamespace(list=lambda **kwargs: [container])
        )
        with (
            tempfile.TemporaryDirectory() as tmp,
            self.assertRaises(self.module.PhysicalDrainError),
        ):
            self.module.require_physical_writers_drained(Path(tmp), client)

    def test_declared_business_service_exec_is_not_a_generation_writer(self):
        container = self.container(exec_ids=["b" * 64])
        labels = container.attrs["Config"]["Labels"]
        labels.update(
            {"omnia.resource_kind": "development", "omnia.fencing_epoch": "7"}
        )
        client = SimpleNamespace(
            containers=SimpleNamespace(list=lambda **kwargs: [container]),
            api=SimpleNamespace(
                exec_inspect=lambda _: {
                    "ContainerID": container.id,
                    "Running": True,
                    "ExitCode": None,
                }
            ),
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "project-machines" / labels["omnia.workspace_id"]
            workspace.mkdir(parents=True)
            (workspace / "docker.json").write_text(
                json.dumps(
                    {
                        "epoch": 7,
                        "services": {"web": {"exec_id": "b" * 64, "epoch": 7}},
                        "exec_logs": {},
                        "manifest": {"services": [{"name": "web"}]},
                    }
                )
            )
            self.module.require_physical_writers_drained(root, client)
            # A service registration cannot launder an actual command receipt.
            (workspace / "machine.json").write_text(
                json.dumps(
                    {
                        "workspace_id": labels["omnia.workspace_id"],
                        "operations": {
                            "op": {
                                "kind": "command",
                                "state": "completed",
                                "exec_id": "b" * 64,
                            }
                        },
                    }
                )
            )
            with self.assertRaises(self.module.PhysicalDrainError):
                self.module.require_physical_writers_drained(root, client)

    def test_journal_exec_not_listed_by_container_still_inspected(self):
        container = self.container(exec_ids=[])
        labels = container.attrs["Config"]["Labels"]
        labels["omnia.resource_kind"] = "development"
        client = SimpleNamespace(
            containers=SimpleNamespace(list=lambda **kwargs: [container]),
            api=SimpleNamespace(
                exec_inspect=lambda _: {
                    "ContainerID": container.id,
                    "Running": True,
                    "ExitCode": None,
                }
            ),
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "project-machines" / labels["omnia.workspace_id"]
            workspace.mkdir(parents=True)
            (workspace / "machine.json").write_text(
                json.dumps(
                    {
                        "workspace_id": labels["omnia.workspace_id"],
                        "operations": {
                            "op": {
                                "kind": "command",
                                "state": "completed",
                                "exec_id": "b" * 64,
                            }
                        },
                    }
                )
            )
            with self.assertRaises(self.module.PhysicalDrainError):
                self.module.require_physical_writers_drained(root, client)

    def test_active_or_unknown_exec_blocks_after_terminal_journal(self):
        container = self.container(exec_ids=["b" * 64])
        for execution in (
            {"ContainerID": container.id, "Running": True, "ExitCode": None},
            {"ContainerID": container.id, "Running": False, "ExitCode": None},
            {"ContainerID": "c" * 64, "Running": False, "ExitCode": 0},
        ):
            with (
                self.subTest(execution=execution),
                tempfile.TemporaryDirectory() as tmp,
            ):
                root = Path(tmp)
                workspace = (
                    root / "project-machines" / "11111111-1111-4111-8111-111111111111"
                )
                workspace.mkdir(parents=True)
                (workspace / "machine.json").write_text(
                    '{"workspace_id":"11111111-1111-4111-8111-111111111111","operations":{"one":{"kind":"command","state":"completed"}}}'
                )
                client = SimpleNamespace(
                    containers=SimpleNamespace(list=lambda **kwargs: [container]),
                    api=SimpleNamespace(exec_inspect=lambda _: execution),
                )
                with self.assertRaisesRegex(
                    self.module.PhysicalDrainError, "exec is active or unknown"
                ):
                    self.module.require_physical_writers_drained(root, client)


@unittest.skipUnless(os.name == "posix", "release shell contract requires bash")
class ReleaseIntegrationTests(unittest.TestCase):
    def test_target_cannot_open_from_only_one_published_host(self):
        source = Path(__file__).with_name("deploy-prod.sh").read_text()
        function = (
            "reader_target() {"
            + source.split("reader_target() {", 1)[1].split("\n}\n", 1)[0]
            + "\n}\n"
        )
        first = {"verified": True, "digest": "a" * 64, "epoch": "same"}
        for second, passed in (
            (first, True),
            ({**first, "digest": "b" * 64}, False),
            ({"verified": False}, False),
        ):
            mock = (
                "reader_control() { if [ \"$1\" = core ]; then printf '%s' '"
                + json.dumps(first)
                + "'; else printf '%s' '"
                + json.dumps(second)
                + "'; fi; }\n"
            )
            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    "set -eu\nSHA=fixture\nREADER_NONCE=fixture\n"
                    + mock
                    + function
                    + "reader_target",
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode == 0, passed)

    def test_first_reader_bootstrap_rejects_terminal_saved_state(self):
        script = Path(__file__).with_name("check-active-generations.sh").read_text()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gate = root / "gate.sh"
            gate.write_text(script)
            docker = root / "docker"
            docker.write_text(
                "#!/bin/sh\ncase \"$*\" in *\"agent_state ? 'recovery_v1'\"*) printf 'cancelled|1\\n';; esac\n"
            )
            docker.chmod(0o700)
            env = {
                **os.environ,
                "PATH": str(root) + os.pathsep + os.environ["PATH"],
                "PLATFORM_DSN": "",
                "FULLSTACK_ENV": str(root / "absent"),
                "PLATFORM_CREDS": str(root / "absent"),
                "PLATFORM_CTR": "fixture",
            }
            result = subprocess.run(
                ["bash", str(gate), "--require-no-recovery-state"],
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("cancelled|1", result.stderr)
            legacy = subprocess.run(
                ["bash", str(gate)], env=env, capture_output=True, text=True
            )
            self.assertEqual(legacy.returncode, 0)


if __name__ == "__main__":
    unittest.main()
