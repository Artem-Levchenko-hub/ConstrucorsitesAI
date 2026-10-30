"""Root filesystem/flock and typed controller-state contracts, no Docker daemon."""

import copy
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import tempfile
import unittest
from dataclasses import asdict
from itertools import product
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
        # Historical authority is an independent fixture, not whatever the
        # newest production table happens to permit.
        self.old = {
            "services.project_machine": "c265481789481f8b4f3f486f4f44345cdf835759204f44b1c7d5667373f171b3",
            "services.docker_machine_backend": "8c963461e50b3bc3aa29d564a711ee5a656356d7f6fd65c739d9ae74288aa10a",
            "services.machine_adapter": "7a8fee13333fee8a79478bc9f64b895a67b1deed284335355158b8a95103d426",
            "routers.workspace": "4ad3af972da6e73af3e15eb407cff041f15321cf9af83466502c14c2f6dd0dc6",
            "services.cell_state": "c506c7071f9b067f7819745c104d207dc496209c576bce3179f505516f2a1d42",
            "services.cell_lock": "1bf272c831eec8446270adf098de101db2aac6e02b5f18ea86ff43c81bfb39b3",
            "services.workspace_provider_factory": "5fc8c2f122c00f9da29439467849b94bcf4eaf3dad11a7d7f04111cc592b7a9d",
            "services.generation_reader_inventory": "119a83b06d26c2936d1fdf6140c824cb53fee761d7f0d4fe5b8465f34ed04a6e",
            "services.docker_cell_resources": "6a7f931d4a17bc4517137de069186eb6bb4434e65ce32429de3ac46b3ff52710",
        }
        self.new = dict(self.old)
        self.new.update(
            {
                "services.machine_adapter": "944528d2805e971120b32336d42adc6544538043fc60ff4c6219de10aabc2611",
                "routers.workspace": "f82e0bb183f1258cccf393d58e9ee047a6b15cf74b2c3ffdb98fd9111c525e29",
            }
        )
        self.snapshot = {
            **self.new,
            "routers.workspace": "25a841e7d4b4f9535c0fe2f415bac0172874ad2bf1fce83b82b7c28792db5213",
        }
        self.catalog = {
            **self.old,
            "services.docker_machine_backend": "f97cc42908ac85602ffbd006d3aaad6845b4dbd1a74cf23c13b55fdb282a6a4e",
            "services.machine_adapter": "80b8a8033be6c314ffcd0bba93c027cce6b50b68a7197ed93078d115254cb661",
            "routers.workspace": "a32fedaf6bb0bca42950a8f1f777937314154ef6d93436b4a0d7610423dc769a",
            "services.app_business_config": "2e6a05f2b43773fbb8ae5b0fcdf9effdb1ace86782592fa74d2dba363cd85601",
        }
        self.owner = json.loads(
            (Path(__file__).parent / "fixtures/owner-management-quiescence-sources.json").read_text()
        )
        self.connect = json.loads(
            (Path(__file__).parent / "fixtures/connect-startup-quiescence-sources.json").read_text()
        )
        self.legacy_catalog = json.loads(
            (Path(__file__).parent / "fixtures/legacy-catalog-quiescence-sources.json").read_text()
        )
        self.reviewed = (
            self.old, self.new, self.snapshot, self.catalog, self.legacy_catalog, self.owner,
            self.connect,
        )

    def test_live_and_reviewed_successor_are_complete_supported_bundles(self):
        self.assertEqual(len(self.old), 9)
        self.assertEqual(self.checker._PORTABLE_QUIESCENCE_SOURCES, self.old)
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
        for sources in (self.old, self.new, self.snapshot):
            with self.subTest(sources=sources):
                before = copy.deepcopy(sources)
                self.checker._require_reviewed_portable_sources(sources)
                self.assertEqual(sources, before)
        # Receipts continue binding the actual observed bundle, not a common alias.
        self.assertNotEqual(self.checker._digest(self.old), self.checker._digest(self.new))
        self.assertNotEqual(self.checker._digest(self.new), self.checker._digest(self.snapshot))

    def test_reviewed_catalog_bundle_is_accepted_with_its_dependency(self):
        before = copy.deepcopy(self.catalog)
        self.checker._require_reviewed_portable_sources(self.catalog)
        self.assertEqual(self.catalog, before)
        self.assertEqual(len(self.catalog), 10)
        self.assertNotEqual(self.checker._digest(self.catalog), self.checker._digest(self.snapshot))

    def test_collector_binds_present_legacy_helpers_and_rejects_missing_tampered_or_mixed(self):
        # Pinned independently from e5adb011fd4e9a78629ef8c7caff60d582bc9b06.
        # CI has a shallow checkout: exercise real filesystem/spec discovery,
        # substituting only the digest boundary, not the collector or authority.
        self.assertEqual(len(self.legacy_catalog), 13)
        self.assertEqual(
            {name: self.legacy_catalog[name] for name in self.catalog}, self.catalog,
        )
        real_sha256 = hashlib.sha256
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            digests = {}
            paths = {}
            for name, digest in self.legacy_catalog.items():
                path = root.joinpath(*name.split(".")).with_suffix(".py")
                path.parent.mkdir(parents=True, exist_ok=True)
                content = f"# immutable e5 module fixture: {name}\n".encode()
                path.write_bytes(content)
                paths[name] = path
                digests[content] = digest

            def find_spec(name):
                relative = name.removeprefix("yleum_orchestrator.").split(".")
                parent = root.joinpath(*relative[:-1])
                return importlib.machinery.PathFinder.find_spec(name, [str(parent)])

            def digest_bytes(content):
                if content not in digests:
                    return real_sha256(content)
                return SimpleNamespace(hexdigest=lambda: digests[content])

            with (
                patch.object(importlib.util, "find_spec", side_effect=find_spec),
                patch.object(hashlib, "sha256", side_effect=digest_bytes),
            ):
                self.assertEqual(self.checker._portable_quiescence_sources(), self.legacy_catalog)
                for name, path in paths.items():
                    content = path.read_bytes()
                    path.unlink()
                    with self.subTest(missing=name), self.assertRaisesRegex(
                        self.checker.PhysicalDrainError, "quiescence_unsupported_source",
                    ):
                        self.checker._portable_quiescence_sources()
                    path.write_bytes(content + b"# unknown edit\n")
                    with self.subTest(tampered=name), self.assertRaisesRegex(
                        self.checker.PhysicalDrainError, "quiescence_unsupported_source",
                    ):
                        self.checker._portable_quiescence_sources()
                    path.write_bytes(content)
                    if self.owner[name] != self.legacy_catalog[name]:
                        digests[content] = self.owner[name]
                        with self.subTest(mixed=name), self.assertRaisesRegex(
                            self.checker.PhysicalDrainError, "quiescence_unsupported_source",
                        ):
                            self.checker._portable_quiescence_sources()
                        digests[content] = self.legacy_catalog[name]
                extra = root / "services/owner_management_identity.py"
                extra.write_bytes(b"# unexpected owner identity helper\n")
                with self.assertRaisesRegex(
                    self.checker.PhysicalDrainError, "quiescence_unsupported_source",
                ):
                    self.checker._portable_quiescence_sources()

    def test_owner_bundle_preserves_historical_trust_and_exact_dependency_closure(self):
        self.assertEqual(len(self.owner), 14)
        self.assertEqual(self.checker._PORTABLE_QUIESCENCE_OWNER_SOURCES, self.owner)
        self.checker._require_reviewed_portable_sources(self.owner)
        self.checker._require_reviewed_portable_sources(self.catalog)
        for changed in self.owner:
            mixed = dict(self.owner)
            if changed in self.catalog:
                mixed[changed] = self.catalog[changed]
            else:
                del mixed[changed]
            if mixed == self.owner:
                continue
            with self.assertRaisesRegex(self.checker.PhysicalDrainError, "quiescence_unsupported_source"):
                self.checker._require_reviewed_portable_sources(mixed)

    def test_connect_successor_preserves_historical_owner_and_changes_only_egress(self):
        self.assertEqual(len(self.connect), 14)
        self.assertEqual(self.checker._PORTABLE_QUIESCENCE_CONNECT_SOURCES, self.connect)
        self.assertEqual(set(self.connect), set(self.owner))
        self.assertEqual(
            {name for name in self.connect if self.connect[name] != self.owner[name]},
            {"services.machine_egress"},
        )
        self.checker._require_reviewed_portable_sources(self.connect)
        self.checker._require_reviewed_portable_sources(self.owner)

    def test_connect_bundle_matches_exact_current_source(self):
        root = Path(__file__).resolve().parents[2] / "apps/orchestrator/src/yleum_orchestrator"
        for name, expected in self.connect.items():
            source = root.joinpath(*name.split(".")).with_suffix(".py").read_bytes()
            # Git checkouts on Windows may materialize CRLF; live Linux bytes
            # and the authority table remain exact LF source, never normalized.
            actual = hashlib.sha256(source.replace(b"\r\n", b"\n")).hexdigest()
            self.assertEqual(actual, expected, name)

    def test_mixed_live_and_successor_modules_are_never_reviewed(self):
        for successor in (self.new, self.snapshot):
            for changed in ("services.machine_adapter", "routers.workspace"):
                sources = dict(self.old)
                sources[changed] = successor[changed]
                with self.subTest(changed=changed, successor=successor):
                    with self.assertRaisesRegex(
                        self.checker.PhysicalDrainError, "quiescence_unsupported_source"
                    ):
                        self.checker._require_reviewed_portable_sources(sources)

    def test_every_unknown_source_hash_is_denied_for_both_bundles(self):
        for original in self.reviewed:
            for changed in original:
                sources = dict(original)
                sources[changed] = "0" * 64
                with self.subTest(changed=changed, original=original):
                    with self.assertRaisesRegex(
                        self.checker.PhysicalDrainError, "quiescence_unsupported_source"
                    ):
                        self.checker._require_reviewed_portable_sources(sources)

    def test_missing_or_extra_source_cannot_be_an_accepted_bundle(self):
        for original in self.reviewed:
            for changed in original:
                sources = dict(original)
                del sources[changed]
                with self.subTest(missing=changed, original=original):
                    with self.assertRaisesRegex(
                        self.checker.PhysicalDrainError, "quiescence_unsupported_source"
                    ):
                        self.checker._require_reviewed_portable_sources(sources)
        sources = {**self.new, "services.unreviewed_producer": "0" * 64}
        with self.assertRaisesRegex(
            self.checker.PhysicalDrainError, "quiescence_unsupported_source"
        ):
            self.checker._require_reviewed_portable_sources(sources)

    def test_dependency_absence_is_allowed_only_for_exact_historical_bundles(self):
        name = "services.app_business_config"
        for original in (self.old, self.new, self.snapshot):
            self.checker._require_reviewed_portable_sources(original)
            with self.assertRaisesRegex(
                self.checker.PhysicalDrainError, "quiescence_unsupported_source"
            ):
                self.checker._require_reviewed_portable_sources({**original, name: self.catalog[name]})
        missing = {key: value for key, value in self.catalog.items() if key != name}
        with self.assertRaisesRegex(
            self.checker.PhysicalDrainError, "quiescence_unsupported_source"
        ):
            self.checker._require_reviewed_portable_sources(missing)

    def test_every_unreviewed_cross_product_of_known_hashes_is_denied(self):
        names = sorted({name for bundle in self.reviewed for name in bundle})
        choices = [tuple(dict.fromkeys(bundle.get(name) for bundle in self.reviewed)) for name in names]
        accepted = []
        for values in product(*choices):
            sources = {name: value for name, value in zip(names, values, strict=True) if value is not None}
            if sources in self.reviewed:
                self.checker._require_reviewed_portable_sources(sources)
                accepted.append(sources)
            else:
                with self.subTest(sources=sources), self.assertRaisesRegex(
                    self.checker.PhysicalDrainError, "quiescence_unsupported_source"
                ):
                    self.checker._require_reviewed_portable_sources(sources)
        self.assertEqual(len(accepted), len(self.reviewed))

    def test_collection_binds_current_helper_bytes_and_rejects_absence_or_tamper(self):
        root = Path(__file__).resolve().parents[2] / "apps/orchestrator/src/yleum_orchestrator"
        with tempfile.TemporaryDirectory() as temporary:
            origins = {}
            for name in self.connect:
                source = root.joinpath(*name.split(".")).with_suffix(".py").read_bytes()
                path = Path(temporary) / (name + ".py")
                path.write_bytes(source.replace(b"\r\n", b"\n"))
                origins["yleum_orchestrator." + name] = SimpleNamespace(origin=str(path))
            with patch.object(importlib.util, "find_spec", side_effect=origins.get):
                self.assertEqual(self.checker._portable_quiescence_sources(), self.connect)
                helper = origins.pop("yleum_orchestrator.services.app_business_config")
                with self.assertRaisesRegex(
                    self.checker.PhysicalDrainError, "quiescence_unsupported_source"
                ):
                    self.checker._portable_quiescence_sources()
                origins["yleum_orchestrator.services.app_business_config"] = helper
                Path(helper.origin).write_bytes(b"# unreviewed helper\n")
                with self.assertRaisesRegex(
                    self.checker.PhysicalDrainError, "quiescence_unsupported_source"
                ):
                    self.checker._portable_quiescence_sources()


