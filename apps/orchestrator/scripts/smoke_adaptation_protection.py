"""Owned real Docker acceptance: protect a never-restored DB before adaptive tools.

No model, external MAX call, publication or API GenerationRun row. Uses one fresh
private workspace; cleanup verifies every resource identity even after failure.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
import traceback
from pathlib import Path
from uuid import uuid4

from smoke_cell_publication import host_budget, ownership, require, signed_cookie
from smoke_code_restoration import fixture_files, http


async def run(args):
    from omnia_orchestrator.core.cell_resources import LifecycleMutation
    from omnia_orchestrator.core.config import Settings, get_settings
    from omnia_orchestrator.core.project_machine import MachineManifest
    from omnia_orchestrator.core.workspace_provider import WorkspaceSpec
    from omnia_orchestrator.routers.workspace import _read_agent_workspace_files
    from omnia_orchestrator.schemas.workspace import WorkspaceAgentBootstrapRequest
    from omnia_orchestrator.services.cell_reservations import CellCapacityReservationStore
    from omnia_orchestrator.services.cell_state import CellStateStore
    from omnia_orchestrator.services.code_restoration_engine import CodeRestorationEngine
    from omnia_orchestrator.services.docker_machine_backend import _archive_file
    from omnia_orchestrator.services.project_machine import (
        machine_budget,
        machine_effect,
        write_controller_json,
    )
    from omnia_orchestrator.services.restoration_database import admin_sql, load_policy
    from omnia_orchestrator.services.restoration_protection import (
        protect_current_database,
        protection_journal,
    )
    from omnia_orchestrator.services.workspace_provider_factory import build_workspace_provider

    require(os.name == "posix", "Linux and documented Docker required")
    parent = await asyncio.to_thread(Path(args.qa_parent).resolve, strict=True)
    require(parent.is_dir() and parent.stat().st_mode & 0o077 == 0, "Private QA parent required")
    root = Path(tempfile.mkdtemp(prefix="adaptation-protection-", dir=parent))
    (root / "state").mkdir(mode=0o700)
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
    manager = build_workspace_provider(settings).resource_manager
    require(manager is not None and manager.namespace == "test", "Fresh canary provider required")
    adapter = manager.machine_runtime
    client = manager.docker._client_obj()
    workspace, project, owner = uuid4(), uuid4(), uuid4()
    evidence = {"workspace_id": str(workspace), "project_id": str(project), "checks": []}

    def record(name):
        evidence["checks"].append(name)
        write_controller_json(root / "evidence.json", evidence)
        print(json.dumps({"check": name, "passed": True}), flush=True)

    ledger = CellStateStore(cfg.cell_state_path).root
    allocated = CellCapacityReservationStore(
        ledger.parent / (ledger.name + "-capacity-reservations")
    ).totals()
    capacity = manager.capacity_reader.read()
    require(capacity.failure_reason is None, "Host capacity could not be verified")
    quota = manager.profile.full_quota
    host_budget(
        allocated.cpu_cores,
        quota.cpu_cores,
        cfg.cell_host_cpu_reserve_cores,
        capacity.cpu_count,
        "CPU",
    )
    host_budget(
        allocated.memory_bytes,
        quota.memory_bytes,
        cfg.cell_host_memory_reserve_bytes,
        capacity.memory_total_bytes,
        "memory",
    )
    host_budget(
        0,
        quota.memory_bytes,
        cfg.cell_host_memory_reserve_bytes,
        capacity.memory_available_bytes,
        "available memory",
    )
    engine = CodeRestorationEngine(settings, manager_factory=lambda _: manager)
    try:
        with machine_budget(1800):
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
            manifest = MachineManifest.from_files(files)
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
            await machine_effect(backend.ensure, manifest, 1)
            for name, content in files.items():
                require(
                    backend._container().put_archive(
                        "/workspace", _archive_file(name, content.encode())
                    ),
                    "Fixture write failed",
                )
            await machine_effect(
                admin_sql,
                backend,
                "CREATE TABLE restore_customers(id uuid PRIMARY KEY,owner_id text NOT NULL,"
                "name text NOT NULL,surname text); INSERT INTO restore_customers VALUES "
                "('00000000-0000-0000-0000-000000000011','10001','Alice','Retained'),"
                "('00000000-0000-0000-0000-000000000012','10002','Bob','Private');",
            )
            await machine_effect(
                engine._command,
                backend,
                ["pnpm", "install", "--frozen-lockfile", "--ignore-scripts"],
                420,
            )
            await machine_effect(engine._command, backend, ["pnpm", "build"], 600)
            await engine._start(backend, manifest, 1)
            await machine_effect(adapter._start_boundary, state, manifest, backend, 1)
            require(load_policy(backend) is None, "Fixture must never have been restored")
            old_database_url = backend.project_database_env()["DATABASE_URL"]
            original_volumes = backend.environment_volume_names(manifest)
            record("never-restored-v2-real-next-build-two-actors-current-business-data")

            generation = uuid4()
            await manager.ensure(
                WorkspaceSpec(
                    workspace_id=workspace,
                    project_id=project,
                    owner_id=owner,
                    profile_version=settings.cell_profile_version,
                    generation_run_id=generation,
                ),
                LifecycleMutation(uuid4(), state.fencing_epoch + 1, "b" * 64),
            )
            state = manager.state_store.load(workspace)
            _, backend = adapter.parts(state)
            source = await _read_agent_workspace_files(manager, backend.workspace_volume)
            request = WorkspaceAgentBootstrapRequest(
                generation_run_id=generation,
                fencing_epoch=state.fencing_epoch,
                protect_existing_data=True,
            )
            async with manager.operation_lock.hold(workspace):
                await protect_current_database(adapter, state, request, source)
                await protect_current_database(adapter, state, request, source)
            require(protection_journal(backend)["state"] == "ready", "Protection receipt absent")
            require(load_policy(backend) is not None, "Policy absent")
            require(
                backend.environment_volume_names(manifest) == original_volumes,
                "Protection replaced current volumes",
            )
            require(
                await _read_agent_workspace_files(manager, backend.workspace_volume) == source,
                "Protection changed application source",
            )
            record("admitted-bootstrap-protection-before-first-tool-idempotent-same-volumes-source")

            # Secrets stay in private process env, never command text or evidence.
            denied_script = """
