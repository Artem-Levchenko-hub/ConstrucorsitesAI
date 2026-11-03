"""The real collector against an in-memory Docker boundary and real workspace locks."""

from __future__ import annotations

import importlib
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from uuid import UUID

import docker.errors
import pytest

from yleum_orchestrator.services.cell_lock import WorkspaceOperationLock

PROJECT = "12345678-1234-4234-8234-123456789abc"
OWNER = "22345678-1234-4234-8234-123456789abc"
WORKSPACE = "32345678-1234-4234-8234-123456789abc"
IMAGE = "sha256:" + "1" * 64
OTHER_IMAGE = "sha256:" + "2" * 64
MANIFEST = "sha256:" + "3" * 64
REPOSITORY = f"registry.yleum.ru/max-app/{PROJECT}"
NOW = datetime(2026, 11, 3, 12, tzinfo=UTC)
SECRET = "credentials-must-not-escape-in-errors"


def module() -> ModuleType:
    return importlib.import_module("yleum_orchestrator.services.local_image_retention")


def labels(prefix: str = "omnia") -> dict[str, str]:
    return {
        f"{prefix}.managed": "true",
        f"{prefix}.project_id": PROJECT,
        f"{prefix}.owner_id": OWNER,
        f"{prefix}.workspace_id": WORKSPACE,
        f"{prefix}.resource_kind": "environment",
    }


def image(*, tags: list[str] | None = None, image_id: str = IMAGE) -> dict[str, Any]:
    return {
        "Id": image_id,
        "RepoTags": tags or [],
        "RepoDigests": [],
        "Created": "2026-11-03T09:00:00.000000000Z",
        "Config": {"Labels": labels()},
    }


class Images:
    def __init__(self, daemon: Docker) -> None:
        self.daemon = daemon

    def list(self, *, all: bool = False) -> list[SimpleNamespace]:
        assert all is True, "intermediate and dangling images must also be inventoried"
        if self.daemon.image_error is not None:
            raise self.daemon.image_error
        return [SimpleNamespace(id=value) for value in self.daemon.records]

    def get(self, reference: str) -> SimpleNamespace:
        if self.daemon.inspect_error is not None:
            raise self.daemon.inspect_error
        for key, attrs in self.daemon.records.items():
            if reference == key or reference in (attrs.get("RepoTags") or []):
                return SimpleNamespace(id=attrs.get("Id"), attrs=deepcopy(attrs))
        raise docker.errors.ImageNotFound("image absent")

    def remove(self, image: str, *, force: bool, noprune: bool) -> None:
        assert force is False and noprune is True
        self.daemon.attempts.append((image, force, noprune))
        if self.daemon.before_remove is not None:
            self.daemon.before_remove(image)
        if self.daemon.refuse:
            raise docker.errors.APIError(SECRET)
        for key, attrs in tuple(self.daemon.records.items()):
            if image == key:
                if any(item.get("ImageID") == key for item in self.daemon.consumers):
                    raise docker.errors.APIError(SECRET)
                del self.daemon.records[key]
                return
            if image in attrs["RepoTags"]:
                attrs["RepoTags"].remove(image)
                if self.daemon.auto_remove_last_tag and not attrs["RepoTags"]:
                    del self.daemon.records[key]
                return
        raise docker.errors.ImageNotFound("image absent")


class Containers:
    def __init__(self, daemon: Docker) -> None:
        self.daemon = daemon

    def list(self, *, all: bool = False) -> list[SimpleNamespace]:
        assert all is True, "stopped containers must also protect their images"
        if self.daemon.consumer_error is not None:
            raise self.daemon.consumer_error
        return [SimpleNamespace(attrs=deepcopy(attrs)) for attrs in self.daemon.consumers]


class Docker:
    def __init__(self, records: dict[str, dict[str, Any]] | None = None) -> None:
        self.records = {IMAGE: image()} if records is None else records
        self.images = Images(self)
        self.containers = Containers(self)
        self.consumers: list[dict[str, Any]] = []
        self.attempts: list[tuple[str, bool, bool]] = []
        self.refuse = False
        self.closed = 0
        self.auto_remove_last_tag = False
        self.before_remove: Callable[[str], None] | None = None
        self.image_error: Exception | None = None
        self.inspect_error: Exception | None = None
        self.consumer_error: Exception | None = None

    def close(self) -> None:
        self.closed += 1


