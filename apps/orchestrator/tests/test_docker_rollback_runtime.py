"""Opt-in rollback acceptance on UUID-owned Docker resources, never production data.

Run from apps/orchestrator with OMNIA_ROLLBACK_DOCKER_TEST=1 and preloaded,
digest-pinned OMNIA_ROLLBACK_{BASE,GUARD,POSTGRES}_IMAGE. The base/guard images
must be the real portable-machine images. Docker defaults to the local Unix
socket; OMNIA_ROLLBACK_DOCKER_HOST and OMNIA_ROLLBACK_NETWORK_POOL override it
and the disposable address pool. No host ports, bots, AI calls or publication.
"""

import asyncio
import hashlib
import json
import os
import secrets
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import docker  # type: ignore[import-untyped]
import pytest

from tests.test_docker_machine_backend import backend
from yleum_orchestrator.core.stack_registry import get_stack
from yleum_orchestrator.routers.runtime import _workspace_revision
from yleum_orchestrator.routers.workspace import _read_agent_workspace_files
from yleum_orchestrator.schemas.workspace import WorkspaceAgentExecRequest
from yleum_orchestrator.services.cell_draft_support import trusted_template_source
from yleum_orchestrator.services.docker_py_cell_backend import DockerPyCellBackend
from yleum_orchestrator.services.machine_adapter import MachineAdapter
from yleum_orchestrator.services.machine_defaults import next_machine_manifest
from yleum_orchestrator.services.project_machine import ProjectMachine
from yleum_orchestrator.services.restoration_database import admin_sql

pytestmark = pytest.mark.skipif(
    os.environ.get("OMNIA_ROLLBACK_DOCKER_TEST") != "1",
    reason="explicitly enable an owned disposable real-Docker rollback test",
)

_PAGE = "src/app/page.tsx"
_MIGRATION = "drizzle/0002_rollback_probe.sql"
_SQL = "CREATE TABLE rollback_probe(id integer PRIMARY KEY, body text NOT NULL);"
_EPOCH = 7


def _required_image(name):
    value = os.environ.get(name, "").strip()
    if not value:
        pytest.fail(f"{name} must name a preloaded digest-pinned image")
    return value


def _page(marker):
    return f"export default function Page() {{ return <main>{marker}</main>; }}\n"


def _source(marker):
    template = trusted_template_source(get_stack("max-miniapp-nextjs").template_dir)
    files = {
        name: (template / name).read_text(encoding="utf-8")
        for name in ("package.json", "pnpm-lock.yaml", "tsconfig.json", "next-env.d.ts")
    }
    # Keep the real frozen dependency contract and Next build/start scripts.
    # A minimal, static app avoids MAX authentication, remote assets and user data.
    files.update(
        {
            "next.config.ts": (
                'import type { NextConfig } from "next";\n'
                "const config: NextConfig = { experimental: { cpus: 2 } };\n"
                "export default config;\n"
            ),
            "src/app/layout.tsx": (
                "export default function Layout({ children }: { children: React.ReactNode }) "
                "{ return <html><body>{children}</body></html>; }\n"
            ),
            "src/app/api/omnia/health/route.ts": (
                'export function GET() { return Response.json({ status: "ok" }); }\n'
            ),
            _PAGE: _page(marker),
            _MIGRATION: _SQL,
            "tests/rollback.test.mjs": (
                'import assert from "node:assert/strict";\n'
                'import fs from "node:fs";\n'
                'import test from "node:test";\n'
                'test("static rollback inputs", () => {\n'
                '  assert.match(fs.readFileSync("src/app/layout.tsx", "utf8"), /children/);\n'
                '  if (fs.existsSync("src/app/page.tsx")) {\n'
                '    assert.match(fs.readFileSync("src/app/page.tsx", "utf8"), /rollback-/);\n'
                "  }\n"
                "});\n"
            ),
        }
    )
    return files