const {Client}=require('pg');
(async()=>{
 const old=new Client({connectionString:process.env.QA_OLD_DATABASE_URL,
  connectionTimeoutMillis:5000});
 let denied=false;
 try{await old.connect();}catch{denied=true;}finally{await old.end().catch(()=>{});}
 if(!denied)process.exit(11);
 const db=new Client({connectionString:process.env.DATABASE_URL});await db.connect();
 for(const sql of ['DROP TABLE restore_customers','TRUNCATE restore_customers']){
  await db.query('BEGIN');let blocked=false;
  try{await db.query(sql);}catch{blocked=true;}finally{await db.query('ROLLBACK');}
  if(!blocked)process.exit(12);
 }
 await db.end();
})().catch(()=>process.exit(13));
"""
            result = await machine_effect(
                backend._container().exec_run,
                ["node", "-e", denied_script],
                workdir="/workspace",
                environment={"QA_OLD_DATABASE_URL": old_database_url},
            )
            require(result.exit_code == 0, "Old owner login or destructive SQL was allowed")
            record("old-owner-network-login-drop-and-truncate-denied")

            async def reads(expected=1):
                url = "http://" + adapter.preview(state)[1] + ":3000"
                alice, bob = (
                    signed_cookie(adapter.secret(workspace), user) for user in ("10001", "10002")
                )
                status, body = await asyncio.to_thread(http, url, alice)
                require(
                    status == 200
                    and body["version"] == 2
                    and len(body["records"]) == expected
                    and all(row["owner_id"] == "10001" for row in body["records"])
                    and any(row["surname"] == "Retained" for row in body["records"]),
                    "Current records or retained surname lost",
                )
                status, body = await asyncio.to_thread(http, url, bob)
                require(
                    status == 200
                    and len(body["records"]) == 1
                    and body["records"][0]["name"] == "Bob",
                    "Other actor isolation failed",
                )
                return url, alice, bob

            url, alice, bob = await reads()
            status, _ = await asyncio.to_thread(
                http,
                url,
                bob,
                "PATCH",
                {"id": "00000000-0000-0000-0000-000000000011", "name": "Intrusion"},
            )
            require(status == 404, "Cross-user update allowed")
            status, _ = await asyncio.to_thread(
                http,
                url,
                alice,
                "PATCH",
                {"id": "00000000-0000-0000-0000-000000000011", "name": "Alice edited"},
            )
            require(status == 200, "Owner update failed")
            status, _ = await asyncio.to_thread(
                http,
                url,
                alice,
                "POST",
                {"id": "00000000-0000-0000-0000-000000000013", "name": "New row"},
            )
            require(status == 201, "Protected insert failed")
            await reads(2)
            actual = await machine_effect(
                admin_sql,
                backend,
                "SELECT name||':'||surname FROM restore_customers WHERE "
                "id='00000000-0000-0000-0000-000000000011';",
            )
            require(actual.strip() == b"Alice edited:Retained", "Existing data changed")
            record("signed-two-actor-read-insert-update-cross-owner-denial-retained-surname")
            await manager.release_generation(
                workspace,
                LifecycleMutation(uuid4(), state.fencing_epoch + 1, "c" * 64),
                generation_run_id=generation,
            )
            state = manager.state_store.load(workspace)
            require(state.active_generation_run_id is None, "Lease not released")
            await adapter.halt(state, retain_trusted=True)
            await adapter.resume_preview(state)
            await reads(2)
            record("release-cold-resume-retains-current-data-and-policy")
    except BaseException:
        (root / "failure.txt").write_text(traceback.format_exc())
        (root / "failure.txt").chmod(0o600)
        raise
    finally:
        if args.cleanup:
            inventory = []
            for collection, kind in (
                (client.containers, "containers"),
                (client.networks, "networks"),
                (client.volumes, "volumes"),
            ):
                kwargs = {"filters": {"label": f"omnia.workspace_id={workspace}"}}
                if kind == "containers":
                    kwargs["all"] = True
                resources = collection.list(**kwargs)
                for resource in resources:
                    ownership(resource, {workspace}, project, owner)
                inventory.append((kind, resources))
            for kind, resources in inventory:
                for resource in resources:
                    resource.remove(force=True) if kind == "containers" else resource.remove()
            record("owned-resources-cleaned")
        client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qa-parent", required=True)
    parser.add_argument("--cleanup", action="store_true", help="Clean owned resources on all exits")
    asyncio.run(run(parser.parse_args()))
