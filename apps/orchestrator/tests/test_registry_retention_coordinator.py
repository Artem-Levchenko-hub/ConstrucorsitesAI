"""Registry cleanup uses real policy/maintenance with only external boundaries faked."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
from collections.abc import Callable
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest

from tests.test_registry_maintenance import OWNER, READ_ONLY, FakeApi, env
from tests.test_registry_retention import BASE, NOW, PASSWORD, REPO, Registry, image
from yleum_orchestrator.services.build_retention_inventory import ProtectedBuildRoots
from yleum_orchestrator.services.registry_maintenance import RegistryMaintenance, recover
from yleum_orchestrator.services.registry_retention import RegistryRetention

TOKEN = "do-not-report-controller-token"


def module() -> Any:
    try:
        return importlib.import_module("yleum_orchestrator.services.registry_retention_coordinator")
    except ModuleNotFoundError:
        pytest.fail("registry cleanup coordinator is not implemented")


def roots() -> ProtectedBuildRoots:
    return ProtectedBuildRoots(
        archive_names=frozenset(),
        image_ids=frozenset(),
        image_refs=frozenset(),
        busy_workspaces=frozenset(),
        active_operations=0,
        observed_at=NOW.isoformat(),
        generation="a" * 64,
    )


def peer_payload(nonce: str) -> dict[str, Any]:
    return {
        "version": 1,
        "archive_names": [],
        "image_ids": [],
        "image_refs": [],
        "busy_workspaces": [],
        "active_operations": 0,
        "observed_at": NOW.isoformat(),
        "generation": "b" * 64,
        "nonce": nonce,
    }


class Kube:
    def __init__(self, api: FakeApi) -> None:
        self.api = api
        self.images: frozenset[str] = frozenset({"registry:2"})
        self.generation = "c" * 64
        self.calls = 0
        self.waited = False
        self.wait_error = False
        self.change: Callable[[], None] | None = None

    def snapshot(self) -> Any:
        self.calls += 1
        if self.change:
            self.change()
        return module().KubernetesRoots(self.images, self.generation)

    def registry_uid(self) -> str:
        value = self.api.deployment["metadata"]["uid"]
        assert isinstance(value, str)
        return value

    async def wait_restored_ready(self, uid: str) -> None:
        # This yield makes a missing await observable.
        await asyncio.sleep(0)
        assert uid == self.api.deployment["metadata"]["uid"]
        assert OWNER not in self.api.deployment["metadata"].get("annotations", {})
        assert READ_ONLY not in env(self.api.deployment)
        self.waited = True
        if self.wait_error:
            raise RuntimeError("readiness transport contains " + PASSWORD)


class Harness:
    def __init__(self, tmp_path: Path) -> None:
        mod = module()
        self.state = tmp_path / "state"
        self.journal = tmp_path / "private" / "registry"
        self.state.mkdir()
        self.journal.mkdir(parents=True, mode=0o700)
        self.controllers = tmp_path / "controllers.json"
        self.controller_data: dict[str, Any] = {
            "version": 1,
            "controllers": [
                {"name": "core", "url": "local"},
                {"name": "commerce", "url": "http://peer.test:8003"},
            ],
        }
        self.write_controllers()
        self.config = mod.RetentionConfig(
            state_root=self.state,
            journal_root=self.journal,
            controllers_file=self.controllers,
            registry_url=BASE,
            registry_username="retention",
            registry_password=PASSWORD,
            internal_token=TOKEN,
            kubeconfig_path="unused",
        )
        self.registry = Registry()
        self.api = FakeApi()
        self.api.journal = self.journal / "registry-maintenance.json"
        self.kube = Kube(self.api)
        self.local = roots()
        self.peer: Callable[[httpx.Request], httpx.Response] = self.default_peer
        self.client = httpx.Client(transport=httpx.MockTransport(self.request))
        self.policy = RegistryRetention(
            BASE,
            "retention",
            PASSWORD,
            self.journal / "registry-seen.json",
            client=self.client,
        )
        self.now = NOW
        self.deletion_error = False
        original_exec = self.api.exec

        def execute(*args: Any, **kwargs: Any) -> tuple[int, str]:
            if args[3][0] == "nc":
                if self.deletion_error:
                    raise RuntimeError("deletion contains " + PASSWORD)
                request = kwargs["stdin"]
                digest = request.split("/manifests/", 1)[1].split(" ", 1)[0]
                assert "Authorization: Basic " in request
                self.registry.delete(REPO, digest)
            return original_exec(*args, **kwargs)

        self.api.exec = execute  # type: ignore[method-assign]
        self.dependencies = mod.CoordinatorDependencies(
            local_roots=lambda _path: self.local,
            http_client=self.client,
            kubernetes=self.kube,
            retention_factory=lambda _path: self.policy,
            maintenance_factory=lambda path: RegistryMaintenance("unused", path, api=self.api),
            recover=lambda path: recover("unused", path, api=self.api),
            now=lambda: self.now,
        )

    def write_controllers(self) -> None:
        self.controllers.write_text(json.dumps(self.controller_data), encoding="utf-8")

    def default_peer(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Internal-Token"] == TOKEN
        assert request.url.path == "/internal/build-retention/roots"
        return httpx.Response(200, json=peer_payload(request.url.params["nonce"]))

    def request(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "peer.test":
            return self.peer(request)
        return self.registry.request(request)

    def age(self) -> None:
        self.policy.plan(set(), self.now - timedelta(seconds=7200))

    async def run(self, **options: Any) -> dict[str, Any]:
        config = replace(self.config, **options)
        return cast(
            dict[str, Any],
            await module().run_retention(config, dependencies=self.dependencies),
        )


async def test_dry_run_observes_first_seen_without_registry_rollout(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    first = await h.run()
    assert first["status"] == "dry_run" and first["candidates"] == 0
    assert (h.journal / "registry-seen.json").is_file()
    assert not (h.journal / "registry-pending-gc.json").exists()
    h.now += timedelta(seconds=7200)
    h.local = replace(h.local, observed_at=h.now.isoformat())
    h.peer = lambda request: httpx.Response(
        200,
        json={**peer_payload(request.url.params["nonce"]), "observed_at": h.now.isoformat()},
    )
    second = await h.run()
    assert second["candidates"] == 1 and second["deleted"] == 0
    assert not h.api.patches and h.registry.deletions == []


async def test_complete_apply_restores_and_awaits_original_registry_readiness(
    tmp_path: Path,
) -> None:
    h = Harness(tmp_path)
    h.age()
    report = await h.run(apply=True)
    assert report["status"] == "applied" and report["deleted"] == 1
    assert report["bytes_freed"] == 75 * 1024
    assert h.api.gc_runs == 1 and h.api.writer is None and h.kube.waited
    assert h.api.journal is not None and not h.api.journal.exists()
    receipts = json.loads((h.journal / "registry-receipts.json").read_text())
    assert receipts["deleted"] == [{"repository": REPO, "digest": image().digest}]
    assert h.registry.repositories[REPO] == {}
    for path in h.journal.glob("*.json"):
        assert PASSWORD not in path.read_text() and TOKEN not in path.read_text()
    assert PASSWORD not in repr(h.config) and TOKEN not in repr(h.config)


@pytest.mark.parametrize("source", ["local_id", "local_ref", "peer", "kubernetes"])
async def test_all_current_image_sources_protect_manifests(tmp_path: Path, source: str) -> None:
    h = Harness(tmp_path)
    h.age()
    reference = f"{BASE.removeprefix('https://')}/{REPO}:old"
    if source == "local_id":
        h.local = replace(h.local, image_ids=frozenset({image().digest}))
    elif source == "local_ref":
        h.local = replace(h.local, image_refs=frozenset({reference}))
    elif source == "peer":
        h.peer = lambda request: httpx.Response(
            200,
            json={**peer_payload(request.url.params["nonce"]), "image_refs": [reference]},
        )
    else:
        h.kube.images = frozenset({reference})
    report = await h.run(apply=True)
    assert report["status"] == "no_candidates"
    assert not h.api.patches and not h.registry.deletions


@pytest.mark.parametrize(
    "problem",
    [
        "unreachable",
        "http_error",
        "redirect",
        "nonce",
        "stale",
        "future",
        "version",
        "unknown_field",
        "missing_field",
        "duplicate_ref",
        "unsorted",
        "image_id",
        "generation",
        "active_bool",
        "active",
        "busy",
        "bad_workspace",
        "invalid_json",
    ],
)
async def test_incomplete_untrusted_or_busy_peer_cannot_start_maintenance(
    tmp_path: Path,
    problem: str,
) -> None:
    h = Harness(tmp_path)
    h.age()

    def peer(request: httpx.Request) -> httpx.Response:
        if problem == "unreachable":
            raise httpx.ConnectError("contains " + TOKEN, request=request)
        if problem == "http_error":
            return httpx.Response(503)
        if problem == "redirect":
            return httpx.Response(302, headers={"Location": "https://untrusted.test/"})
        if problem == "invalid_json":
            return httpx.Response(200, content=b'{"version":1,"version":1}')
        payload = peer_payload(request.url.params["nonce"])
        alterations: dict[str, Any] = {
            "nonce": {"nonce": "replayed"},
            "stale": {"observed_at": (NOW - timedelta(minutes=5)).isoformat()},
            "future": {"observed_at": (NOW + timedelta(minutes=5)).isoformat()},
            "version": {"version": 2},
            "unknown_field": {"secret": TOKEN},
            "duplicate_ref": {"image_ids": ["sha256:" + "a" * 64, "sha256:" + "a" * 64]},
            "unsorted": {"image_ids": ["sha256:" + "b" * 64, "sha256:" + "a" * 64]},
            "image_id": {"image_ids": ["not-an-image"]},
            "generation": {"generation": "invalid"},
            "active_bool": {"active_operations": False},
            "active": {"active_operations": 1},
            "busy": {"busy_workspaces": ["12345678-1234-4234-8234-123456789abc"]},
            "bad_workspace": {"busy_workspaces": ["../state"]},
        }
        if problem == "missing_field":
            del payload["image_refs"]
        else:
            payload.update(alterations[problem])
        return httpx.Response(200, json=payload)

    h.peer = peer
    report = await h.run(apply=True)
    assert report["status"] in {"skipped", "failed"}
    assert not h.api.patches and not h.registry.deletions and h.api.gc_runs == 0
    assert PASSWORD not in json.dumps(report) and TOKEN not in json.dumps(report)


@pytest.mark.parametrize("source", ["local", "peer", "kubernetes", "busy"])
async def test_changed_inventory_after_read_only_restores_without_deletion(
    tmp_path: Path,
    source: str,
) -> None:
    h = Harness(tmp_path)
    h.age()
    if source in {"local", "busy"}:

        def collect(_path: Path) -> ProtectedBuildRoots:
            if OWNER in h.api.deployment["metadata"].get("annotations", {}):
                return replace(
                    h.local,
                    **({"generation": "d" * 64} if source == "local" else {"active_operations": 1}),
                )
            return h.local

        h.dependencies = replace(h.dependencies, local_roots=collect)
    elif source == "peer":
        h.peer = lambda request: httpx.Response(
            200,
            json={
                **peer_payload(request.url.params["nonce"]),
                "generation": "d" * 64 if h.api.patches else "b" * 64,
            },
        )
    else:
        h.kube.change = lambda: setattr(
            h.kube, "generation", "d" * 64 if h.api.patches else "c" * 64
        )
    report = await h.run(apply=True)
    assert report["status"] == "skipped"
    assert h.api.patches and h.kube.waited
    assert not h.registry.deletions and h.api.gc_runs == 0


async def test_change_before_gc_stops_after_receipt_without_gc(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.age()
    h.kube.change = lambda: setattr(
        h.kube,
        "generation",
        "d" * 64 if h.registry.deletions else "c" * 64,
    )
    report = await h.run(apply=True)
    assert report["status"] == "skipped" and report["deleted"] == 1
    assert h.api.writer is None and h.api.gc_runs == 0 and h.kube.waited
    assert json.loads((h.journal / "registry-receipts.json").read_text())["deleted"]


@pytest.mark.parametrize("failure", ["lost_202", "before_gc", "gc_failed"])
async def test_pending_gc_survives_the_last_manifest_and_resumes_without_new_candidates(
    tmp_path: Path,
    failure: str,
) -> None:
    h = Harness(tmp_path)
    h.age()
    pending = h.journal / "registry-pending-gc.json"
    original_exec = h.api.exec

    def execute(*args: Any, **kwargs: Any) -> tuple[int, str]:
        if args[3][0] == "nc":
            assert pending.is_file(), "durable GC intent must precede the actual DELETE"
        result = original_exec(*args, **kwargs)
        if args[3][0] == "nc" and failure == "lost_202":
            raise RuntimeError("DELETE applied but response was lost")
        return result

    h.api.exec = execute  # type: ignore[method-assign]
    if failure == "before_gc":
        h.kube.change = lambda: setattr(
            h.kube,
            "generation",
            "d" * 64 if h.registry.deletions else "c" * 64,
        )
    elif failure == "gc_failed":
        h.api.fail_action = "gc"
    first = await h.run(apply=True)
    assert first["status"] in {"failed", "skipped"}
    assert h.registry.repositories[REPO] == {} and pending.is_file()
    assert first["pending_gc"] is True and h.api.gc_runs == 0
    saved = pending.read_bytes()
    receipts_path = h.journal / "registry-receipts.json"
    saved_receipts = receipts_path.read_bytes()

    # Observation, standalone restoration and busy refusal cannot consume intent.
    h.kube.change = None
    h.kube.generation = "c" * 64
    h.api.fail_action = None
    observed = await h.run()
    assert observed["status"] == "dry_run" and observed["pending_gc"] is True
    assert observed["candidates"] == 0 and pending.read_bytes() == saved
    interrupted_lease = h.dependencies.maintenance_factory(h.api.journal)
    interrupted_lease.__enter__()
    interrupted_lease.start_private_writer()
    recovered = await h.run(recover_only=True)
    assert recovered["status"] == "recovered" and recovered["pending_gc"] is True
    assert pending.read_bytes() == saved and h.api.gc_runs == 0 and h.api.writer is None
    h.local = replace(h.local, active_operations=1)
    busy = await h.run(apply=True)
    assert busy["status"] == "skipped" and busy["pending_gc"] is True
    assert pending.read_bytes() == saved and h.api.gc_runs == 0
    h.local = replace(h.local, active_operations=0)

    starts = sum(command[-2] == "start" for command, _ in h.api.commands)
    completed = await h.run(apply=True)
    assert completed["status"] == "applied"
    assert completed["candidates"] == completed["deleted"] == 0
    assert completed["pending_gc"] is False and not pending.exists()
    assert h.api.gc_runs == 1 and h.api.writer is None and h.kube.waited
    assert completed["bytes_freed"] == 75 * 1024
    assert sum(command[-2] == "start" for command, _ in h.api.commands) == starts
    assert receipts_path.read_bytes() == saved_receipts


async def test_pending_gc_retry_appends_receipts_and_reports_only_new_deletions(
    tmp_path: Path,
) -> None:
    h = Harness(tmp_path)
    h.registry.repositories[REPO]["other"] = image(marker="other-generation")
    h.age()
    h.kube.change = lambda: setattr(
        h.kube,
        "generation",
        "d" * 64 if h.registry.deletions else "c" * 64,
    )
    interrupted = await h.run(apply=True)
    assert interrupted["status"] == "skipped" and interrupted["deleted"] == 1
    assert len(h.registry.repositories[REPO]) == 1
    h.kube.change = None
    h.kube.generation = "c" * 64
    retried = await h.run(apply=True)
    assert retried["status"] == "applied" and retried["deleted"] == 1
    receipts = json.loads((h.journal / "registry-receipts.json").read_text())
    assert len(receipts["deleted"]) == 2
    assert {item["digest"] for item in receipts["deleted"]} == {
        image().digest,
        image(marker="other-generation").digest,
    }


async def test_pending_gc_resume_rechecks_roots_after_read_only(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.age()
    h.api.fail_action = "gc"
    assert (await h.run(apply=True))["status"] == "failed"
    pending = h.journal / "registry-pending-gc.json"
    assert pending.is_file()
    original = pending.read_bytes()
    h.api.fail_action = None

    def roots_after_fence(_path: Path) -> ProtectedBuildRoots:
        if OWNER in h.api.deployment["metadata"].get("annotations", {}):
            return replace(h.local, generation="d" * 64)
        return h.local

    h.dependencies = replace(h.dependencies, local_roots=roots_after_fence)
    report = await h.run(apply=True)
    assert report["status"] == "skipped" and report["candidates"] == 0
    assert report["pending_gc"] is True and pending.read_bytes() == original
    assert h.api.gc_runs == 0 and h.api.writer is None and h.kube.waited


async def test_report_cannot_overwrite_pending_gc_intent(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.age()
    h.api.fail_action = "gc"
    await h.run(apply=True)
    pending = h.journal / "registry-pending-gc.json"
    assert pending.is_file()
    original = pending.read_bytes()
    report = await h.run(report=pending)
    assert report["status"] == "failed" and pending.read_bytes() == original


async def test_changed_pending_gc_identity_never_authorizes_gc(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.age()
    h.api.fail_action = "gc"
    await h.run(apply=True)
    pending = h.journal / "registry-pending-gc.json"
    assert pending.is_file()
    payload = json.loads(pending.read_text())
    payload["deployment_uid"] = "different-registry"
    pending.write_text(json.dumps(payload))
    h.api.fail_action = None
    report = await h.run(apply=True)
    assert report["status"] == "failed" and report["pending_gc"] is True
    assert pending.is_file() and h.api.gc_runs == 0


async def test_delete_failure_never_gcs_and_restores_owned_flags(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.age()
    h.deletion_error = True
    report = await h.run(apply=True)
    assert report["status"] == "failed"
    assert h.api.gc_runs == 0 and h.api.writer is None and h.kube.waited
    assert PASSWORD not in json.dumps(report)


async def test_restore_readiness_failure_is_reported_as_failure(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.age()
    h.kube.wait_error = True
    report = await h.run(apply=True)
    assert report["status"] == "failed" and report["code"] == "restore_readiness_failed"


async def test_changed_controller_coverage_after_fence_aborts_cleanup(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.age()

    def change() -> None:
        if h.api.patches:
            h.controller_data["controllers"].append({"name": "new", "url": "http://new.test"})
            h.write_controllers()

    h.kube.change = change
    report = await h.run(apply=True)
    assert report["status"] == "skipped" and not h.registry.deletions and h.api.gc_runs == 0
    assert h.kube.waited


async def test_recover_only_holds_lock_and_awaits_restoration(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    lease = h.dependencies.maintenance_factory(h.api.journal)
    lease.__enter__()
    lease.start_private_writer()
    report = await h.run(recover_only=True)
    assert report["status"] == "recovered"
    assert h.kube.waited and h.api.writer is None and h.api.gc_runs == 0
    assert h.api.journal is not None and not h.api.journal.exists()


async def test_recover_does_not_depend_on_complete_controller_configuration(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    lease = h.dependencies.maintenance_factory(h.api.journal)
    lease.__enter__()
    h.controllers.unlink()
    report = await h.run(recover_only=True)
    assert report["status"] == "recovered" and h.kube.waited
    assert h.api.journal is not None and not h.api.journal.exists()


async def test_recover_without_journal_still_waits_for_the_asynchronous_rollout(
    tmp_path: Path,
) -> None:
    h = Harness(tmp_path)
    report = await h.run(recover_only=True)
    assert report["status"] == "no_recovery" and h.kube.waited


@pytest.mark.parametrize(
    "problem",
    ["under_state", "config_alias", "duplicate_name", "duplicate_url", "few", "two_local"],
)
async def test_unsafe_paths_and_incomplete_controller_configuration_refuse_work(
    tmp_path: Path,
    problem: str,
) -> None:
    h = Harness(tmp_path)
    options: dict[str, Any] = {"apply": True}
    controllers = h.controller_data["controllers"]
    if problem == "under_state":
        options["journal_root"] = h.state
    elif problem == "config_alias":
        options["report"] = h.controllers
    elif problem == "duplicate_name":
        controllers[1]["name"] = "core"
    elif problem == "duplicate_url":
        controllers[1]["url"] = "local"
    elif problem == "few":
        controllers.pop()
    else:
        controllers.append({"name": "extra", "url": "local"})
    h.write_controllers()
    report = await h.run(**options)
    assert report["status"] == "failed"
    assert not h.api.patches and not h.registry.requests
    assert json.loads(h.controllers.read_text()) == h.controller_data


async def test_peer_token_file_overrides_default_without_leaking(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    token = tmp_path / "peer.token"
    token.write_text("different-private-peer-token\n", encoding="utf-8")
    token.chmod(0o600)
    h.controller_data["controllers"][1]["token_file"] = str(token)
    h.write_controllers()

    def peer(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Internal-Token"] == "different-private-peer-token"
        return httpx.Response(200, json=peer_payload(request.url.params["nonce"]))

    h.peer = peer
    report = await h.run()
    assert report["status"] == "dry_run"
    assert "different-private-peer-token" not in json.dumps(report)


async def test_report_cannot_overwrite_peer_token_file(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    token = tmp_path / "peer.token"
    token.write_text(TOKEN, encoding="utf-8")
    h.controller_data["controllers"][1]["token_file"] = str(token)
    h.write_controllers()
    report = await h.run(report=token)
    assert report["status"] == "failed" and token.read_text() == TOKEN


async def test_busy_local_state_never_means_empty_inventory(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.local = replace(h.local, active_operations=1)
    report = await h.run(apply=True)
    assert report["status"] == "skipped" and report["code"] == "controller_busy"
    assert not h.registry.requests and not h.api.patches


async def test_kubernetes_list_error_cannot_start_maintenance(tmp_path: Path) -> None:
    h = Harness(tmp_path)

    def fail() -> Any:
        raise RuntimeError("list denied with " + TOKEN)

    h.kube.snapshot = fail  # type: ignore[method-assign]
    report = await h.run(apply=True)
    assert report["status"] == "skipped" and report["code"] == "kubernetes_unavailable"
    assert not h.registry.requests and not h.api.patches


async def test_lifetime_lock_is_held_until_asynchronous_restoration_completes(
    tmp_path: Path,
) -> None:
    from yleum_orchestrator.services.cell_lock import WorkspaceOperationLock

    h = Harness(tmp_path)
    h.age()
    h.dependencies = replace(
        h.dependencies,
        lock=WorkspaceOperationLock(
            h.journal,
            acquire_timeout_seconds=0.1,
            retry_interval_seconds=0.01,
        ),
    )
    ready = asyncio.Event()
    proceed = asyncio.Event()
    original_wait = h.kube.wait_restored_ready

    async def wait(uid: str) -> None:
        ready.set()
        await proceed.wait()
        await original_wait(uid)

    h.kube.wait_restored_ready = wait  # type: ignore[method-assign]
    running = asyncio.create_task(h.run(apply=True))
    await asyncio.wait_for(ready.wait(), timeout=5)
    refused = await h.run(recover_only=True)
    proceed.set()
    first = await running
    assert refused["code"] == "registry_lock_unavailable"
    assert first["status"] == "applied" and h.kube.waited


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink fixture")
@pytest.mark.parametrize("target", ["state", "journal", "configuration", "report", "token"])
async def test_symlink_paths_are_never_followed(tmp_path: Path, target: str) -> None:
    h = Harness(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_text(TOKEN)
    options: dict[str, Any] = {"apply": True}
    if target in {"state", "journal"}:
        link = tmp_path / "linked-directory"
        link.symlink_to(outside, target_is_directory=True)
        options["state_root" if target == "state" else "journal_root"] = link
    elif target == "configuration":
        h.controllers.unlink()
        h.controllers.symlink_to(sentinel)
    elif target == "report":
        report_link = h.journal / "registry-report.json"
        report_link.symlink_to(sentinel)
    else:
        token = tmp_path / "linked.token"
        token.symlink_to(sentinel)
        h.controller_data["controllers"][1]["token_file"] = str(token)
        h.write_controllers()
    report = await h.run(**options)
    assert report["status"] == "failed"
    assert sentinel.read_text() == TOKEN and not h.api.patches


def test_cli_defaults_to_dry_run_outside_live_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from pydantic import SecretStr

    script_path = Path(__file__).parents[1] / "scripts" / "cleanup_registry.py"
    if not script_path.exists():
        pytest.fail("registry cleanup CLI is not implemented")
    spec = importlib.util.spec_from_file_location("cleanup_registry_cli_test", script_path)
    assert spec is not None and spec.loader is not None
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    settings = SimpleNamespace(
        cell_state_path=str(tmp_path / "state" / "project-cells.json"),
        image_registry="registry.example.test",
        image_registry_username="retention",
        image_registry_password=SecretStr(PASSWORD),
        internal_token=SecretStr(TOKEN),
        k8s_kubeconfig_path="/private/kubeconfig",
        k8s_context="runtime",
    )
    seen: list[Any] = []

    async def run(config: Any) -> dict[str, Any]:
        seen.append(config)
        return {"version": 1, "status": "dry_run", "code": "ok", "deleted": 0}

    monkeypatch.setattr(script, "get_settings", lambda: settings)
    monkeypatch.setattr(script, "run_retention", run)
    assert script.main([]) == 0
    config = seen[0]
    assert config.state_root == tmp_path / "state" and not config.apply
    assert config.journal_root == Path("/opt/omnia-runtime/retention/registry")
    assert config.controllers_file == Path("/opt/omnia-runtime/retention/controllers.json")
    assert config.expected_controller_count == 2 and config.kube_context == "runtime"
    assert config.registry_url == "https://registry.example.test"
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "dry_run" and PASSWORD not in json.dumps(output)
    assert (
        script.main(
            [
                "--apply",
                "--state-root",
                str(tmp_path),
                "--expected-controller-count",
                "3",
                "--report",
                str(tmp_path / "report"),
            ]
        )
        == 0
    )
    assert seen[-1].apply and seen[-1].expected_controller_count == 3
    with pytest.raises(SystemExit):
        script.main(["--apply", "--recover-only"])


def resource(kind: str, name: str, images: list[str], **metadata: Any) -> dict[str, Any]:
    spec = {
        "containers": [
            {"name": f"container-{i}", "image": value} for i, value in enumerate(images)
        ],
        "initContainers": [{"name": "init", "image": "init.example/tool:v1"}],
    }
    body: dict[str, Any]
    if kind == "Pod":
        body = {"spec": spec}
    elif kind == "PodTemplate":
        body = {"template": {"spec": spec}}
    elif kind == "CronJob":
        body = {"spec": {"jobTemplate": {"spec": {"template": {"spec": spec}}}}}
    else:
        body = {"spec": {"replicas": 0, "template": {"spec": spec}}}
    return {
        "kind": kind,
        "metadata": {
            "name": name,
            "namespace": "apps",
            "uid": name,
            "resourceVersion": "1",
            "generation": 1,
            **metadata,
        },
        **body,
    }


def test_kubernetes_inventory_includes_zero_replicas_jobs_init_and_ephemeral_containers() -> None:
    resources = [
        resource(kind, kind.lower(), [f"example.test/{kind.lower()}:current"])
        for kind in [
            "Pod",
            "Deployment",
            "StatefulSet",
            "DaemonSet",
            "ReplicaSet",
            "ReplicationController",
            "PodTemplate",
            "Job",
            "CronJob",
        ]
    ]
    resources[0]["spec"]["ephemeralContainers"] = [{"name": "debug", "image": "debug:v1"}]
    snapshot = module().kubernetes_roots(resources)
    assert snapshot.image_refs == frozenset(
        {
            "init.example/tool:v1",
            "debug:v1",
            "example.test/pod:current",
            "example.test/deployment:current",
            "example.test/statefulset:current",
            "example.test/daemonset:current",
            "example.test/replicaset:current",
            "example.test/replicationcontroller:current",
            "example.test/podtemplate:current",
            "example.test/job:current",
            "example.test/cronjob:current",
        }
    )
    changed = deepcopy(resources)
    changed[1]["metadata"]["resourceVersion"] = "2"
    assert module().kubernetes_roots(changed).generation != snapshot.generation


def test_only_owned_registry_rollout_identities_are_ignored_but_images_stay_protected() -> None:
    deployment = resource("Deployment", "registry", ["registry:2"], namespace="registry")
    replica = resource(
        "ReplicaSet",
        "registry-ro",
        ["registry:2"],
        namespace="registry",
        ownerReferences=[{"kind": "Deployment", "uid": "registry", "controller": True}],
    )
    pod = resource(
        "Pod",
        "registry-ro-pod",
        ["registry:2"],
        namespace="registry",
        ownerReferences=[{"kind": "ReplicaSet", "uid": "registry-ro", "controller": True}],
    )
    before = module().kubernetes_roots([deployment])
    after = module().kubernetes_roots([deployment, replica, pod])
    assert after == before and "registry:2" in after.image_refs
    alien = resource("Pod", "other", ["registry:2"], namespace="registry")
    assert module().kubernetes_roots([deployment, alien]).generation != before.generation


@pytest.mark.parametrize("problem", ["missing_spec", "bad_containers", "missing_image", "bad_kind"])
def test_malformed_kubernetes_workload_never_means_empty_roots(problem: str) -> None:
    value = resource("Pod", "pod", ["example.test/app:v1"])
    if problem == "missing_spec":
        del value["spec"]
    elif problem == "bad_containers":
        value["spec"]["containers"] = None
    elif problem == "missing_image":
        del value["spec"]["containers"][0]["image"]
    else:
        value["kind"] = "Unknown"
    with pytest.raises(module().CoordinatorError):
        module().kubernetes_roots([value])


def test_official_kubernetes_adapter_lists_every_kind_and_follows_every_page(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kubernetes import client, config

    connection = client.ApiClient()
    captured: dict[str, Any] = {}

    def load(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return connection

    monkeypatch.setattr(config, "new_client_from_config", load)
    kubeconfig = tmp_path / "kubeconfig"
    kubeconfig.write_text("test fixture")
    adapter = module().KubernetesWorkloads(str(kubeconfig), context="runtime")
    calls: list[tuple[str, str | None]] = []
    methods = [
        (adapter._core, "list_pod_for_all_namespaces", "Pod"),
        (adapter._core, "list_pod_template_for_all_namespaces", "PodTemplate"),
        (adapter._core, "list_replication_controller_for_all_namespaces", "ReplicationController"),
        (adapter._apps, "list_deployment_for_all_namespaces", "Deployment"),
        (adapter._apps, "list_stateful_set_for_all_namespaces", "StatefulSet"),
        (adapter._apps, "list_daemon_set_for_all_namespaces", "DaemonSet"),
        (adapter._apps, "list_replica_set_for_all_namespaces", "ReplicaSet"),
        (adapter._batch, "list_job_for_all_namespaces", "Job"),
        (adapter._batch, "list_cron_job_for_all_namespaces", "CronJob"),
    ]

    def paged(kind: str) -> Callable[..., Any]:
        def page(**kwargs: Any) -> Any:
            continuation = kwargs["_continue"]
            calls.append((kind, continuation))
            assert kwargs["limit"] == 1000 and kwargs["_request_timeout"] > 0
            return {
                "metadata": {
                    "resourceVersion": "99",
                    "continue": ("next-pod" if kind == "Pod" and not continuation else ""),
                },
                "items": [
                    resource(kind, kind.lower() + (continuation or ""), [kind.lower() + ":v1"])
                ],
            }

        return page

    for api, method, kind in methods:
        assert hasattr(api, method), "the installed official client must support this method"
        monkeypatch.setattr(api, method, paged(kind))
    try:
        snapshot = adapter.snapshot()
    finally:
        adapter.close()
    assert captured == {"config_file": str(kubeconfig), "context": "runtime"}
    assert len(calls) == 10 and ("Pod", "next-pod") in calls
    assert {kind.lower() + ":v1" for _, _, kind in methods} <= snapshot.image_refs


@pytest.mark.parametrize(
    "problem", ["loop", "changed_version", "missing_items", "missing_metadata"]
)
def test_kubernetes_pagination_refuses_incomplete_lists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    problem: str,
) -> None:
    from kubernetes import client, config

    connection = client.ApiClient()
    monkeypatch.setattr(config, "new_client_from_config", lambda **_: connection)
    kubeconfig = tmp_path / "kubeconfig"
    kubeconfig.write_text("test fixture")
    adapter = module().KubernetesWorkloads(str(kubeconfig))
    count = 0

    def pages(**kwargs: Any) -> dict[str, Any]:
        nonlocal count
        count += 1
        payload: dict[str, Any] = {
            "metadata": {"resourceVersion": "2" if count > 1 else "1", "continue": "next"},
            "items": [],
        }
        if problem == "loop":
            payload["metadata"]["resourceVersion"] = "1"
        elif problem == "missing_items":
            del payload["items"]
        elif problem == "missing_metadata":
            del payload["metadata"]
        return payload

    try:
        with pytest.raises(module().CoordinatorError):
            adapter._list(pages, "Pod")
    finally:
        adapter.close()


def test_restored_readiness_wait_requires_observed_generation_and_original_uid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kubernetes import client, config

    connection = client.ApiClient()
    monkeypatch.setattr(config, "new_client_from_config", lambda **_: connection)
    kubeconfig = tmp_path / "kubeconfig"
    kubeconfig.write_text("test fixture")
    now = 0.0
    api = FakeApi()
    api.deployment["metadata"]["generation"] = 4
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        nonlocal now
        now += seconds
        sleeps.append(seconds)
        api.deployment["status"]["observedGeneration"] = 4

    adapter = module().KubernetesWorkloads(
        str(kubeconfig),
        clock=lambda: now,
        sleep=sleep,
        timeout_seconds=2,
    )
    monkeypatch.setattr(
        adapter._apps, "read_namespaced_deployment", lambda *_, **__: api.deployment
    )
    try:
        asyncio.run(adapter.wait_restored_ready("deployment-1"))
        assert sleeps == [1]
        api.deployment["metadata"]["uid"] = "replaced"
        with pytest.raises(module().CoordinatorError, match="restore_readiness_failed"):
            asyncio.run(adapter.wait_restored_ready("deployment-1"))
    finally:
        adapter.close()
