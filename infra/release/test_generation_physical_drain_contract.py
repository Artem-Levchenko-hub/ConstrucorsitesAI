"""Actual journal models and Docker error classes; SDK is never connected."""

import copy
import hashlib
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

AVAILABLE = importlib.util.find_spec("yleum_orchestrator") is not None
WORKSPACE = "11111111-1111-4111-8111-111111111111"
OPERATION = "22222222-2222-4222-8222-222222222222"


@unittest.skipUnless(AVAILABLE, "requires cached orchestrator contract dependencies")
class PhysicalContractTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "physical_drain_contract",
            Path(__file__).with_name("generation-publication-drain.py"),
        )
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def binding(self):
        return {
            "version": 1,
            "operation_id": OPERATION,
            "workspace_id": WORKSPACE,
            "project_id": WORKSPACE,
            "owner_id": WORKSPACE,
            "generation_run_id": WORKSPACE,
            "physical_volume": "fixture-volume",
            "physical_volume_identity": "a" * 64,
            "request_sha256": "b" * 64,
            "before_revision": "c" * 64,
        }

    def terminal(self, family):
        binding = self.binding()
        if family == "source-write-operations":
            binding["fencing_epoch"] = 7
            return {
                **binding,
                "helpers": {},
                "receipt": {
                    **binding,
                    "after_revision": "d" * 64,
                    "written": 1,
                    "deleted": 0,
                },
            }
        binding["dispatch_fencing_epoch"] = 7
        identity = {
            key: "c" * 64
            for key in (
                "workspace_revision",
                "dependency_digest",
                "schema_data_digest",
                "cell_manifest_digest",
                "environment_digest",
                "build_config_digest",
            )
        }
        response = {
            "ok": True,
            "exit_code": 0,
            "detail": "",
            "timed_out": False,
            "workspace_revision": "c" * 64,
            "operation_id": OPERATION,
            "before_identity": identity,
            "after_identity": identity,
            "environment_mutated": False,
            "project_migration_receipt": None,
            "compiled_asset_receipt": None,
        }
        digest = hashlib.sha256(
            json.dumps(
                response, sort_keys=True, separators=(",", ":"), ensure_ascii=True
            ).encode()
        ).hexdigest()
        return {
            "binding": binding,
            "response": response,
            "receipt": {
                **binding,
                "after_revision": "c" * 64,
                "response_sha256": digest,
            },
        }

    def record(self, root, family, value):
        path = root / family / WORKSPACE / (OPERATION + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    def container(self, kind, identity):
        return SimpleNamespace(
            id=identity,
            name=kind,
            reload=lambda: None,
            attrs={
                "Id": identity,
                "Config": {
                    "Labels": {
                        "omnia.project_machine": "true",
                        "omnia.resource_kind": kind,
                        **{
                            "omnia." + key: WORKSPACE
                            for key in ("workspace_id", "project_id", "owner_id")
                        },
                    }
                },
                "State": {
                    "Status": "running",
                    "Running": True,
                    "Paused": False,
                    "Dead": False,
                    "Pid": 5,
                },
                "HostConfig": {"RestartPolicy": {"Name": "no"}},
                "ExecIDs": [],
            },
        )

    def machine(self, root):
        path = root / "project-machines" / WORKSPACE / "machine.json"
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps(
                {
                    "workspace_id": WORKSPACE,
                    "operations": {
                        OPERATION: {
                            "kind": "command",
                            "state": "completed",
                            "exec_id": "e" * 64,
                            "result": {
                                "operation_id": OPERATION,
                                "state": "completed",
                                "exit_code": 0,
                                "output": "",
                                "timed_out": False,
                            },
                        }
                    },
                }
            )
        )

    def test_managed_serving_processes_allow_only_confirmed_finished_execs(self):
        for kind in ("managed-max-core", "max-gateway"):
            for outcome in ("none", "finished", "running", "unknown", "foreign"):
                container = self.container(kind, "a" * 64)
                container.attrs["ExecIDs"] = [] if outcome == "none" else ["b" * 64]
                execution = {
                    "ContainerID": "c" * 64 if outcome == "foreign" else container.id,
                    "Running": outcome == "running",
                    "ExitCode": 0 if outcome in {"finished", "foreign"} else None,
                }
                client = SimpleNamespace(
                    containers=SimpleNamespace(list=lambda **kwargs: [container]),
                    api=SimpleNamespace(exec_inspect=lambda _: execution),
                )
                with (
                    self.subTest(kind=kind, outcome=outcome),
                    tempfile.TemporaryDirectory() as tmp,
                ):
                    if outcome in {"none", "finished"}:
                        self.module.require_physical_writers_drained(Path(tmp), client)
                    else:
                        with self.assertRaises(self.module.PhysicalDrainError):
                            self.module.require_physical_writers_drained(
                                Path(tmp), client
                            )

    def test_cell_bootstrap_helper_requires_strict_physical_stop(self):
        for damage in (None, "running", "paused", "dead", "pid", "restart", "owner"):
            container = self.container("core-database-bootstrap", "a" * 64)
            labels = container.attrs["Config"]["Labels"]
            labels.pop("omnia.project_machine")
            labels.update({"omnia.managed": "true", "omnia.project_cell": "true"})
            container.attrs["State"].update(Status="exited", Running=False, Pid=0)
            if damage == "running":
                container.attrs["State"].update(Status="running", Running=True, Pid=5)
            elif damage in {"paused", "dead", "pid"}:
                field = {"paused": "Paused", "dead": "Dead", "pid": "Pid"}[damage]
                container.attrs["State"][field] = 5 if damage == "pid" else True
            elif damage == "restart":
                container.attrs["HostConfig"]["RestartPolicy"]["Name"] = "always"
            elif damage == "owner":
                labels.pop("omnia.owner_id")
            client = SimpleNamespace(
                containers=SimpleNamespace(list=lambda **kwargs: [container])
            )
            with self.subTest(damage=damage), tempfile.TemporaryDirectory() as tmp:
                if damage is None:
                    self.module.require_physical_writers_drained(Path(tmp), client)
                else:
                    with self.assertRaises(self.module.PhysicalDrainError):
                        self.module.require_physical_writers_drained(Path(tmp), client)

    def test_unknown_machine_kind_remains_denied(self):
        container = self.container("unknown-writer", "a" * 64)
        client = SimpleNamespace(
            containers=SimpleNamespace(list=lambda **kwargs: [container])
        )
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(
                self.module.PhysicalDrainError, "resource kind is unknown"
            ):
                self.module.require_physical_writers_drained(Path(tmp), client)

    def kernel_fixture(self, root, *, service=True, extra_parent=None, group=None):
        group = group or "a" * 64
        from docker.errors import NotFound
        from requests import Response
        from yleum_orchestrator.services.docker_machine_backend import (
            _SERVICE_WRAPPER,
            _USERLAND_CAPS,
        )

        state, proc, groups = root / "state", root / "proc", root / "groups"
        self.machine(state)
        (proc / "1/ns").mkdir(parents=True)
        (proc / "1/ns/pid").write_text("host namespace")
        (proc / "1/task/1").mkdir(parents=True)
        (proc / "1/cgroup").write_text("0::/init.scope\n")
        (proc / "1/task/1/cgroup").write_text("0::/init.scope\n")
        (proc / "sys/kernel/random").mkdir(parents=True)
        (proc / "sys/kernel/random/boot_id").write_text(WORKSPACE)
        (proc / "container-namespace").write_text("container namespace")
        (groups / group).mkdir(parents=True)
        container = self.container("development", "a" * 64)
        labels = container.attrs["Config"]["Labels"]
        labels.update({"omnia.managed": "true", "omnia.fencing_epoch": "7"})
        idle = "import signal,sys; signal.signal(signal.SIGTERM, lambda *_: sys.exit(0)); signal.pause()"
        container.attrs["State"].update(
            Pid=100, StartedAt="fixed-start", Restarting=False
        )
        container.attrs["Config"].update(
            Entrypoint=["python3", "-c"], Cmd=[idle], User="0:0"
        )
        container.attrs["HostConfig"].update(
            Privileged=False,
            PidMode="",
            UsernsMode="",
            CgroupnsMode="private",
            CapDrop=["ALL"],
            CapAdd=_USERLAND_CAPS,
            SecurityOpt=["no-new-privileges:true"],
        )
        service_args = [
            ["node", "server.js"],
            "/workspace/.",
            "/run/service.log",
            "never",
        ]
        service_exec = {
            "ContainerID": container.id,
            "Running": True,
            "ExitCode": None,
            "Pid": 101,
            "ProcessConfig": {
                "entrypoint": "python3",
                "arguments": [
                    "-c",
                    _SERVICE_WRAPPER,
                    json.dumps(service_args),
                ],
            },
        }
        container.attrs["ExecIDs"] = ["b" * 64] if service else []
        metadata = {
            "epoch": 7,
            "manifest": {
                "services": [
                    {
                        "name": "web",
                        "argv": service_args[0],
                        "cwd": ".",
                        "restart": "never",
                    }
                ]
                if service
                else []
            },
            "services": {
                "web": {"exec_id": "b" * 64, "log": service_args[2], "epoch": 7}
            }
            if service
            else {},
            "exec_logs": {},
            "exec_pids": {},
        }
        (state / "project-machines" / WORKSPACE / "docker.json").write_text(
            json.dumps(metadata)
        )
        commands = {100: (99, ["python3", "-c", idle])}
        if service:
            commands.update(
                {
                    101: (
                        99,
                        ["python3", "-c", _SERVICE_WRAPPER, json.dumps(service_args)],
                    ),
                    102: (101, ["node", "server.js"]),
                }
            )
        if extra_parent is not None:
            commands[103] = (extra_parent, ["unconfirmed-command"])
        caps = sum(1 << bit for bit in (0, 1, 3, 4, 6, 7, 31, 5, 10, 18))
        for pid, (parent, argv) in commands.items():
            directory = proc / str(pid)
            (directory / "task" / str(pid)).mkdir(parents=True)
            (directory / "ns").mkdir()
            os.link(proc / "container-namespace", directory / "ns/pid")
            fields = ["S", str(parent), *(["0"] * 17), str(1000 + pid)]
            (directory / "stat").write_text(f"{pid} (fixture) " + " ".join(fields))
            (directory / "status").write_text(
                f"Pid:\t{pid}\nTgid:\t{pid}\nNSpid:\t{pid}\t{pid - 99}\nNoNewPrivs:\t1\n"
                + "".join(
                    f"{key}:\t{caps:x}\n"
                    for key in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb")
                )
            )
            (directory / "cgroup").write_text("0::/" + group + "\n")
            (directory / "task" / str(pid) / "cgroup").write_text("0::/" + group + "\n")
            (directory / "cmdline").write_bytes(
                b"\0".join(v.encode() for v in argv) + b"\0"
            )
            (directory / "exe").symlink_to("/usr/bin/python3")
        for name in ("cgroup.procs", "cgroup.threads"):
            (groups / group / name).write_text("\n".join(map(str, commands)) + "\n")
        missing = Response()
        missing.status_code = 404

        def inspect(exec_id):
            if exec_id == "b" * 64 and service:
                return copy.deepcopy(service_exec)
            raise NotFound("synthetic terminal exec forgotten", response=missing)

        client = SimpleNamespace(
            containers=SimpleNamespace(list=lambda **kwargs: [container]),
            api=SimpleNamespace(exec_inspect=inspect),
            info=lambda: {"ID": "fixture-daemon", "CgroupDriver": "systemd", "CgroupVersion": "2"},
        )
        return state, proc, groups, client, container, service_exec

    def test_forgotten_terminal_exec_requires_independent_current_process_absence(self):
        for service in (False, True):
            with self.subTest(service=service), tempfile.TemporaryDirectory() as tmp:
                state, proc, groups, client, _container, _exec = self.kernel_fixture(
                    Path(tmp),
                    service=service,
                )
                with (
                    patch.object(self.module, "_PROC_ROOT", proc, create=True),
                    patch.object(
                        self.module,
                        "_CGROUP_ROOT",
                        groups,
                        create=True,
                    ),
                ):
                    self.module.require_physical_writers_drained(state, client)

    def test_forgotten_terminal_exec_never_hides_unknown_or_reparented_process(self):
        for parent in (100, 999):
            with self.subTest(parent=parent), tempfile.TemporaryDirectory() as tmp:
                state, proc, groups, client, _container, _exec = self.kernel_fixture(
                    Path(tmp),
                    extra_parent=parent,
                )
                with (
                    patch.object(self.module, "_PROC_ROOT", proc, create=True),
                    patch.object(
                        self.module,
                        "_CGROUP_ROOT",
                        groups,
                        create=True,
                    ),
                    self.assertRaises(self.module.PhysicalDrainError),
                ):
                    self.module.require_physical_writers_drained(state, client)

    def test_stopped_development_requires_kernel_absence(self):
        for damage in (
            None,
            "group",
            "nested_group",
            "process",
            "task",
            "restart",
            "pid",
            "state",
            "parent",
        ):
            with self.subTest(damage=damage), tempfile.TemporaryDirectory() as tmp:
                state, proc, groups, client, container, _exec = self.kernel_fixture(
                    Path(tmp), service=False, group="owned"
                )
                container.attrs["State"].update(Status="exited", Running=False, Pid=0)
                container.attrs["HostConfig"]["CgroupParent"] = ""
                (proc / "1/cgroup").write_text("0::/init.scope\n")
                if damage in {"group", "nested_group"}:
                    path = groups / (
                        "nested" if damage == "nested_group" else "system.slice"
                    )
                    (path / ("docker-" + container.id + ".scope")).mkdir(parents=True)
                elif damage == "process":
                    (proc / "100/cgroup").write_text(
                        "0::/docker/" + container.id + "\n"
                    )
                elif damage == "task":
                    (proc / "100/task/100/cgroup").write_text(
                        "0::/docker/" + container.id + "\n"
                    )
                elif damage == "restart":
                    container.attrs["HostConfig"]["RestartPolicy"]["Name"] = "always"
                elif damage == "pid":
                    container.attrs["State"]["Pid"] = 100
                elif damage == "state":
                    container.attrs["State"]["Restarting"] = True
                elif damage == "parent":
                    container.attrs["HostConfig"]["CgroupParent"] = "unproven"
                with (
                    patch.object(self.module, "_PROC_ROOT", proc),
                    patch.object(self.module, "_CGROUP_ROOT", groups),
                ):
                    if damage is None:
                        self.module.require_physical_writers_drained(state, client)
                    else:
                        with self.assertRaises(self.module.PhysicalDrainError):
                            self.module.require_physical_writers_drained(state, client)

    def test_running_idle_null_exec_inventory_requires_complete_repeated_absence(self):
        for damage in (
            None,
            "service",
            "unknown_process",
            "reparented_process",
            "missing",
            "wrong_type",
            "manifest",
            "late_created",
            "representation_drift",
        ):
            with self.subTest(damage=damage), tempfile.TemporaryDirectory() as tmp:
                parent = {
                    "unknown_process": 100,
                    "reparented_process": 999,
                }.get(damage)
                state, proc, groups, client, container, _exec = self.kernel_fixture(
                    Path(tmp), service=damage == "service", extra_parent=parent,
                )
                container.attrs["ExecIDs"] = None
                if damage == "missing":
                    del container.attrs["ExecIDs"]
                elif damage == "wrong_type":
                    container.attrs["ExecIDs"] = {}
                elif damage == "manifest":
                    path = state / "project-machines" / WORKSPACE / "docker.json"
                    metadata = json.loads(path.read_text())
                    metadata.pop("manifest")
                    path.write_text(json.dumps(metadata))

                sample = self.module._command_absence_sample
                samples, inspected = [], []
                previous = client.api.exec_inspect

                def inspect(exec_id):
                    inspected.append(exec_id)
                    if exec_id == "d" * 64:
                        return {
                            "ContainerID": container.id,
                            "Running": False,
                            "ExitCode": None,
                        }
                    return previous(exec_id)

                def sampled(*args):
                    result = sample(*args)
                    samples.append(result)
                    if len(samples) == 1:
                        if damage == "late_created":
                            container.attrs["ExecIDs"] = ["d" * 64]
                        elif damage == "representation_drift":
                            container.attrs["ExecIDs"] = []
                    return result

                client.api.exec_inspect = inspect
                with (
                    patch.object(self.module, "_PROC_ROOT", proc),
                    patch.object(self.module, "_CGROUP_ROOT", groups),
                    patch.object(
                        self.module, "_command_absence_sample", side_effect=sampled
                    ),
                ):
                    if damage is None:
                        self.module.require_physical_writers_drained(state, client)
                        self.assertEqual(len(samples), 2)
                        self.assertTrue(all(value["exec_ids"] is None for value in samples))
                        self.assertEqual(samples[0], samples[1])
                    else:
                        with self.assertRaises(self.module.PhysicalDrainError):
                            self.module.require_physical_writers_drained(state, client)
                        if damage == "late_created":
                            self.assertIn("d" * 64, inspected)
                        elif damage == "representation_drift":
                            self.assertEqual(len(samples), 2)
                            self.assertIsNone(samples[0]["exec_ids"])
                            self.assertEqual(samples[1]["exec_ids"], [])

    def test_running_idle_historical_service_404_requires_current_absence_not_an_outcome(self):
        from docker.errors import APIError, NotFound
        from requests import Response

        for damage in (
            None,
            "missing_status",
            "wrong_status",
            "generic_404",
            "generic_500",
            "timeout",
            "live_service",
            "terminal_service",
            "foreign_service",
            "unknown_response",
            "unknown_process",
            "reparented_process",
            "thread",
            "namespace",
            "security",
            "manifest",
            "epoch",
            "service_id",
            "missing_inventory",
            "wrong_inventory_type",
            "late_exec",
            "representation_drift",
            "daemon_drift",
            "daemon_sample_drift",
            "daemon_unknown",
            "metadata_drift",
            "container_drift",
            "pid_reuse",
            "service_revives",
        ):
            with self.subTest(damage=damage), tempfile.TemporaryDirectory() as tmp:
                parent = {
                    "unknown_process": 100,
                    "reparented_process": 999,
                }.get(damage)
                state, proc, groups, client, container, _exec = self.kernel_fixture(
                    Path(tmp), service=False, extra_parent=parent,
                )
                container.attrs["ExecIDs"] = None
                metadata_path = state / "project-machines" / WORKSPACE / "docker.json"
                metadata = json.loads(metadata_path.read_text())
                metadata["manifest"]["services"] = [
                    {"name": "web", "argv": ["node", "server.js"], "cwd": ".",
                     "restart": "never"}
                ]
                metadata["services"]["web"] = {
                    "exec_id": "b" * 64, "log": "/run/service.log", "epoch": 7,
                }
                if damage == "manifest":
                    metadata["manifest"]["services"][0].pop("restart")
                elif damage == "epoch":
                    metadata["services"]["web"]["epoch"] = 8
                elif damage == "service_id":
                    metadata["services"]["web"]["exec_id"] = "malformed"
                metadata_path.write_text(json.dumps(metadata))
                journals = {
                    path: path.read_bytes() for path in (
                        metadata_path, metadata_path.with_name("machine.json"),
                    )
                }
                if damage == "missing_inventory":
                    del container.attrs["ExecIDs"]
                elif damage == "wrong_inventory_type":
                    container.attrs["ExecIDs"] = False
                elif damage == "thread":
                    (groups / container.id / "cgroup.threads").write_text("100\n999\n")
                elif damage == "namespace":
                    path = proc / "100/ns/pid"
                    path.unlink()
                    os.link(proc / "1/ns/pid", path)
                elif damage == "security":
                    container.attrs["HostConfig"]["CapAdd"] = ["SYS_ADMIN"]

                sample = self.module._command_absence_sample
                samples, inspected, daemon_reads = [], [], []
                previous = client.api.exec_inspect

                def info():
                    daemon_reads.append(True)
                    if damage == "daemon_unknown":
                        return {"ID": None}
                    changed = (
                        damage == "daemon_drift" and len(daemon_reads) > 1
                        or damage == "daemon_sample_drift" and len(daemon_reads) > 2
                    )
                    return {
                        "ID": "other-daemon" if changed else "fixture-daemon",
                        "CgroupDriver": "systemd", "CgroupVersion": "2",
                    }

                def inspect(exec_id):
                    inspected.append(exec_id)
                    if exec_id == "d" * 64:
                        return {"ContainerID": container.id, "Running": False, "ExitCode": None}
                    if exec_id == "b" * 64:
                        if damage in {"missing_status", "wrong_status", "generic_404",
                                      "generic_500"}:
                            if damage == "missing_status":
                                raise NotFound("unconfirmed status")
                            response = Response()
                            response.status_code = 500 if damage in {
                                "wrong_status", "generic_500",
                            } else 404
                            error = APIError if damage.startswith("generic") else NotFound
                            raise error("private SDK detail", response=response)
                        if damage == "timeout":
                            raise TimeoutError("unconfirmed service inventory")
                        if damage == "unknown_response":
                            return None
                        if damage in {"live_service", "terminal_service", "foreign_service"} or (
                            damage == "service_revives" and samples
                        ):
                            return {
                                "ContainerID": "f" * 64 if damage == "foreign_service"
                                else container.id,
                                "Running": damage != "terminal_service",
                                "ExitCode": 0 if damage == "terminal_service" else None,
                                "Pid": 101,
                            }
                    return previous(exec_id)

                def sampled(*args):
                    result = sample(*args)
                    samples.append(result)
                    if len(samples) == 1:
                        if damage == "late_exec":
                            container.attrs["ExecIDs"] = ["d" * 64]
                        elif damage == "representation_drift":
                            container.attrs["ExecIDs"] = []
                        elif damage == "metadata_drift":
                            metadata["services"]["web"]["log"] = "/run/changed.log"
                            metadata_path.write_text(json.dumps(metadata))
                            journals[metadata_path] = metadata_path.read_bytes()
                        elif damage == "container_drift":
                            container.attrs["State"]["StartedAt"] = "different-start"
                        elif damage == "pid_reuse":
                            path = proc / "100/stat"
                            path.write_text(path.read_text().replace("1100", "2100"))
                    return result

                client.info, client.api.exec_inspect = info, inspect
                with (
                    patch.object(self.module, "_PROC_ROOT", proc),
                    patch.object(self.module, "_CGROUP_ROOT", groups),
                    patch.object(self.module, "_command_absence_sample", side_effect=sampled),
                ):
                    if damage is None:
                        self.module.require_physical_writers_drained(state, client)
                        self.assertEqual(len(samples), 2)
                        self.assertEqual(samples[0], samples[1])
                        self.assertIsNone(samples[0]["exec_ids"])
                        self.assertEqual(samples[0]["missing_service_execs"], ("b" * 64,))
                        self.assertEqual(samples[0]["daemon"]["ID"], "fixture-daemon")
                        self.assertEqual(samples[0]["execs"], {})
                        self.assertGreaterEqual(inspected.count("b" * 64), 4)
                        self.assertEqual(len(daemon_reads), 4)
                    else:
                        with self.assertRaises(self.module.PhysicalDrainError):
                            self.module.require_physical_writers_drained(state, client)
                        if damage == "late_exec":
                            self.assertIn("d" * 64, inspected)
                        if damage in {"representation_drift", "daemon_sample_drift",
                                      "metadata_drift", "container_drift", "pid_reuse"}:
                            self.assertTrue(samples)
                self.assertEqual({path: path.read_bytes() for path in journals}, journals)

    def test_stopped_null_exec_inventory_requires_complete_repeated_absence(self):
        for damage in (
            None,
            "missing",
            "wrong_type",
            "running",
            "process",
            "task",
            "restart",
            "late_created",
            "representation_drift",
        ):
            with self.subTest(damage=damage), tempfile.TemporaryDirectory() as tmp:
                state, proc, groups, client, container, _exec = self.kernel_fixture(
                    Path(tmp), service=False, group="owned"
                )
                container.attrs["State"].update(Status="exited", Running=False, Pid=0)
                container.attrs["HostConfig"]["CgroupParent"] = ""
                container.attrs["ExecIDs"] = None
                (proc / "1/cgroup").write_text("0::/init.scope\n")
                if damage == "missing":
                    del container.attrs["ExecIDs"]
                elif damage == "wrong_type":
                    container.attrs["ExecIDs"] = {}
                elif damage == "running":
                    container.attrs["State"].update(
                        Status="running", Running=True, Pid=100
                    )
                elif damage in {"process", "task"}:
                    path = proc / (
                        "100/cgroup" if damage == "process" else "100/task/100/cgroup"
                    )
                    path.write_text("0::/docker/" + container.id + "\n")
                elif damage == "restart":
                    container.attrs["HostConfig"]["RestartPolicy"]["Name"] = "always"

                sample = self.module._command_absence_sample
                calls = 0
                inspected = []
                previous = client.api.exec_inspect

                def inspect(exec_id):
                    inspected.append(exec_id)
                    if exec_id == "d" * 64:
                        return {
                            "ContainerID": container.id,
                            "Running": False,
                            "ExitCode": None,
                        }
                    return previous(exec_id)

                def sampled(*args):
                    nonlocal calls
                    result = sample(*args)
                    calls += 1
                    if calls == 1 and damage in {
                        "late_created",
                        "representation_drift",
                    }:
                        container.attrs["ExecIDs"] = (
                            ["d" * 64] if damage == "late_created" else []
                        )
                    return result

                client.api.exec_inspect = inspect
                with (
                    patch.object(self.module, "_PROC_ROOT", proc),
                    patch.object(self.module, "_CGROUP_ROOT", groups),
                    patch.object(
                        self.module, "_command_absence_sample", side_effect=sampled
                    ),
                ):
                    if damage is None:
                        self.module.require_physical_writers_drained(state, client)
                        self.assertEqual(calls, 2)
                    else:
                        with self.assertRaises(self.module.PhysicalDrainError):
                            self.module.require_physical_writers_drained(state, client)
                        if damage == "late_created":
                            self.assertIn("d" * 64, inspected)

    def test_fresh_exec_inventory_is_revalidated_after_outer_scan(self):
        for outcome in (
            "created",
            "running",
            "foreign",
            "missing",
            "terminal",
            "null_inventory",
        ):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as tmp:
                state, proc, groups, client, container, _execution = (
                    self.kernel_fixture(Path(tmp))
                )
                previous = client.api.exec_inspect
                inspected = []
                reloads = 0

                def reload():
                    nonlocal reloads
                    reloads += 1
                    if reloads == 2:
                        container.attrs["ExecIDs"] = (
                            None
                            if outcome == "null_inventory"
                            else ["b" * 64, "d" * 64]
                        )

                def inspect(exec_id):
                    inspected.append(exec_id)
                    if exec_id == "d" * 64 and outcome != "missing":
                        return {
                            "ContainerID": "f" * 64
                            if outcome == "foreign"
                            else container.id,
                            "Running": outcome == "running",
                            "ExitCode": 0
                            if outcome in {"terminal", "foreign"}
                            else None,
                        }
                    return previous(exec_id)

                container.reload = reload
                client.api.exec_inspect = inspect
                with (
                    patch.object(self.module, "_PROC_ROOT", proc),
                    patch.object(self.module, "_CGROUP_ROOT", groups),
                ):
                    if outcome == "terminal":
                        self.module.require_physical_writers_drained(state, client)
                        self.assertGreaterEqual(inspected.count("d" * 64), 2)
                    else:
                        with self.assertRaises(self.module.PhysicalDrainError):
                            self.module.require_physical_writers_drained(state, client)

    def test_kernel_cgroup_requires_exact_docker_identity_component(self):
        for group in (
            "foreign",
            "a" * 63,
            "a" * 64 + "suffix",
            "docker-" + "a" * 64 + ".scope.extra",
        ):
            with self.subTest(group=group), tempfile.TemporaryDirectory() as tmp:
                state, proc, groups, client, _container, _execution = (
                    self.kernel_fixture(Path(tmp), group=group)
                )
                with (
                    patch.object(self.module, "_PROC_ROOT", proc),
                    patch.object(self.module, "_CGROUP_ROOT", groups),
                ):
                    with self.assertRaises(self.module.PhysicalDrainError):
                        self.module.require_physical_writers_drained(state, client)

    def test_kernel_proof_security_and_service_binding_fail_closed(self):
        damages = (
            "privileged",
            "host_pid",
            "userns",
            "cgroupns",
            "caps",
            "no_new_privs",
            "idle_config",
            "idle_process",
            "boot",
            "thread",
            "nested",
            "service_pid",
            "service_container",
            "service_args",
            "service_missing",
            "namespace",
            "process_caps",
            "read",
        )
        for damage in damages:
            with self.subTest(damage=damage), tempfile.TemporaryDirectory() as tmp:
                state, proc, groups, client, container, execution = self.kernel_fixture(
                    Path(tmp)
                )
                host = container.attrs["HostConfig"]
                host_changes = {
                    "privileged": ("Privileged", True),
                    "host_pid": ("PidMode", "host"),
                    "userns": ("UsernsMode", "host"),
                    "cgroupns": ("CgroupnsMode", "host"),
                    "caps": ("CapAdd", ["SYS_ADMIN"]),
                    "no_new_privs": ("SecurityOpt", []),
                }
                if damage in host_changes:
                    key, value = host_changes[damage]
                    host[key] = value
                elif damage == "idle_config":
                    container.attrs["Config"]["Cmd"] = ["unconfirmed"]
                elif damage == "idle_process":
                    (proc / "100/cmdline").write_bytes(b"writer\0")
                elif damage == "boot":
                    (proc / "sys/kernel/random/boot_id").write_text("unknown")
                elif damage == "thread":
                    (groups / container.id / "cgroup.threads").write_text(
                        "100\n101\n102\n999\n"
                    )
                elif damage == "nested":
                    (groups / container.id / "delegated").mkdir()
                elif damage == "service_pid":
                    execution["Pid"] = 999
                elif damage == "service_container":
                    execution["ContainerID"] = "f" * 64
                elif damage == "service_args":
                    execution["ProcessConfig"]["arguments"][-1] = "[]"
                elif damage == "service_missing":
                    execution["Running"] = False
                elif damage == "namespace":
                    (proc / "102/ns/pid").unlink()
                    os.link(proc / "1/ns/pid", proc / "102/ns/pid")
                elif damage == "process_caps":
                    path = proc / "102/status"
                    path.write_text(
                        path.read_text().replace("CapEff:", "OldCapEff:")
                        + "CapEff:\tffffffff\n"
                    )
                elif damage == "read":
                    (proc / "102/stat").unlink()
                with (
                    patch.object(self.module, "_PROC_ROOT", proc),
                    patch.object(self.module, "_CGROUP_ROOT", groups),
                ):
                    with self.assertRaises(self.module.PhysicalDrainError):
                        self.module.require_physical_writers_drained(state, client)

    def test_kernel_proof_rejects_identity_drift_between_samples(self):
        for damage in ("pid_reuse", "boot", "started", "exec_ids", "service_pid"):
            with self.subTest(damage=damage), tempfile.TemporaryDirectory() as tmp:
                state, proc, groups, client, container, execution = self.kernel_fixture(
                    Path(tmp)
                )
                sample = self.module._command_absence_sample
                count = 0

                def changed(*args):
                    nonlocal count
                    result = sample(*args)
                    count += 1
                    if count == 1:
                        if damage == "pid_reuse":
                            path = proc / "102/stat"
                            path.write_text(path.read_text().replace("1102", "2102"))
                        elif damage == "boot":
                            (proc / "sys/kernel/random/boot_id").write_text(OPERATION)
                        elif damage == "started":
                            container.attrs["State"]["StartedAt"] = "new-start"
                        elif damage == "exec_ids":
                            container.attrs["ExecIDs"].append("f" * 64)
                        else:
                            execution["Pid"] = 102
                    return result

                with (
                    patch.object(self.module, "_PROC_ROOT", proc),
                    patch.object(self.module, "_CGROUP_ROOT", groups),
                    patch.object(
                        self.module,
                        "_command_absence_sample",
                        side_effect=changed,
                    ),
                    self.assertRaises(self.module.PhysicalDrainError),
                ):
                    self.module.require_physical_writers_drained(state, client)

    def test_typed_terminal_positive_and_missing_or_mismatched_identity_denied(self):
        client = SimpleNamespace(containers=SimpleNamespace(list=lambda **kwargs: []))
        for family in ("source-write-operations", "command-operations"):
            terminal = self.terminal(family)
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                self.record(root, family, terminal)
                self.module.require_physical_writers_drained(root, client)
                variants = []
                for key in (
                    "owner_id",
                    "project_id",
                    "generation_run_id",
                    "physical_volume_identity",
                    "before_revision",
                ):
                    value = copy.deepcopy(terminal)
                    value["receipt"].pop(key)
                    variants.append(value)
                value = copy.deepcopy(terminal)
                value["receipt"]["owner_id"] = OPERATION
                variants.append(value)
                value = copy.deepcopy(terminal)
                value["receipt"]["version"] = True
                variants.append(value)
                variants.append(
                    {"workspace_id": WORKSPACE, "receipt": {}, "helpers": {}}
                    if family == "source-write-operations"
                    else {
                        "binding": {
                            "workspace_id": WORKSPACE,
                            "operation_id": OPERATION,
                        },
                        "response": {"operation_id": OPERATION},
                        "receipt": {},
                    }
                )
                for value in variants:
                    self.record(root, family, value)
                    with (
                        self.subTest(family=family, value_fields=list(value)),
                        self.assertRaises(self.module.PhysicalDrainError),
                    ):
                        self.module.require_physical_writers_drained(root, client)

    def test_terminal_development_command_does_not_bind_to_postgres(self):
        development, postgres = (
            self.container("development", "a" * 64),
            self.container("project-postgres", "b" * 64),
        )
        calls = []

        def inspect(exec_id):
            calls.append(exec_id)
            return {"ContainerID": development.id, "Running": False, "ExitCode": 0}

        client = SimpleNamespace(
            containers=SimpleNamespace(list=lambda **kwargs: [development, postgres]),
            api=SimpleNamespace(exec_inspect=inspect),
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.machine(root)
            self.module.require_physical_writers_drained(root, client)
        self.assertEqual(calls, ["e" * 64])

    def test_exact_missing_terminal_exec_requires_no_development(self):
        from docker.errors import APIError, NotFound
        from requests import Response

        missing = Response()
        missing.status_code = 404
        for kind, error, expected in (
            (None, NotFound("private", response=missing), True),
            ("development", NotFound("private", response=missing), False),
            ("stopped", NotFound("private", response=missing), False),
            (None, APIError("private500"), False),
            (None, NotFound("unknown-status"), False),
        ):
            containers = (
                [] if kind is None else [self.container("development", "a" * 64)]
            )
            if kind == "stopped":
                containers[0].attrs["State"].update(
                    Status="exited", Running=False, Pid=0
                )

            def inspect(_, error=error):
                raise error

            client = SimpleNamespace(
                containers=SimpleNamespace(list=lambda **kwargs: containers),
                api=SimpleNamespace(exec_inspect=inspect),
            )
            with (
                self.subTest(kind=kind, expected=expected),
                tempfile.TemporaryDirectory() as tmp,
            ):
                root = Path(tmp)
                self.machine(root)
                if expected:
                    self.module.require_physical_writers_drained(root, client)
                else:
                    with self.assertRaises(self.module.PhysicalDrainError):
                        self.module.require_physical_writers_drained(root, client)

    def test_missing_exec_cannot_hide_new_development_at_final_enumeration(self):
        from docker.errors import NotFound
        from requests import Response

        response = Response()
        response.status_code = 404
        enumerations = iter([[], [self.container("development", "a" * 64)]])

        def inspect(_):
            raise NotFound("private", response=response)

        client = SimpleNamespace(
            containers=SimpleNamespace(list=lambda **kwargs: next(enumerations)),
            api=SimpleNamespace(exec_inspect=inspect),
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.machine(root)
            with self.assertRaisesRegex(
                self.module.PhysicalDrainError, "inventory changed"
            ):
                self.module.require_physical_writers_drained(root, client)


if __name__ == "__main__":
    unittest.main()