class ProcessObservationRetryTests(unittest.TestCase):
    """A disappearing task invalidates its whole sample, never a single row."""

    def setUp(self):
        self.checker = _checker_module()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.proc, self.groups = self.root / "proc", self.root / "groups"
        self.proc.mkdir()
        self.groups.mkdir()
        for filename in ("cgroup.procs", "cgroup.threads"):
            (self.groups / filename).write_text("")
        (self.proc / "sys/kernel/random").mkdir(parents=True)
        (self.proc / "sys/kernel/random/boot_id").write_text(WORKSPACE)
        for name, value in (("_PROC_ROOT", self.proc), ("_CGROUP_ROOT", self.groups)):
            patcher = patch.object(self.checker, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.process(1)
        self.process(42)

    def process(self, pid, membership="init.scope"):
        directory = self.proc / str(pid)
        task = directory / "task" / str(pid)
        task.mkdir(parents=True)
        for member in (directory, task):
            (member / "cgroup").write_text("0::/" + membership + "\n")
            (member / "stat").write_text(f"{pid} (fixture) S " + "0 " * 18 + "100\n")
        return directory

    def observe(self):
        with patch.object(self.checker, "_quiescent_roster", return_value={}), patch.object(
            self.checker, "_quiescence_binding", return_value=({}, None)
        ):
            return self.checker._quiescence_physical(None, WORKSPACE, 0, None, {}, None)

    def test_disappearing_process_restarts_a_complete_cgroup_and_process_sample(self):
        original_iterdir = Path.iterdir
        original_ids = self.checker._kernel_ids
        group_observations = []

        def read_ids(path):
            group_observations.append(path)
            return original_ids(path)

        def exiting_process(path):
            if path == self.proc / "42/task":
                (self.proc / "42").rename(self.root / "retired")
            return original_iterdir(path)

        with patch.object(Path, "iterdir", exiting_process), patch.object(
            self.checker, "_kernel_ids", read_ids
        ):
            result = self.observe()
        self.assertTrue(result["no_orphan_docker_membership"])
        self.assertEqual(group_observations.count(self.groups / "cgroup.procs"), 3)

    def test_restarted_sample_detects_a_new_orphan_instead_of_skipping_lost_row(self):
        original = Path.iterdir

        def replace_process(path):
            if path == self.proc / "42/task":
                (self.proc / "42").rename(self.root / "retired")
                self.process(99, "system.slice/docker-" + "f" * 64 + ".scope")
            return original(path)

        with patch.object(Path, "iterdir", replace_process), self.assertRaisesRegex(
            self.checker.PhysicalDrainError, "quiescence_orphan_process"
        ):
            self.observe()

    def test_continuously_racing_sample_is_bounded_and_never_accepted(self):
        original = Path.iterdir
        attempts = []

        def racing_process(path):
            if path == self.proc:
                if not (self.proc / "42").exists():
                    self.process(42)
            elif path == self.proc / "42/task":
                attempts.append(path)
                (self.proc / "42").rename(self.root / f"retired-{len(attempts)}")
            return original(path)

        with patch.object(Path, "iterdir", racing_process), self.assertRaises(
            self.checker.ProcessDisappeared
        ):
            self.observe()
        self.assertEqual(len(attempts), 5)

    def test_permission_denial_is_not_a_retryable_process_exit(self):
        original = Path.iterdir
        attempts = []

        def inaccessible_process(path):
            if path == self.proc / "42/task":
                attempts.append(path)
                raise PermissionError(13, "denied", str(path))
            return original(path)

        with patch.object(Path, "iterdir", inaccessible_process), self.assertRaises(PermissionError):
            self.observe()
        self.assertEqual(len(attempts), 1)

    def test_second_scan_race_discards_the_first_successful_scan_too(self):
        original = Path.iterdir
        task_observations = []
        group_observations = []
        original_ids = self.checker._kernel_ids

        def read_ids(path):
            group_observations.append(path)
            return original_ids(path)

        def exiting_process(path):
            if path == self.proc / "42/task":
                task_observations.append(path)
                if len(task_observations) == 2:
                    (self.proc / "42").rename(self.root / "retired")
            return original(path)

        with patch.object(Path, "iterdir", exiting_process), patch.object(
            self.checker, "_kernel_ids", read_ids
        ):
            self.observe()
        self.assertEqual(group_observations.count(self.groups / "cgroup.procs"), 4)

    def test_missing_membership_of_live_process_is_not_retried(self):
        original = self.checker._kernel_read
        attempts = []

        def missing_file(path):
            if path == self.proc / "42/cgroup":
                attempts.append(path)
                raise FileNotFoundError(2, "membership missing", str(path))
            return original(path)

        with patch.object(self.checker, "_kernel_read", missing_file), self.assertRaises(FileNotFoundError):
            self.observe()
        self.assertEqual(len(attempts), 1)

    def test_reused_process_identity_fails_closed(self):
        original = self.checker._kernel_read

        def reuse_pid(path):
            result = original(path)
            if path == self.proc / "42/cgroup":
                (self.proc / "42/stat").write_text("42 (replacement) S " + "0 " * 18 + "200\n")
            return result

        with patch.object(self.checker, "_kernel_read", reuse_pid), self.assertRaisesRegex(
            self.checker.PhysicalDrainError, "process_identity_changed"
        ):
            self.observe()

    def test_stat_parser_accepts_embedded_delimiter_and_non_ascii_comm(self):
        path = self.proc / "42"
        (path / "stat").write_bytes(b"42 (name ) \xff) S " + b"0 " * 18 + b"0\n")
        self.assertEqual(self.checker._process_start(path), 0)

    def test_task_exit_restarts_the_entire_sample(self):
        task = self.proc / "42/task/77"
        task.mkdir()
        (task / "stat").write_bytes(b"77 (fixture) S " + b"0 " * 18 + b"100\n")
        (task / "cgroup").write_text("0::/init.scope\n")
        original = self.checker._kernel_read

        def exit_task(path):
            if path == task / "cgroup":
                task.rename(self.root / "retired-thread")
            return original(path)

        with patch.object(self.checker, "_kernel_read", exit_task):
            self.assertTrue(self.observe()["no_orphan_docker_membership"])

    def test_task_pid_reuse_is_denied(self):
        task = self.proc / "42/task/77"
        task.mkdir()
        (task / "stat").write_bytes(b"77 (fixture) S " + b"0 " * 18 + b"100\n")
        (task / "cgroup").write_text("0::/init.scope\n")
        original = self.checker._kernel_read

        def reused_task(path):
            result = original(path)
            if path == task / "cgroup":
                (task / "stat").write_bytes(b"77 (replacement) S " + b"0 " * 18 + b"200\n")
            return result

        with patch.object(self.checker, "_kernel_read", reused_task), self.assertRaisesRegex(
            self.checker.PhysicalDrainError, "process_identity_changed"
        ):
            self.observe()

    def test_stat_parser_rejects_truncated_header_wrong_pid_and_unknown_state(self):
        path = self.proc / "42"
        for raw in (
            b"42 (fixture S " + b"0 " * 18 + b"100\n",
            b"41 (fixture) S " + b"0 " * 18 + b"100\n",
            b"42 (fixture) ? " + b"0 " * 18 + b"100\n",
        ):
            with self.subTest(raw=raw):
                (path / "stat").write_bytes(raw)
                with self.assertRaisesRegex(self.checker.PhysicalDrainError, "process_stat"):
                    self.checker._process_start(path)


class StoppedProcessObservationRetryTests(unittest.TestCase):
    """Portable real /proc fixture; Docker sample boundary remains independently checked."""

    setUp = ProcessObservationRetryTests.setUp
    process = ProcessObservationRetryTests.process

    def observe(self):
        self.samples = []

        def sample(*_):
            self.samples.append(True)
            return self.checker._stopped_kernel_absence("a" * 64)

        with patch.object(self.checker, "_command_absence_sample", sample):
            self.checker._require_current_command_absence(self.root, None, [None])

    def race(self, *, task=False, second=False):
        original_iterdir, original_read = Path.iterdir, self.checker._kernel_read
        scans = []
        victim = self.proc / ("42/task/77" if task else "42")
        if task:
            victim.mkdir()
            (victim / "stat").write_text("77 (fixture) S " + "0 " * 18 + "100\n")
            (victim / "cgroup").write_text("0::/init.scope\n")
        failing_sample = 2 if second else 1

        def scan(path):
            if path == self.groups:
                scans.append(True)
            if not task and path == victim / "task" and len(self.samples) == failing_sample:
                victim.rename(self.root / "retired")
            return original_iterdir(path)

        def read(path):
            if task and path == victim / "cgroup" and len(self.samples) == failing_sample:
                victim.rename(self.root / "retired")
            return original_read(path)

        with patch.object(Path, "iterdir", scan), patch.object(self.checker, "_kernel_read", read):
            self.observe()
        self.assertEqual(len(self.samples), failing_sample + 2)
        self.assertEqual(len(scans), failing_sample + 2)

    def test_gone_process_restarts_complete_pair(self):
        self.race()

    def test_second_sample_process_exit_discards_first_sample(self):
        self.race(second=True)

    def test_gone_task_restarts_complete_pair(self):
        self.race(task=True)

    def test_second_sample_task_exit_discards_first_sample(self):
        self.race(task=True, second=True)

    def test_live_membership_missing_is_not_retried(self):
        (self.proc / "42/cgroup").unlink()
        with self.assertRaises(self.checker.PhysicalDrainError) as caught:
            self.observe()
        self.assertIsInstance(caught.exception.__cause__, FileNotFoundError)
        self.assertEqual(len(self.samples), 1)

    def test_start_identity_change_is_not_retried(self):
        original = self.checker._kernel_read

        def read(path):
            result = original(path)
            if path == self.proc / "42/cgroup":
                (self.proc / "42/stat").write_text("42 (new) S " + "0 " * 18 + "999\n")
            return result

        with patch.object(self.checker, "_kernel_read", read), self.assertRaisesRegex(
            self.checker.PhysicalDrainError, "process_identity_changed"
        ):
            self.observe()
        self.assertEqual(len(self.samples), 1)

    def test_repeated_process_exit_stops_after_five_complete_attempts(self):
        original = Path.iterdir

        def scan(path):
            if path == self.proc and not (self.proc / "42").exists():
                self.process(42)
            elif path == self.proc / "42/task":
                (self.proc / "42").rename(self.root / f"retired-{len(self.samples)}")
            return original(path)

        with patch.object(Path, "iterdir", scan), self.assertRaises(self.checker.ProcessDisappeared):
            self.observe()
        self.assertEqual(len(self.samples), 5)

    def test_permission_and_missing_cgroup_directory_are_not_retried(self):
        original = Path.iterdir
        for failure in (PermissionError("denied"), FileNotFoundError("missing cgroup")):
            def scan(path):
                if path == self.groups:
                    raise failure
                return original(path)

            with patch.object(Path, "iterdir", scan), self.assertRaises(
                self.checker.PhysicalDrainError
            ) as caught:
                self.observe()
            self.assertIs(caught.exception.__cause__, failure)
            self.assertEqual(len(self.samples), 1)

    def test_unknown_process_and_docker_timeout_do_not_retry_sample(self):
        for failure in (self.checker.PhysicalDrainError("unknown_process"), TimeoutError("Docker")):
            with patch.object(self.checker, "_command_absence_sample", side_effect=failure) as sample:
                with self.assertRaises(self.checker.PhysicalDrainError) as caught:
                    self.checker._require_current_command_absence(self.root, None, [None])
            self.assertEqual(sample.call_count, 1)
            if isinstance(failure, self.checker.PhysicalDrainError):
                self.assertIs(caught.exception, failure)
            else:
                self.assertIs(caught.exception.__cause__, failure)

    def test_restart_observes_new_residual_instead_of_skipping_lost_process(self):
        original = Path.iterdir

        def scan(path):
            if path == self.proc / "42/task":
                (self.proc / "42").rename(self.root / "retired")
                (self.groups / ("docker-" + "a" * 64 + ".scope")).mkdir()
            return original(path)

        with patch.object(Path, "iterdir", scan), self.assertRaisesRegex(
            self.checker.PhysicalDrainError, "stopped_cgroup_present"
        ):
            self.observe()
        self.assertEqual(len(self.samples), 2)

    def test_missing_task_membership_in_live_task_and_task_reuse_are_not_retried(self):
        member = self.proc / "42/task/42"
        original = self.checker._kernel_read
        for reuse in (False, True):
            def read(path):
                if path == member / "cgroup":
                    if not reuse:
                        raise FileNotFoundError("live task membership missing")
                    (member / "stat").write_text("42 (new) S " + "0 " * 18 + "999\n")
                return original(path)

            with patch.object(self.checker, "_kernel_read", read), self.assertRaises(
                self.checker.PhysicalDrainError
            ) as caught:
                self.observe()
            if reuse:
                self.assertEqual(str(caught.exception), "quiescence_process_identity_changed")
            else:
                self.assertIsInstance(caught.exception.__cause__, FileNotFoundError)
            self.assertEqual(len(self.samples), 1)


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
        (directory / "stat").write_text(f"{pid} (fixture) S " + "0 " * 18 + "100\n")
        (thread / "stat").write_text(f"{tid or pid} (fixture) S " + "0 " * 18 + "100\n")

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