def roots(
    policy: ModuleType,
    *,
    ids: frozenset[str] = frozenset(),
    refs: frozenset[str] = frozenset(),
    busy: frozenset[str] = frozenset(),
) -> Any:
    return policy.ImageRoots(image_ids=ids, image_refs=refs, busy_workspaces=busy)


async def test_default_dry_run_reports_candidate_without_creating_lock_files(
    tmp_path: Path,
) -> None:
    policy = module()
    daemon = Docker()
    root = tmp_path / "state-does-not-exist"

    result = await policy.collect_local_images(root, lambda: roots(policy), client=daemon, now=NOW)

    assert result["scanned"] == 1
    assert result["candidates"] == 1
    assert result["removed"] == 0
    assert daemon.attempts == []
    assert IMAGE in daemon.records
    assert not root.exists()
    assert daemon.closed == 0


async def test_apply_removes_only_exact_aged_image_without_force_or_parent_prune(
    tmp_path: Path,
) -> None:
    policy = module()
    daemon = Docker()

    result = await policy.collect_local_images(
        tmp_path, lambda: roots(policy), apply=True, client=daemon, now=NOW,
    )

    assert result["removed"] == 1
    assert daemon.attempts == [(IMAGE, False, True)]
    assert daemon.records == {}
    assert (tmp_path / "locks" / f"workspace-{WORKSPACE}.lock").is_file()
    assert daemon.closed == 0


@pytest.mark.parametrize(
    ("ids", "refs", "digests"),
    [
        (frozenset({IMAGE}), frozenset(), []),
        (frozenset(), frozenset({IMAGE}), []),
        (frozenset(), frozenset({REPOSITORY + ":current"}), []),
        (frozenset(), frozenset({REPOSITORY + ":alias"}), []),
        (frozenset(), frozenset({REPOSITORY + "@" + MANIFEST}), [REPOSITORY + "@" + MANIFEST]),
        (frozenset({MANIFEST}), frozenset(), [REPOSITORY + "@" + MANIFEST]),
        (frozenset(), frozenset({"elsewhere.test/image@" + MANIFEST}),
         [REPOSITORY + "@" + MANIFEST]),
        (frozenset(), frozenset({REPOSITORY + "@" + IMAGE}), []),
    ],
)
async def test_current_or_paused_metadata_protects_every_alias_and_digest(
    tmp_path: Path, ids: frozenset[str], refs: frozenset[str], digests: list[str],
) -> None:
    policy = module()
    value = image(tags=[REPOSITORY + ":current", REPOSITORY + ":alias"])
    value["RepoDigests"] = digests
    daemon = Docker({IMAGE: value})

    result = await policy.collect_local_images(
        tmp_path, lambda: roots(policy, ids=ids, refs=refs), apply=True, client=daemon, now=NOW,
    )

    assert result["candidates"] == 0
    assert result["preserved"] == 1
    assert daemon.attempts == []
    assert IMAGE in daemon.records


@pytest.mark.parametrize("automatic", [False, True])
async def test_all_application_aliases_are_addressed_and_second_sweep_is_idempotent(
    tmp_path: Path, automatic: bool,
) -> None:
    policy = module()
    daemon = Docker({IMAGE: image(tags=[REPOSITORY + ":a", REPOSITORY + ":b"])})
    daemon.auto_remove_last_tag = automatic

    first = await policy.collect_local_images(
        tmp_path, lambda: roots(policy), apply=True, client=daemon, now=NOW,
    )
    second = await policy.collect_local_images(
        tmp_path, lambda: roots(policy), apply=True, client=daemon, now=NOW,
    )

    expected = [(REPOSITORY + ":a", False, True), (REPOSITORY + ":b", False, True)]
    if not automatic:
        expected.append((IMAGE, False, True))
    assert first["removed"] == 1
    assert second["scanned"] == second["removed"] == 0
    assert daemon.attempts == expected


