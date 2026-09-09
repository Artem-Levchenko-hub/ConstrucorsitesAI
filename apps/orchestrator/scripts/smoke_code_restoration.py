"""Real Docker / Next.js / PostgreSQL restore acceptance on fresh isolated identities.

Run with the documented orchestrator environment loaded and a private --qa-parent.
Only fresh IDs or a private root previously created by this script are accepted.
No model, MAX API or customer data is used. Cleanup checks every resource's owner.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import secrets
import tempfile
import traceback
import urllib.error
import urllib.request
from pathlib import Path
from uuid import UUID, uuid4, uuid5

from smoke_cell_publication import host_budget, ownership, require, signed_cookie


def fixture_files(version: int) -> dict[str, str]:
    template = Path(__file__).resolve().parents[1] / "templates" / "max-miniapp-nextjs"
    fields = "id,owner_id,name" + (",surname" if version == 2 else "")
    columns = [
        {"name": "id", "type": "uuid", "nullable": False},
        {"name": "owner_id", "type": "text", "nullable": False},
        {"name": "name", "type": "text", "nullable": False},
    ]
    if version == 2:
        columns.append({"name": "surname", "type": "text", "nullable": True})
    manifest = {
        "version": 1,
        "tasks": [
            {
                "name": "build",
                "role": "full_build",
                "argv": ["pnpm", "build"],
                "timeout_seconds": 600,
            }
        ],
        "services": [
            {
                "name": "web",
                "argv": ["pnpm", "start"],
                "readiness": {"port": 3000, "path": "/", "timeout_seconds": 60},
            }
        ],
        "routes": [{"path": "/", "service": "web", "port": 3000}],
    }
    # Deliberately omit tenant WHERE predicates: the real database boundary must
    # constrain reads and writes even when historical SQL forgets that predicate.
    route = """
