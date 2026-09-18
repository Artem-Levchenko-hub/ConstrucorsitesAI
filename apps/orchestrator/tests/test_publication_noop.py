"""P04: request idempotency and desired-release identity are different things.
The same healthy release is not rebuilt; a config-only change takes the
configuration path; an unhealthy, unproven or changed release still gets a full
publication; secrets never enter the fingerprint."""

from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import UUID

import pytest

from omnia_orchestrator.core.cell_resources import CellIdentityConflict
from omnia_orchestrator.services.cell_publication import CellPublicationService
from omnia_orchestrator.services.project_machine import write_controller_json
from omnia_orchestrator.services.publication_identity import (
    classify_publication,
    fingerprint_key,
    fingerprint_payload,
    release_fingerprint,
    source_identity,
)
from tests.test_cell_publication import request

TOKEN = "old-test-token"


def active_for(service, value, *, extra: dict | None = None) -> dict:
    key = fingerprint_key(service.root)
    effective = service._effective_request(value)
    return {
        "release_id": str(UUID(int=77)),
        "image_id": "sha256:" + "a" * 64,
        "prod_url": "https://real-product.example.test",
        "snapshot_id": str(value.snapshot_id),
        "source_revision": value.source_revision,
        "fingerprint": release_fingerprint(effective, key),
        "source_identity": source_identity(effective),
        **(extra or {}),
    }


def published(tmp_path, value, *, active: dict | None = None, seeded: bool = True):
    service = CellPublicationService(SimpleNamespace(), root=tmp_path)
    service._write(
        value.project_id,
        {
            "project_id": str(value.project_id),
            "owner_id": str(value.owner_id),
            "slug": value.slug,
            "source_workspace_id": str(value.workspace_id),
            "production_workspace_id": str(service.production_identity(value)),
            "history": [],
            "active_release": None,
            "data_seeded": seeded,
        },
    )
    if active is None:
        active = active_for(service, value)
    saved = service._read(value.project_id)
    saved["active_release"] = active
    service._write(value.project_id, saved)
    write_controller_json(
        tmp_path / str(value.project_id) / "requests" / f"{active['release_id']}.json",
        value.model_dump(mode="json"),
    )

    async def never_execute(*_args):
        raise AssertionError("a full publication must not start")

    service._execute = never_execute
    return service


def test_fingerprint_is_secret_free_and_changes_with_config_or_source():
    key = b"k" * 32
    value = request(runtime_env={"MAX_BOT_TOKEN": TOKEN}, business_config={"app_name": "A"})
    payload = json.dumps(fingerprint_payload(value, key))
    assert TOKEN not in payload and "MAX_BOT_TOKEN" not in payload
    same = release_fingerprint(value, key)
    assert same == release_fingerprint(request(**value.model_dump()), key)
    revoked = request(**{**value.model_dump(), "runtime_env": {}})
    assert same != release_fingerprint(revoked, key)
    other_config = request(**{**value.model_dump(), "business_config": {"app_name": "B"}})
    assert same != release_fingerprint(other_config, key)
    other_commit = request(**{**value.model_dump(), "commit_sha": "b" * 40})
    assert same != release_fingerprint(other_commit, key)
    assert same != release_fingerprint(value, b"other-key" * 4)  # another installation


def test_classification_from_durable_state():
    key = b"k" * 32
    value = request()
    active = {
        "fingerprint": release_fingerprint(value, key),
        "source_identity": source_identity(value),
    }
    assert classify_publication(value, None, data_seeded=False, key=key).kind == "release"
    assert classify_publication(value, active, data_seeded=False, key=key).kind == "release"
    assert classify_publication(value, {"release_id": "x"}, data_seeded=True, key=key).reason == (
        "unproven_release"
    )
    assert classify_publication(value, active, data_seeded=True, key=key).kind == "already_current"
    other_config = request(**{**value.model_dump(), "business_config_version": 2})
    assert classify_publication(other_config, active, data_seeded=True, key=key).kind == (
        "config_only"
    )
    other_source = request(**{**value.model_dump(), "source_revision": "c" * 64})
    assert classify_publication(other_source, active, data_seeded=True, key=key).kind == "release"