@pytest.mark.parametrize(
    "tag",
    [
        "node:22", "registry.yleum.ru/platform/base:latest", "other.test/max-app/" + PROJECT + ":a",
        "registry.yleum.ru/max-app/" + OWNER + ":a", REPOSITORY + ":", REPOSITORY,
        REPOSITORY + ":tag with space", "registry.yleum.ru.evil/max-app/" + PROJECT + ":a",
    ],
)
async def test_any_shared_unknown_or_mismatched_tag_preserves_the_whole_image(
    tmp_path: Path, tag: str,
) -> None:
    policy = module()
    daemon = Docker({IMAGE: image(tags=[REPOSITORY + ":old", tag])})

    result = await policy.collect_local_images(
        tmp_path, lambda: roots(policy), apply=True, client=daemon, now=NOW,
    )

    assert result["candidates"] == 0
    assert daemon.attempts == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("Created", "not-a-timestamp"), ("Created", None),
        ("Created", "2026-11-03T10:00:01Z"), ("Created", "2026-11-04T10:00:01Z"),
        ("Created", "2026-11-03T10:00:00.000000001Z"),
        ("Created", "2026-11-03T09:00:00"), ("Config", {}), ("Config", {"Labels": None}),
        ("RepoTags", None), ("RepoTags", "not-a-list"), ("RepoDigests", None),
        ("RepoDigests", ["registry.yleum.ru/platform/base@" + MANIFEST]),
        ("Id", OTHER_IMAGE),
    ],
)
async def test_unknown_identity_time_or_reference_inventory_preserves_image(
    tmp_path: Path, field: str, value: Any,
) -> None:
    policy = module()
    attrs = image()
    attrs[field] = value
    daemon = Docker({IMAGE: attrs})

    result = await policy.collect_local_images(
        tmp_path, lambda: roots(policy), apply=True, client=daemon, now=NOW,
    )

    assert result["candidates"] == 0
    assert daemon.attempts == []


async def test_image_becomes_eligible_at_exact_two_hour_boundary(tmp_path: Path) -> None:
    policy = module()
    value = image()
    value["Created"] = "2026-11-03T10:00:00Z"
    daemon = Docker({IMAGE: value})

    result = await policy.collect_local_images(
        tmp_path, lambda: roots(policy), apply=True, client=daemon, now=NOW,
    )

    assert result["removed"] == 1


@pytest.mark.parametrize("prefix", ["omnia", "yleum"])
async def test_complete_trusted_environment_labels_are_required_for_application_tags(
    tmp_path: Path, prefix: str,
) -> None:
    policy = module()
    value = image(tags=[REPOSITORY + ":old"])
    value["Config"]["Labels"] = labels(prefix)
    daemon = Docker({IMAGE: value})

    result = await policy.collect_local_images(
        tmp_path, lambda: roots(policy), apply=True, client=daemon, now=NOW,
    )

    assert result["removed"] == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"omnia.managed": "false"}, {"omnia.project_id": "bad"},
        {"omnia.owner_id": OWNER.upper()}, {"omnia.workspace_id": ""},
        {"omnia.resource_kind": "workspace"}, {"yleum.workspace_id": OWNER},
    ],
)
async def test_untrusted_or_conflicting_identity_labels_preserve_even_application_tags(
    tmp_path: Path, changes: dict[str, str],
) -> None:
    policy = module()
    value = image(tags=[REPOSITORY + ":old"])
    value["Config"]["Labels"].update(changes)
    daemon = Docker({IMAGE: value})

    result = await policy.collect_local_images(
        tmp_path, lambda: roots(policy), apply=True, client=daemon, now=NOW,
    )

    assert result["candidates"] == 0
    assert daemon.attempts == []


async def test_tag_alone_without_trusted_ownership_never_authorizes_deletion(
    tmp_path: Path,
) -> None:
    policy = module()
    value = image(tags=[REPOSITORY + ":old"])
    value["Config"]["Labels"] = {}
    daemon = Docker({IMAGE: value})

    result = await policy.collect_local_images(
        tmp_path, lambda: roots(policy), apply=True, client=daemon, now=NOW,
    )

    assert result["candidates"] == 0
    assert daemon.attempts == []