@pytest.fixture
def rollback_machine(tmp_path, monkeypatch):
    host = os.environ.get("OMNIA_ROLLBACK_DOCKER_HOST", "unix:///var/run/docker.sock")
    client = docker.DockerClient(base_url=host)
    workspace_id, project_id, owner_id, run_id = (uuid4() for _ in range(4))
    identity = workspace_id.hex
    runtime = backend(
        tmp_path,
        client=client,
        workspace_id=workspace_id,
        project_id=project_id,
        owner_id=owner_id,
        root=tmp_path / "project-machines",
        namespace=f"rollback-{identity}",
        internal_network=f"rollback-{identity}-internal",
        workspace_volume=f"rollback-{identity}-source",
        base_image=_required_image("OMNIA_ROLLBACK_BASE_IMAGE"),
        guard_image=_required_image("OMNIA_ROLLBACK_GUARD_IMAGE"),
        postgres_image=_required_image("OMNIA_ROLLBACK_POSTGRES_IMAGE"),
        project_postgres_password=secrets.token_urlsafe(32),
        project_postgres_memory_bytes=256 * 1024**2,
        project_postgres_cpu_cores=0.25,
        cpu_cores=2.0,
        memory_bytes=3 * 1024**3,
        resource_profile_version="docker-owner-cell-resources-v2",
        network_pool=os.environ.get("OMNIA_ROLLBACK_NETWORK_POOL", "10.253.240.0/24"),
    )
    labels = [f"omnia.workspace_id={workspace_id}", f"omnia.namespace={runtime.namespace}"]
    try:
        client.ping()
        for image in (runtime.base_image, runtime.guard_image, runtime.postgres_image):
            client.images.get(image)  # Fail explicitly; never pull an unrequested image.
        runtime._network(runtime.internal_network, internal=True)
        # Source IO uses the same owner-cell identity as a provisioned workspace;
        # machine-owned cache/data volumes have a different resource-kind contract.
        client.volumes.create(
            name=runtime.workspace_volume,
            labels={
                "omnia.managed": "true",
                "omnia.project_cell": "true",
                "omnia.workspace_id": str(workspace_id),
                "omnia.project_id": str(project_id),
                "omnia.owner_id": str(owner_id),
                "omnia.provider": "docker_owner_canary",
                "omnia.profile_version": runtime.resource_profile_version,
                "omnia.resource_kind": "workspace",
                "omnia.namespace": runtime.namespace,
            },
        )
        volume_io = DockerPyCellBackend(
            docker_host=host,
            helper_image=runtime.base_image,
            client_factory=lambda _: client,
        )
        manager = SimpleNamespace(
            docker=volume_io,
            state_store=SimpleNamespace(root=tmp_path / "cells"),
        )
        machine = ProjectMachine(
            tmp_path / "project-machines",
            workspace_id,
            runtime,
            lease_epoch=lambda: _EPOCH,
        )
        adapter = MachineAdapter(manager, SimpleNamespace())
        monkeypatch.setattr(adapter, "parts", lambda _: (machine, runtime))
        monkeypatch.setattr(adapter, "checkpoint", AsyncMock(return_value=None))
        monkeypatch.setattr(adapter, "_start_boundary", lambda *_args, **_kwargs: None)
        state = SimpleNamespace(
            workspace_id=workspace_id,
            project_id=project_id,
            owner_id=owner_id,
            active_generation_run_id=run_id,
            operations=[],
        )
        yield SimpleNamespace(
            backend=runtime,
            manager=manager,
            machine=machine,
            adapter=adapter,
            state=state,
            run_id=run_id,
            manifest=next_machine_manifest(),
        )
    finally:
        # Both filters are mandatory: all cleanup stays inside this fixture's UUID.
        for container in client.containers.list(all=True, filters={"label": labels}):
            container.remove(force=True)
        for network in client.networks.list(filters={"label": labels}):
            network.remove()
        for volume in client.volumes.list(filters={"label": labels}):
            volume.remove(force=True)
        client.close()


def _http(runtime, path):
    product = runtime._container()
    assert product is not None
    script = (
        "import http.client,json,sys; "
        "c=http.client.HTTPConnection('127.0.0.1',3000,timeout=10); "
        "c.request('GET',sys.argv[1]); r=c.getresponse(); "
        "print(json.dumps([r.status,r.read().decode()])); c.close()"
    )
    result = product.exec_run(["python3", "-I", "-S", "-c", script, path], user="0:0")
    assert result.exit_code == 0, "fixture HTTP probe failed"
    return json.loads(result.output)