import {Pool} from 'pg';
export const dynamic='force-dynamic';
const pool=new Pool({connectionString:process.env.DATABASE_URL,max:3});
export async function GET() {
 try {const result=await pool.query('SELECT FIELDS FROM restore_customers ORDER BY name');
  return Response.json({version:VERSION,records:result.rows});
 } catch {return Response.json({error:'read failed'},{status:500});}
}
export async function POST(request) {
 try {const input=await request.json();
  await pool.query('INSERT INTO restore_customers(id,owner_id,name) VALUES($1,$2,$3)',
   [input.id,request.headers.get('x-omnia-user-id'),input.name]);
  return Response.json({saved:true},{status:201});
 } catch {return Response.json({error:'write failed'},{status:500});}
}
export async function PATCH(request) {
 try {const input=await request.json();
  const result=await pool.query('UPDATE restore_customers SET name=$1 WHERE id=$2',
   [input.name,input.id]);
  return Response.json({changed:result.rowCount},{status:result.rowCount?200:404});
 } catch {return Response.json({error:'update failed'},{status:500});}
}
""".replace("FIELDS", fields).replace("VERSION", str(version))
    return {
        "package.json": (template / "package.json").read_text(),
        "pnpm-lock.yaml": (template / "pnpm-lock.yaml").read_text(),
        ".omnia/cell.json": json.dumps(manifest),
        ".omnia/data-contract.json": json.dumps(
            {
                "version": 1,
                "tables": [
                    {
                        "name": "restore_customers",
                        "columns": columns,
                        "owner_column": "owner_id",
                        "primary_key": ["id"],
                        "unique_keys": [],
                    }
                ],
            }
        ),
        "next.config.mjs": (
            "export default {experimental:{cpus:1},serverExternalPackages:['pg']};\n"
        ),
        "src/app/layout.jsx": (
            "export default function Layout({children}){"
            "return <html><body>{children}</body></html>}"
        ),
        "src/app/page.jsx": (
            f"export default function Page(){{return <h1>Restore canary v{version}</h1>}}"
        ),
        "src/app/api/records/route.js": route,
    }


def http(url: str, cookie: str, method="GET", payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(
        url + "/api/records",
        data=data,
        method=method,
        headers={"Cookie": cookie, "Content-Type": "application/json", "Origin": url},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        response = opener.open(request, timeout=30)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        body = response.read(65536)
        return response.status, json.loads(body)


async def run(args):
    from omnia_orchestrator.core.cell_resources import LifecycleMutation
    from omnia_orchestrator.core.config import Settings, get_settings
    from omnia_orchestrator.core.project_machine import MachineManifest
    from omnia_orchestrator.core.workspace_provider import WorkspaceSpec
    from omnia_orchestrator.routers.workspace import _read_agent_workspace_files
    from omnia_orchestrator.schemas.code_restoration import (
        CodeRestorationApply,
        CodeRestorationPrepare,
    )
    from omnia_orchestrator.services.cell_publication_capacity import production_manager
    from omnia_orchestrator.services.cell_reservations import CellCapacityReservationStore
    from omnia_orchestrator.services.cell_state import CellStateStore
    from omnia_orchestrator.services.code_restoration_engine import CodeRestorationEngine
    from omnia_orchestrator.services.docker_machine_backend import _archive_file
    from omnia_orchestrator.services.project_machine import (
        machine_budget,
        machine_effect,
        write_controller_json,
    )
    from omnia_orchestrator.services.restoration_database import admin_sql
    from omnia_orchestrator.services.workspace_provider_factory import build_workspace_provider

    require(os.name == "posix", "Linux and the documented local Docker are required")
    parent = await asyncio.to_thread(Path(args.qa_parent).resolve, strict=True)
    require(parent.is_dir() and parent.stat().st_mode & 0o077 == 0, "Private QA parent required")
    root = (
        await asyncio.to_thread(Path(args.resume_root).resolve, strict=True)
        if args.resume_root
        else Path(tempfile.mkdtemp(prefix="code-restore-", dir=parent))
    )
    require(
        root.parent == parent
        and root.name.startswith("code-restore-")
        and root.stat().st_mode & 0o077 == 0,
        "Fresh private canary root required",
    )
    (root / "state").mkdir(mode=0o700, exist_ok=True)
    print("QA_ARTIFACT_ROOT=" + str(root), flush=True)
    cfg = get_settings()
    settings = Settings.model_validate(
        {
            **cfg.model_dump(),
            "cell_state_path": str(root / "state" / "project-cells.json"),
            "workspace_provider": "docker_owner_canary",
            "docker_owner_canary_enabled": True,
            "cell_machine_enabled": True,
            "cell_profile_version": "docker-owner-cell-resources-v2",
            "cell_bundle_cpu_cores": 1.0,
            "cell_bundle_memory_bytes": 1024**3,
            "cell_active_machine_cpu_cores": 0.5,
            "cell_active_machine_memory_bytes": 2 * 1024**3,
            "cell_project_postgres_cpu_cores": 0.1,
            "cell_project_postgres_memory_bytes": 128 * 1024**2,
            "cell_helper_cpu_cores": 0.2,
            "cell_helper_memory_bytes": 128 * 1024**2,
            "cell_managed_core_cpu_cores": 0.2,
            "cell_managed_core_memory_bytes": 512 * 1024**2,
            "cell_required_free_disk_bytes": 4 * 1024**3,
        }
    )
    provider = build_workspace_provider(settings)
    manager = provider.resource_manager
    require(manager is not None and manager.namespace == "test", "Fresh canary provider required")
    adapter = manager.machine_runtime
    client = manager.docker._client_obj()
    workspace, project, owner = uuid4(), uuid4(), uuid4()
    allowed = {workspace}
    if args.resume_root:
        saved_evidence = json.loads((root / "evidence.json").read_text())
        workspace = UUID(saved_evidence["workspace_id"])
        prior = manager.state_store.load(workspace)
        require(
            prior is not None and str(prior.project_id) == saved_evidence["project_id"],
            "Canary evidence identity mismatch",
        )
        project, owner = prior.project_id, prior.owner_id
        require(project is not None and owner is not None, "Canary owner required")
        allowed = {
            item.workspace_id
            for item in manager.state_store.all_states()
            if item.project_id == project and item.owner_id == owner
        }
        from omnia_orchestrator.services import nginx_writer
        from omnia_orchestrator.services.cell_publication import publication_root

        # A prior public runtime would be a third bundle during preparation.
        # Preserve its journal/resources for explicit operator recovery instead
        # of resetting the in-memory publication handle and allocating again.
        previous_host = nginx_writer.prod_host("qa-restore-" + project.hex[:20])
        require(
            not (publication_root(settings) / str(project)).exists()
            and not (Path(cfg.nginx_sites_dir) / (previous_host + ".conf")).exists(),
            "Resume refused after publication started; recover the owned QA publication first",
        )
    ledger_root = CellStateStore(cfg.cell_state_path).root
    allocated = CellCapacityReservationStore(
        ledger_root.parent / (ledger_root.name + "-capacity-reservations")
    ).totals()
    candidate_profile = production_manager(manager, settings).profile
    required_cpu = manager.profile.full_quota.cpu_cores + candidate_profile.full_quota.cpu_cores
    required_memory = (
        manager.profile.full_quota.memory_bytes + candidate_profile.full_quota.memory_bytes
    )
    # Prepare both artifacts before seeding the public environment. Candidate
    # and public runtimes never overlap; at most two complete bundles are live.
    capacity = manager.capacity_reader.read()
    require(capacity.failure_reason is None, "Host capacity could not be verified")
    host_budget(
        allocated.cpu_cores,
        required_cpu,
        cfg.cell_host_cpu_reserve_cores,
        capacity.cpu_count,
        "CPU",
    )
    host_budget(
        allocated.memory_bytes,
        required_memory,
        cfg.cell_host_memory_reserve_bytes,
        capacity.memory_total_bytes,
        "memory",
    )
    host_budget(
        0,
        required_memory,
        cfg.cell_host_memory_reserve_bytes,
        capacity.memory_available_bytes,
        "available memory",
    )
    print(
        json.dumps({"required_cpu": required_cpu, "required_memory": required_memory}), flush=True
    )
    evidence = {"workspace_id": str(workspace), "project_id": str(project), "checks": []}

    def record(name):
        evidence["checks"].append(name)
        write_controller_json(root / "evidence.json", evidence)
        print(json.dumps({"check": name, "passed": True}), flush=True)

    def manager_for(value):
        require(value == workspace, "Foreign workspace refused")
        return manager

    engine = CodeRestorationEngine(settings, manager_factory=manager_for)
    publication = None
    public_host = None
    latest_apply = None
    success = False
    try:
        with machine_budget(2400):
            if not args.resume_root:
                await manager.ensure(
                    WorkspaceSpec(
                        workspace_id=workspace,
                        project_id=project,
                        owner_id=owner,
                        profile_version=settings.cell_profile_version,
                    ),
                    LifecycleMutation(uuid4(), 1, "a" * 64),
                )
            state = manager.state_store.load(workspace)
            machine, backend = adapter.parts(state)
            files = fixture_files(2)
            if args.resume_root:
                current_source = await _read_agent_workspace_files(
                    manager, backend.workspace_volume
                )
                page = current_source.get("src/app/page.jsx", "")
                require(
                    page
                    in {fixture_files(1)["src/app/page.jsx"], fixture_files(2)["src/app/page.jsx"]},
                    "Resume refused non-fixture source",
                )
                files = fixture_files(1 if "canary v1" in page else 2)
            manifest = MachineManifest.from_files(files)
            if not args.resume_root:
                write_controller_json(
                    machine.path,
                    {
                        "workspace_id": str(workspace),
                        "epoch": 1,
                        "ready_epoch": 1,
                        "operations": {},
                        "manifest": manifest.model_dump(mode="json"),
                    },
                )
            if not args.resume_root:
                await machine_effect(backend.ensure, manifest, 1)
            for name, content in files.items() if not args.resume_root else []:
                require(
                    backend._container().put_archive(
                        "/workspace", _archive_file(name, content.encode())
                    ),
                    "Fixture write failed",
                )
            if not args.resume_root:
                await machine_effect(
                    admin_sql,
                    backend,
                    "CREATE TABLE restore_customers ("
                    "id uuid PRIMARY KEY,owner_id text NOT NULL,name text NOT NULL,surname text);"
                    "INSERT INTO restore_customers VALUES("
                    "'00000000-0000-0000-0000-000000000011','10001','Alice','Retained'),"
                    "('00000000-0000-0000-0000-000000000012','10002','Bob','Private');",
                )
            if not args.resume_root:
                await machine_effect(
                    engine._command,
                    backend,
                    ["pnpm", "install", "--frozen-lockfile", "--ignore-scripts"],
                    420,
                )
            if not args.resume_root:
                await machine_effect(engine._command, backend, ["pnpm", "build"], 600)
                await engine._start(backend, manifest, 1)
                await machine_effect(adapter._start_boundary, state, manifest, backend, 1)
            record("v2-source-real-next-app-and-current-postgresql")

            prepared_candidates = {}

            async def restore(version, *, prepare_only=False):
                nonlocal files, latest_apply
                current = manager.state_store.load(workspace)
                operation = uuid4()
                target_files = fixture_files(version)
                prepare = CodeRestorationPrepare(
                    operation_id=operation,
                    workspace_id=workspace,
                    project_id=project,
                    owner_id=owner,
                    expected_source_head="a" * 40,
                    target_commit_sha="b" * 40,
                    planned_commit_sha="c" * 40,
                    fencing_epoch=current.fencing_epoch,
                    current_files=[
                        {
                            "path": name,
                            "sha256": hashlib.sha256(content.encode()).hexdigest(),
                            "mode": "100644",
                        }
                        for name, content in files.items()
                    ],
                    files=[
                        {
                            "path": name,
                            "content_base64": base64.b64encode(content.encode()).decode(),
                        }
                        for name, content in target_files.items()
                    ],
                )
                allowed.add(uuid5(operation, "candidate"))
                if version in prepared_candidates:
                    prepare, prepared = prepared_candidates.pop(version)
                    require(
                        current.fencing_epoch == prepare.fencing_epoch,
                        "Prepared candidate source epoch changed",
                    )
                else:
                    prepared = await engine.prepare(prepare)
                if prepared["state"] != "ready":
                    write_controller_json(root / "preparation-failure.json", prepared)
                require(
                    prepared["state"] == "ready", "Preparation did not pass; inspect private report"
                )
                if prepare_only:
                    prepared_candidates[version] = (prepare, prepared)
                    return current
                # A write AFTER the copy was checked must survive the code switch.
                _, current_backend = adapter.parts(current)
                await machine_effect(
                    admin_sql,
                    current_backend,
                    "INSERT INTO restore_customers VALUES('00000000-0000-0000-0000-000000000013',"
                    "'10001','After preparation','New data') ON CONFLICT DO NOTHING;",
                )
                apply = CodeRestorationApply(
                    **prepare.model_dump(exclude={"files", "current_files", "fencing_epoch"}),
                    candidate_id=UUID(prepared["candidate_id"]),
                    report_revision=1,
                    expected_fencing_epoch=current.fencing_epoch,
                    fencing_epoch=current.fencing_epoch + 1,
                )
                outcome = await engine.apply(apply, prepared)
                require(outcome["applied"] is True, "Activation returned recovered previous code")
                # Fresh controller observes the receipt instead of applying twice.
                reloaded = CodeRestorationEngine(settings, manager_factory=manager_for)
                require(
                    await reloaded.observe(apply, prepared) == outcome,
                    "Fresh observation disagrees",
                )
                record("restore-v" + str(version) + "-and-lost-response-observation")
                files = target_files
                latest_apply = apply
                return manager.state_store.load(workspace)

            async def publish():
                nonlocal publication, public_host
                from smoke_cell_publication import business_config

                from omnia_orchestrator.routers.runtime import _workspace_revision
                from omnia_orchestrator.schemas.cell_publication import CellDeployRequest
                from omnia_orchestrator.services import nginx_writer
                from omnia_orchestrator.services.cell_publication import CellPublicationService

                current = manager.state_store.load(workspace)
                _, source_backend = adapter.parts(current)
                actual_source = await _read_agent_workspace_files(
                    manager, source_backend.workspace_volume
                )
                slug = "qa-restore-" + project.hex[:20]
                public_host = nginx_writer.prod_host(slug)
                if publication is None:
                    require(
                        not (Path(cfg.nginx_sites_dir) / (public_host + ".conf")).exists(),
                        "Fresh publication hostname required",
                    )
                    publication = CellPublicationService(settings, manager_factory=manager_for)
                body = CellDeployRequest(
                    workspace_id=workspace,
                    project_id=project,
                    owner_id=owner,
                    snapshot_id=uuid4(),
                    candidate_id=latest_apply.candidate_id,
                    slug=slug,
                    commit_sha=latest_apply.planned_commit_sha,
                    source_revision=_workspace_revision(actual_source),
                    fencing_epoch=current.fencing_epoch,
                    accepted_fencing_epoch=latest_apply.fencing_epoch,
                    restoration_operation_id=latest_apply.operation_id,
                    idempotency_key="qa-restore-" + uuid4().hex,
                    runtime_env={
                        "MAX_BOT_TOKEN": secrets.token_urlsafe(32),
                        "MAX_WEBHOOK_SECRET": secrets.token_urlsafe(32),
                    },
                    business_config=business_config(),
                )
                public_id = publication.production_identity(body)
                allowed.add(public_id)
                await publication.submit(body)
                await publication.drain()
                result = publication.get(project)
                require(result.phase == "done", "Restored publication failed: " + str(result.error))
                public_manager = publication._production_manager(workspace)
                cookie = signed_cookie(public_manager.machine_runtime.secret(public_id), "10001")
                return result.prod_url, cookie

            if args.resume_applied_v1:
                require(
                    args.resume_root and files == fixture_files(1), "Confirmed v1 source required"
                )
                current_source = await _read_agent_workspace_files(
                    manager, backend.workspace_volume
                )
                from omnia_orchestrator.routers.runtime import _workspace_revision

                proof = backend._metadata().get("restoration_proof", {})
                require(
                    proof.get("fencing_epoch") == state.fencing_epoch
                    and proof.get("source_revision") == _workspace_revision(current_source)
                    and proof.get("project_id") == str(project)
                    and proof.get("owner_id") == str(owner),
                    "Resume requires the exact already-applied fixture receipt",
                )
                latest_apply = CodeRestorationApply(
                    operation_id=UUID(proof["operation_id"]),
                    workspace_id=workspace,
                    project_id=project,
                    owner_id=owner,
                    candidate_id=UUID(proof["candidate_id"]),
                    expected_source_head="a" * 40,
                    target_commit_sha="b" * 40,
                    planned_commit_sha=proof["source_commit_sha"],
                    expected_fencing_epoch=state.fencing_epoch - 1,
                    fencing_epoch=state.fencing_epoch,
                    report_revision=1,
                )
                prepared = json.loads(
                    (engine._directory(latest_apply.operation_id) / "prepared.json").read_text()
                )
                outcome = await engine.observe(latest_apply, prepared)
                require(outcome and outcome["applied"], "Existing activation could not be observed")
                record("restore-v1-and-lost-response-observation")
            else:
                state = await restore(1)
            _, backend = adapter.parts(state)
            url = "http://" + adapter.preview(state)[1] + ":3000"
            alice, bob = (
                signed_cookie(adapter.secret(workspace), user) for user in ("10001", "10002")
            )
            status, payload = await asyncio.to_thread(http, url, alice)
            require(
                status == 200 and payload["version"] == 1 and len(payload["records"]) == 2,
                "Alice did not get v1 and her current rows",
            )
            require(
                all(
                    row["owner_id"] == "10001" and "surname" not in row
                    for row in payload["records"]
                ),
                "Historical reads exposed hidden columns or another user",
            )
            status, payload = await asyncio.to_thread(http, url, bob)
            require(
                status == 200
                and len(payload["records"]) == 1
                and payload["records"][0]["name"] == "Bob",
                "Bob isolation failed",
            )
            status, _ = await asyncio.to_thread(
                http,
                url,
                bob,
                "PATCH",
                {"id": "00000000-0000-0000-0000-000000000011", "name": "Intrusion"},
            )
            require(status == 404, "Cross-user update was allowed")
            status, _ = await asyncio.to_thread(
                http,
                url,
                alice,
                "PATCH",
                {"id": "00000000-0000-0000-0000-000000000011", "name": "Alice edited"},
            )
            require(status == 200, "Historical edit failed")
            status, _ = await asyncio.to_thread(
                http,
                url,
                alice,
                "POST",
                {"id": "00000000-0000-0000-0000-000000000014", "name": "Created under v1"},
            )
            require(status == 201, "Historical insert failed")
            actual = await machine_effect(
                admin_sql,
                backend,
                "SELECT name || ':' || surname FROM restore_customers "
                "WHERE id='00000000-0000-0000-0000-000000000011';",
            )
            require(actual.strip() == b"Alice edited:Retained", "Hidden surname was lost")
            record("two-users-read-write-cross-user-denial-hidden-surname-and-new-records")
            if args.publication:
                await restore(2, prepare_only=True)
                public_url, public_cookie = await publish()
                status, payload = await asyncio.to_thread(http, public_url, public_cookie)
                require(
                    status == 200 and payload["version"] == 1 and len(payload["records"]) == 3,
                    "Restored public v1 is not serving current data",
                )
                status, _ = await asyncio.to_thread(
                    http,
                    public_url,
                    public_cookie,
                    "PATCH",
                    {"id": "00000000-0000-0000-0000-000000000011", "name": "PUBLIC ONLY"},
                )
                require(status == 200, "Independent public write failed")
                record("explicit-publication-v1-has-independent-writable-database")
            state = await restore(2)
            url = "http://" + adapter.preview(state)[1] + ":3000"
            alice = signed_cookie(adapter.secret(workspace), "10001")
            status, payload = await asyncio.to_thread(http, url, alice)
            require(
                status == 200 and payload["version"] == 2 and len(payload["records"]) == 3,
                "Return to new code lost current rows",
            )
            require(
                any(
                    row["name"] == "Alice edited" and row["surname"] == "Retained"
                    for row in payload["records"]
                ),
                "New code did not recover retained surname",
            )
            record("return-to-v2-retains-old-edits-new-rows-and-hidden-fields")
            if args.publication:
                # Publication stays on v1 until another explicit publish.
                current_release = publication._read(project)
                public_id = UUID(current_release["production_workspace_id"])
                public_cookie = signed_cookie(
                    publication._production_manager(workspace).machine_runtime.secret(public_id),
                    "10001",
                )
                status, payload = await asyncio.to_thread(http, public_url, public_cookie)
                require(
                    status == 200 and payload["version"] == 1,
                    "Draft restore changed live publication",
                )
                public_url, public_cookie = await publish()
                status, payload = await asyncio.to_thread(http, public_url, public_cookie)
                require(
                    status == 200
                    and payload["version"] == 2
                    and any(
                        row["name"] == "PUBLIC ONLY" and row["surname"] == "Retained"
                        for row in payload["records"]
                    ),
                    "Republish overwrote current public rows",
                )
                record("explicit-v2-republish-preserves-public-only-writes-and-hidden-fields")
            # Exercise the next ordinary generation after a restored environment
            # has been stopped. No LLM or external business integration is called.
            from omnia_orchestrator.routers.runtime import _workspace_revision
            from omnia_orchestrator.schemas.workspace import WorkspaceAgentExecRequest

            await adapter.halt(state, retain_trusted=True)
            await adapter.resume_preview(state)
            url = "http://" + adapter.preview(state)[1] + ":3000"
            alice = signed_cookie(adapter.secret(workspace), "10001")
            status, payload = await asyncio.to_thread(http, url, alice)
            require(
                status == 200 and len(payload["records"]) == 3,
                "Protected cold resume lost current records",
            )
            record("protected-cold-resume-keeps-current-data")
            generation = uuid4()
            await manager.ensure(
                WorkspaceSpec(
                    workspace_id=workspace,
                    project_id=project,
                    owner_id=owner,
                    profile_version=settings.cell_profile_version,
                    generation_run_id=generation,
                ),
                LifecycleMutation(uuid4(), state.fencing_epoch + 1, "d" * 64),
            )
            state = manager.state_store.load(workspace)
            _, backend = adapter.parts(state)
            desired = json.loads(files[".omnia/data-contract.json"])
            desired["tables"][0]["columns"].append(
                {"name": "notes", "type": "text", "nullable": True}
            )
            files[".omnia/data-contract.json"] = json.dumps(desired)
            await manager.docker.write_volume_files(
                backend.workspace_volume,
                {".omnia/data-contract.json": files[".omnia/data-contract.json"].encode()},
            )
            current_source = await _read_agent_workspace_files(manager, backend.workspace_volume)
            request = WorkspaceAgentExecRequest(
                generation_run_id=generation,
                fencing_epoch=state.fencing_epoch,
                expected_revision=_workspace_revision(current_source),
                cmd="omnia-db apply .omnia/data-contract.json",
                timeout_seconds=900,
            )
            async with manager.operation_lock.hold(workspace):
                result = await adapter.execute(state, MachineManifest.from_files(files), request)
            require(result.exit_code == 0, "Protected generation migration did not complete")
            present = await machine_effect(
                admin_sql,
                backend,
                "SELECT count(*) FROM information_schema.columns WHERE "
                "table_schema='public' AND table_name='restore_customers' AND column_name='notes';",
            )
            require(present.strip() == b"1", "Additive migration did not create its column")
            await manager.release_generation(
                workspace,
                LifecycleMutation(uuid4(), state.fencing_epoch + 1, "e" * 64),
                generation_run_id=generation,
            )
            state = manager.state_store.load(workspace)
            require(state.active_generation_run_id is None, "Generation lease was not released")
            await adapter.resume_preview(state)
            url = "http://" + adapter.preview(state)[1] + ":3000"
            alice = signed_cookie(adapter.secret(workspace), "10001")
            status, payload = await asyncio.to_thread(http, url, alice)
            require(status == 200 and len(payload["records"]) == 3, "Post-generation reads failed")
            record("next-generation-additive-DDL-release-and-resume-retain-data")
            success = True
    except BaseException:
        (root / "failure.txt").write_text(traceback.format_exc())
        (root / "failure.txt").chmod(0o600)
        raise
    finally:
        if success and args.cleanup_on_success:
            inventory = []
            for collection, kind in (
                (client.containers, "containers"),
                (client.networks, "networks"),
                (client.volumes, "volumes"),
            ):
                resources = []
                for value in allowed:
                    kwargs = {"filters": {"label": f"omnia.workspace_id={value}"}}
                    if kind == "containers":
                        kwargs["all"] = True
                    resources.extend(collection.list(**kwargs))
                for resource in resources:
                    ownership(resource, allowed, project, owner)
                inventory.append((kind, resources))
            for kind, resources in inventory:
                for resource in resources:
                    if kind == "containers":
                        resource.remove(force=True)
                    else:
                        resource.remove()
            if public_host:
                from omnia_orchestrator.services import nginx_writer

                await nginx_writer.unpublish(public_host)
            record("fresh-canary-resources-cleaned")
        client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qa-parent", required=True)
    parser.add_argument("--cleanup-on-success", action="store_true")
    parser.add_argument("--resume-root", help="Resume only a private canary created by this script")
    parser.add_argument(
        "--resume-applied-v1",
        action="store_true",
        help="Continue a confirmed fixture v1 activation after driver interruption",
    )
    parser.add_argument(
        "--publication", action="store_true", help="Also test independent public HTTPS releases"
    )
    arguments = parser.parse_args()
    asyncio.run(run(arguments))