async def test_same_healthy_release_is_already_current_without_any_work(tmp_path):
    value = request(idempotency_key="publish-two")
    service = published(tmp_path, value)
    probes = []

    async def healthy(_request, active):
        probes.append(active["release_id"])
        return True

    service._serving_current = healthy
    result = await service.submit(value)
    assert result.phase == "done" and result.detail == "already_current"
    assert result.prod_url == "https://real-product.example.test"
    assert result.metrics.get("already_current") == 1 and "total_ms" in result.metrics
    assert probes == [str(UUID(int=77))] and service._tasks == {}
    # Replaying the same key returns the same envelope; a new key is a new no-op.
    assert (await service.submit(value)).run_id == result.run_id
    again = await service.submit(request(idempotency_key="publish-three"))
    assert again.detail == "already_current" and again.run_id != result.run_id
    assert len(service._read(value.project_id)["history"]) == 2


async def test_unhealthy_serving_gets_a_full_publication_not_a_fake_success(tmp_path):
    value = request()
    service = published(tmp_path, value)

    async def unhealthy(_request, _active):
        return False

    started = []

    async def execute(request_, run_id):
        started.append(run_id)

    service._serving_current = unhealthy
    service._execute = execute
    result = await service.submit(value)
    assert result.phase == "queued" and result.detail == "serving_unhealthy"
    await service.drain()
    assert started == [result.run_id]


async def test_config_only_change_takes_the_configuration_path(tmp_path):
    value = request(runtime_env={"MAX_BOT_TOKEN": TOKEN}, business_config={"app_name": "A"})
    service = published(tmp_path, value)
    refreshed = []

    async def refresh(project_id):
        refreshed.append(project_id)

    service._refresh_public_configuration = refresh
    changed = request(
        **{
            **value.model_dump(),
            "business_config": {"app_name": "B"},
            "business_config_version": 2,
            "idempotency_key": "publish-config",
        }
    )
    result = await service.submit(changed)
    assert result.phase == "done" and result.detail == "config_only"
    assert result.metrics.get("config_only") == 1 and service._tasks == {}
    assert refreshed == [value.project_id]
    configuration = json.loads(
        (tmp_path / str(value.project_id) / "configuration.json").read_text()
    )
    assert configuration["business_config"] == {"app_name": "B"}
    assert configuration["business_config_version"] == 2
    # The active release now carries the applied configuration in its fingerprint.
    active = service._read(value.project_id)["active_release"]
    assert active["fingerprint"] == release_fingerprint(
        service._effective_request(changed), fingerprint_key(service.root)
    )


async def test_revoked_credential_is_never_cached_as_a_no_op(tmp_path):
    value = request(runtime_env={"MAX_BOT_TOKEN": TOKEN})
    service = published(tmp_path, value)
    refreshed = []

    async def refresh(project_id):
        refreshed.append(project_id)

    service._refresh_public_configuration = refresh
    revoked = request(**{**value.model_dump(), "runtime_env": {}, "idempotency_key": "revoke-one"})
    result = await service.submit(revoked)
    assert result.detail == "config_only" and refreshed == [value.project_id]
    configuration = json.loads(
        (tmp_path / str(value.project_id) / "configuration.json").read_text()
    )
    assert configuration["runtime_env"] == {}


async def test_source_change_legacy_release_and_hostname_change(tmp_path):
    value = request()
    service = published(tmp_path, value)
    started = []

    def finishing(target):
        async def execute(request_, run_id):
            started.append(run_id)
            target._phase(request_.project_id, run_id, "done", finished_at="2026-09-18T00:00:00Z")

        return execute

    service._execute = finishing(service)
    changed = request(
        **{**value.model_dump(), "source_revision": "c" * 64, "idempotency_key": "publish-src"}
    )
    assert (await service.submit(changed)).phase == "queued"
    await service.drain()
    assert len(started) == 1

    # A release published before fingerprints existed is never assumed current.
    legacy = published(tmp_path / "legacy", value, active={"release_id": "old", "image_id": "x"})
    legacy._execute = finishing(legacy)
    assert (await legacy.submit(value)).phase == "queued"
    await legacy.drain()
    assert len(started) == 2

    moved = request(
        **{**value.model_dump(), "slug": "other-host", "idempotency_key": "publish-host"}
    )
    with pytest.raises(CellIdentityConflict, match="hostname"):
        await service.submit(moved)