@pytest.mark.parametrize("state", ["running", "exited", "created", "paused"])
async def test_any_container_consumer_protects_image(tmp_path: Path, state: str) -> None:
    policy = module()
    daemon = Docker()
    daemon.consumers = [{"Id": "container", "ImageID": IMAGE, "Image": IMAGE, "State": state}]

    result = await policy.collect_local_images(
        tmp_path, lambda: roots(policy), apply=True, client=daemon, now=NOW,
    )

    assert result["candidates"] == 0
    assert daemon.attempts == []


async def test_unknown_consumer_identity_aborts_without_exposing_payload(tmp_path: Path) -> None:
    policy = module()
    daemon = Docker()
    daemon.consumers = [{"Id": SECRET, "Image": SECRET}]

    with pytest.raises(policy.LocalImageRetentionError) as error:
        await policy.collect_local_images(
            tmp_path, lambda: roots(policy), apply=True, client=daemon, now=NOW,
        )

    assert SECRET not in str(error.value)
    assert daemon.attempts == []


@pytest.mark.parametrize("stage", ["initial", "locked"])
async def test_roots_failure_aborts_without_treating_failure_as_empty_or_leaking_secrets(
    tmp_path: Path, stage: str,
) -> None:
    policy = module()
    daemon = Docker()
    calls = 0

    def load() -> Any:
        nonlocal calls
        calls += 1
        if stage == "initial" or calls > 1:
            raise RuntimeError(SECRET)
        return roots(policy)

    with pytest.raises(policy.LocalImageRetentionError) as error:
        await policy.collect_local_images(tmp_path, load, apply=True, client=daemon, now=NOW)

    assert SECRET not in str(error.value)
    assert error.value.__suppress_context__
    assert daemon.attempts == []


@pytest.mark.parametrize("field", ["image_error", "inspect_error", "consumer_error"])
async def test_docker_inventory_failure_aborts_with_sanitized_error(
    tmp_path: Path, field: str,
) -> None:
    policy = module()
    daemon = Docker()
    setattr(daemon, field, RuntimeError(SECRET))

    with pytest.raises(policy.LocalImageRetentionError) as error:
        await policy.collect_local_images(
            tmp_path, lambda: roots(policy), apply=True, client=daemon, now=NOW,
        )

    assert SECRET not in str(error.value)
    assert daemon.attempts == []


@pytest.mark.parametrize("payload", [None, {}, SimpleNamespace(image_ids=frozenset())])
async def test_incomplete_roots_are_rejected(tmp_path: Path, payload: Any) -> None:
    policy = module()
    daemon = Docker()

    with pytest.raises(policy.LocalImageRetentionError):
        await policy.collect_local_images(
            tmp_path, lambda: payload, apply=True, client=daemon, now=NOW,
        )

    assert daemon.attempts == []


@pytest.mark.parametrize("change", ["tag", "label", "created", "id", "consumer", "root", "busy"])
async def test_complete_locked_recheck_preserves_new_reference_consumer_or_changed_identity(
    tmp_path: Path, change: str,
) -> None:
    policy = module()
    daemon = Docker()
    calls = 0

    def load() -> Any:
        nonlocal calls
        calls += 1
        if calls > 1:
            assert (tmp_path / "locks" / f"workspace-{WORKSPACE}.lock").is_file()
            if change == "tag":
                daemon.records[IMAGE]["RepoTags"] = [REPOSITORY + ":new"]
            elif change == "label":
                daemon.records[IMAGE]["Config"]["Labels"]["omnia.owner_id"] = PROJECT
            elif change == "created":
                daemon.records[IMAGE]["Created"] = "2026-11-03T08:00:00Z"
            elif change == "id":
                daemon.records[IMAGE]["Id"] = OTHER_IMAGE
            elif change == "consumer":
                daemon.consumers.append({"Id": "new", "ImageID": IMAGE})
            elif change == "root":
                return roots(policy, ids=frozenset({IMAGE}))
            elif change == "busy":
                return roots(policy, busy=frozenset({WORKSPACE}))
        return roots(policy)

    result = await policy.collect_local_images(tmp_path, load, apply=True, client=daemon, now=NOW)

    assert calls >= 2
    assert result["candidates"] == 1
    assert result["removed"] == 0
    assert result["preserved"] == 1
    assert daemon.attempts == []