def _database_state(runtime):
    return admin_sql(
        runtime,
        """
BEGIN READ ONLY;
SELECT json_build_object(
 'rows', (SELECT json_agg(r ORDER BY id) FROM rollback_probe r),
 'ledger', (SELECT json_agg(r ORDER BY name) FROM public.__omnia_project_migrations r));
COMMIT;
""",
    ).strip()


async def _execute(fixture, role):
    files = await _read_agent_workspace_files(fixture.manager, fixture.backend.workspace_volume)
    request = WorkspaceAgentExecRequest(
        generation_run_id=fixture.run_id,
        fencing_epoch=_EPOCH,
        expected_revision=_workspace_revision(files),
        cmd=f"omnia:{role}",
        task_role=role,
        operation_id=uuid4(),
        timeout_seconds=900,
    )
    result = await fixture.adapter.guarded_execute(fixture.state, fixture.manifest, request)
    assert result.exit_code == 0 and not result.timed_out, result.output
    return request


@pytest.mark.parametrize("core_only", [False, True], ids=["accepted-product", "safe-core-only"])
async def test_source_rollback_replaces_real_next_runtime_and_preserves_database(
    rollback_machine,
    core_only,
):
    fixture = rollback_machine
    runtime = fixture.backend
    marker_b = "rollback-candidate-" + runtime.workspace_id.hex
    marker_a = "rollback-accepted-" + runtime.workspace_id.hex
    await fixture.manager.docker.write_volume_files(
        runtime.workspace_volume,
        {name: text.encode() for name, text in _source(marker_b).items()},
    )
    await _execute(fixture, "full_build")
    status_b, html_b = await asyncio.to_thread(_http, runtime, "/")
    assert status_b == 200 and marker_b in html_b
    old_product_id = runtime._container().id
    postgres_id = runtime._project_postgres().id
    database_volume = runtime.project_postgres_volume
    await asyncio.to_thread(
        admin_sql,
        runtime,
        "INSERT INTO rollback_probe VALUES (1, 'retained-business-row');",
    )
    before = await asyncio.to_thread(_database_state, runtime)
    assert b"retained-business-row" in before

    # Only source changes. The old compiled output/process must demonstrably remain B.
    if core_only:
        await fixture.manager.docker.delete_volume_paths(runtime.workspace_volume, (_PAGE,))
    else:
        await fixture.manager.docker.write_volume_files(
            runtime.workspace_volume,
            {_PAGE: _page(marker_a).encode()},
        )
    restored = await _read_agent_workspace_files(fixture.manager, runtime.workspace_volume)
    if core_only:
        assert _PAGE not in restored
    else:
        assert restored[_PAGE] == _page(marker_a)
    stale_status, stale_html = await asyncio.to_thread(_http, runtime, "/")
    assert stale_status == 200 and marker_b in stale_html and marker_a not in stale_html
    assert runtime._container().id == old_product_id

    request = await _execute(fixture, "restore_runtime")
    status_a, html_a = await asyncio.to_thread(_http, runtime, "/")
    assert status_a == (404 if core_only else 200)
    assert marker_b not in html_a
    if not core_only:
        assert marker_a in html_a
    assert (await asyncio.to_thread(_http, runtime, "/api/omnia/health"))[0] == 200
    assert runtime._container().id != old_product_id
    assert runtime._project_postgres().id == postgres_id
    assert runtime.project_postgres_volume == database_volume
    assert await asyncio.to_thread(_database_state, runtime) == before
    assert await _read_agent_workspace_files(fixture.manager, runtime.workspace_volume) == restored
    receipt = fixture.adapter.compilation_receipt(fixture.state, request.operation_id)
    assert receipt is not None
    assert receipt["collector_version"] == "next-restored-runtime-v1"
    assert receipt["source_revision"] == request.expected_revision
    assert receipt["operation_id"] == str(request.operation_id)
    assert receipt["manifest_digest"] == fixture.manifest.digest()
    migration = fixture.adapter.migration_receipt(fixture.state, request.operation_id)
    assert migration is not None and migration["migration_count"] == 1
    inventory = {_MIGRATION: hashlib.sha256(_SQL.encode()).hexdigest()}
    assert (
        migration["source_digest"]
        == hashlib.sha256(
            json.dumps(inventory, sort_keys=True).encode(),
        ).hexdigest()
    )
