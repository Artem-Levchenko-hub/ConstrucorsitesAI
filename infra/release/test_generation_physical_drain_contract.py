"""Actual journal models and Docker error classes; SDK is never connected."""

import copy
import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

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