async def test_pending_source_workspace_is_preserved_before_lock_creation(tmp_path: Path) -> None:
    policy = module()
    daemon = Docker()

    result = await policy.collect_local_images(
        tmp_path, lambda: roots(policy, busy=frozenset({WORKSPACE})),
        apply=True, client=daemon, now=NOW,
    )

    assert result["candidates"] == 0
    assert daemon.attempts == []
    assert not (tmp_path / "locks").exists()


async def test_busy_producer_lock_skips_deletion(tmp_path: Path) -> None:
    policy = module()
    daemon = Docker()
    producer = WorkspaceOperationLock(tmp_path)

    async with producer.hold(UUID(WORKSPACE)):
        result = await policy.collect_local_images(
            tmp_path, lambda: roots(policy), apply=True, client=daemon, now=NOW,
        )

    assert result["busy"] == 1
    assert result["removed"] == 0
    assert daemon.attempts == []


async def test_docker_refusal_preserves_image_and_counts_without_secret_text(
    tmp_path: Path,
) -> None:
    policy = module()
    daemon = Docker()
    daemon.refuse = True

    result = await policy.collect_local_images(
        tmp_path, lambda: roots(policy), apply=True, client=daemon, now=NOW,
    )

    assert result["refused"] == result["preserved"] == 1
    assert result["removed"] == 0
    assert IMAGE in daemon.records
    assert SECRET not in repr(result)


async def test_container_created_at_deletion_is_protected_by_daemon_no_force(
    tmp_path: Path,
) -> None:
    policy = module()
    daemon = Docker()

    def concurrent_create(_reference: str) -> None:
        daemon.consumers.append({"Id": "race", "ImageID": IMAGE})

    daemon.before_remove = concurrent_create
    result = await policy.collect_local_images(
        tmp_path, lambda: roots(policy), apply=True, client=daemon, now=NOW,
    )

    assert result["refused"] == 1
    assert result["removed"] == 0
    assert IMAGE in daemon.records


async def test_each_alias_removal_rechecks_consumers_before_next_mutation(tmp_path: Path) -> None:
    policy = module()
    daemon = Docker({IMAGE: image(tags=[REPOSITORY + ":a", REPOSITORY + ":b"])})

    def concurrent_create(_reference: str) -> None:
        daemon.consumers.append({"Id": "race", "ImageID": IMAGE})

    daemon.before_remove = concurrent_create
    result = await policy.collect_local_images(
        tmp_path, lambda: roots(policy), apply=True, client=daemon, now=NOW,
    )

    assert result["removed"] == 0
    assert daemon.attempts == [(REPOSITORY + ":a", False, True)]
    assert daemon.records[IMAGE]["RepoTags"] == [REPOSITORY + ":b"]


@pytest.mark.parametrize("failure", [False, True])
async def test_only_owned_docker_client_is_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: bool,
) -> None:
    policy = module()
    daemon = Docker()
    monkeypatch.setattr(policy.docker, "from_env", lambda: daemon)
    if failure:
        daemon.image_error = RuntimeError(SECRET)
        with pytest.raises(policy.LocalImageRetentionError):
            await policy.collect_local_images(tmp_path, lambda: roots(policy), now=NOW)
    else:
        await policy.collect_local_images(tmp_path, lambda: roots(policy), now=NOW)

    assert daemon.closed == 1


@pytest.mark.parametrize("age", [0, 7199, -1, True])
async def test_grace_cannot_be_shortened(tmp_path: Path, age: int) -> None:
    policy = module()
    daemon = Docker()

    with pytest.raises(ValueError, match="min_age_seconds"):
        await policy.collect_local_images(
            tmp_path, lambda: roots(policy), apply=True, min_age_seconds=age,
            client=daemon, now=NOW,
        )

    assert daemon.attempts == []


async def test_configured_registry_boundary_is_used(
    tmp_path: Path,
) -> None:
    policy = module()
    daemon = Docker({IMAGE: image(tags=[f"build.test/max-app/{PROJECT}:old"])})

    result = await policy.collect_local_images(
        tmp_path, lambda: roots(policy), apply=True, client=daemon, now=NOW + timedelta(seconds=1),
        registry_host="build.test",
    )

    assert result["removed"] == 1
