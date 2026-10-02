"""Controller transition cannot resume guest code before a durable schema proof."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

import pytest

from tests.test_cell_publication import request
from yleum_orchestrator.services.cell_publication import CellPublicationService
from yleum_orchestrator.services.project_database_schema_proof import (
    SchemaProofError,
    legacy_schema_digest,
)
from yleum_orchestrator.services.project_machine import write_controller_json

BEFORE = "CREATE ROLE postgres;\nCREATE TABLE public.tasks (id integer);\n"
AFTER = (
    BEFORE
    + "CREATE ROLE omnia_project_runtime;\n"
    "CREATE ROLE omnia_project_owner;\nCREATE ROLE omnia_project_migrator;\n"
)


def fixture(tmp_path, *, after=AFTER, accepted=None):
    value = request()
    service = CellPublicationService(SimpleNamespace(), root=tmp_path)
    release = {
        "release_id": str(UUID(int=23)),
        "epoch": 3,
        "source_revision": value.source_revision,
        "placement": {"backend": "kubernetes"},
        "schema_digest": accepted or legacy_schema_digest(BEFORE),
    }
    service._write(
        value.project_id, {"project_id": str(value.project_id), "active_release": release}
    )
    write_controller_json(
        tmp_path / str(value.project_id) / "requests" / f"{release['release_id']}.json",
        value.model_dump(mode="json"),
    )
    events = []
    state = {"dump": BEFORE, "fail": False}

    def publish(_spec, *, activate_guest=True):
        events.append("guest" if activate_guest else "roles")
        if not activate_guest:
            state["dump"] = after
            if state["fail"]:
                raise RuntimeError("preparation interrupted")

    runtime = SimpleNamespace(
        api=SimpleNamespace(get=lambda *_: {"metadata": {"uid": "synthetic-pvc-identity"}}),
        quiesce_project_app=lambda _: events.append("quiesce"),
        schema_dump=lambda _: state["dump"],
        publish=publish,
    )
    service._kubernetes = lambda: SimpleNamespace(runtime=runtime, namespace=lambda _: "app-owned")

    async def spec(*_, **__):
        return object()

    service._kubernetes_spec = spec
    return service, value, release, events, state


@pytest.mark.asyncio
async def test_role_bridge_preserves_old_hash_and_proves_new_one_before_guest(tmp_path):
    service, value, release, events, _ = fixture(tmp_path)
    assert await service._upgrade_kubernetes_project_roles(value.project_id)
    assert events == ["quiesce", "roles", "guest"]
    saved = service._read(value.project_id)["active_release"]
    assert saved["schema_digest"] == release["schema_digest"]
    assert saved["role_schema_digest"] == legacy_schema_digest(AFTER)
    assert service._accepted_schema_digest(saved) == legacy_schema_digest(AFTER)
    assert not await service._upgrade_kubernetes_project_roles(value.project_id)
    assert events == ["quiesce", "roles", "guest"]


@pytest.mark.asyncio
async def test_business_ddl_during_reconciliation_blocks_guest_and_keeps_pending_proof(tmp_path):
    service, value, _, events, _ = fixture(
        tmp_path, after=AFTER + "ALTER TABLE public.tasks ADD COLUMN x text;"
    )
    with pytest.raises(SchemaProofError, match="changed business schema"):
        await service._upgrade_kubernetes_project_roles(value.project_id)
    assert events == ["quiesce", "roles"]
    assert (
        service._read(value.project_id)["active_release"].get("project_database_role_protocol")
        is None
    )


@pytest.mark.asyncio
async def test_unaccepted_preexisting_schema_blocks_role_effect_and_guest(tmp_path):
    service, value, _, events, _ = fixture(tmp_path, accepted="a" * 64)
    with pytest.raises(SchemaProofError, match="historical schema differs"):
        await service._upgrade_kubernetes_project_roles(value.project_id)
    assert events == ["quiesce"]


@pytest.mark.asyncio
async def test_interrupted_role_transition_resumes_using_original_durable_preproof(tmp_path):
    service, value, _, events, state = fixture(tmp_path)
    state["fail"] = True
    with pytest.raises(RuntimeError, match="interrupted"):
        await service._upgrade_kubernetes_project_roles(value.project_id)
    state["fail"] = False
    assert await service._upgrade_kubernetes_project_roles(value.project_id)
    assert events == ["quiesce", "roles", "quiesce", "roles", "guest"]


@pytest.mark.asyncio
async def test_pending_bridge_for_replaced_pvc_is_denied_before_role_effect(tmp_path):
    service, value, _, events, state = fixture(tmp_path)
    state["fail"] = True
    with pytest.raises(RuntimeError):
        await service._upgrade_kubernetes_project_roles(value.project_id)
    runtime = service._kubernetes().runtime
    runtime.api.get = lambda *_: {"metadata": {"uid": "different-pvc-identity"}}
    state["fail"] = False
    with pytest.raises(SchemaProofError, match="record mismatch"):
        await service._upgrade_kubernetes_project_roles(value.project_id)
    assert events == ["quiesce", "roles", "quiesce"]


@pytest.mark.asyncio
async def test_old_role_protocol_cannot_take_already_current_success_shortcut(tmp_path):
    service, value, release, _, _ = fixture(tmp_path)
    assert not await service._serving_current(value, release)


@pytest.mark.asyncio
@pytest.mark.parametrize("changed_business", [False, True])
async def test_docker_role_bridge_verifies_before_any_guest_resume(
    tmp_path, monkeypatch, changed_business
):
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock

    from tests.test_cell_publication import _manifest

    service, value, release, events, state = fixture(
        tmp_path,
        after=AFTER + ("ALTER TABLE public.tasks ADD COLUMN bad text;" if changed_business else ""),
    )
    release.pop("placement")
    release.update(manifest=_manifest(), image_id="sha256:" + "a" * 64)
    production = str(UUID(int=31))
    service._write(
        value.project_id,
        {
            "project_id": str(value.project_id),
            "production_workspace_id": production,
            "active_release": release,
        },
    )

    @asynccontextmanager
    async def hold(_):
        yield

    def ensure(*_):
        events.append("roles")
        state["dump"] = AFTER + (
            "ALTER TABLE public.tasks ADD COLUMN bad text;" if changed_business else ""
        )

    backend = SimpleNamespace(
        project_postgres_volume="owned-data",
        _lookup=lambda *_: SimpleNamespace(attrs={"CreatedAt": "synthetic-volume-birth"}),
        quiesce_current=lambda: events.append("quiesce"),
        schema_dump=lambda: state["dump"],
        ensure_published=ensure,
    )
    manager = SimpleNamespace(
        operation_lock=SimpleNamespace(hold=hold),
        state_store=SimpleNamespace(load=lambda _: object()),
        docker=SimpleNamespace(_client_obj=lambda: SimpleNamespace(volumes=object())),
    )
    service._production_manager = lambda _: manager
    service._backend = lambda *_: backend
    monkeypatch.setattr(
        "yleum_orchestrator.services.cell_publication.ensure_managed_infrastructure", AsyncMock()
    )

    async def start(*_, **__):
        events.append("guest")

    service._start = start
    if changed_business:
        with pytest.raises(SchemaProofError, match="changed business schema"):
            await service._upgrade_docker_project_roles(value.project_id)
        assert events == ["quiesce", "roles"]
    else:
        assert await service._upgrade_docker_project_roles(value.project_id)
        assert events == ["quiesce", "roles", "guest"]
        assert (
            service._read(value.project_id)["active_release"]["schema_digest"]
            == release["schema_digest"]
        )
