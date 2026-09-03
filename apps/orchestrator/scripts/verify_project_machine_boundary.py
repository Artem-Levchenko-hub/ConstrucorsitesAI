"""Disposable real Docker adapter/MAX core proof; no model, platform API or payments."""

# Embedded TypeScript/Python fixtures intentionally preserve literal source lines.
# ruff: noqa: E501
import argparse
import asyncio
import http.client
import json
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import docker

from omnia_orchestrator.core.cell_resources import LifecycleMutation
from omnia_orchestrator.core.project_machine import MachineManifest
from omnia_orchestrator.routers.runtime import _max_preview_bootstrap_signature
from omnia_orchestrator.services import machine_adapter
from omnia_orchestrator.services.cell_state import CellCredentialStore
from omnia_orchestrator.services.docker_machine_backend import _archive_file
from omnia_orchestrator.services.machine_environment import (
    MachineEnvironmentRef,
    MachineEnvironmentStore,
)
from omnia_orchestrator.services.machine_network_allocation import create_pool_network


def next_fixture():
    from omnia_orchestrator.services.machine_defaults import next_machine_seed

    template = Path(__file__).resolve().parents[1] / "templates" / "max-miniapp-nextjs"
    files = next_machine_seed(
        {
            path.relative_to(template).as_posix(): path.read_text(encoding="utf-8")
            for path in template.rglob("*")
            if path.is_file()
        }
    )
    files["src/app/page.tsx"] = """import { headers } from "next/headers";
import isNumber from "is-number";
import { execFileSync } from "node:child_process";
export const dynamic = "force-dynamic";
export default async function Page() {
 const identity = (await headers()).get("x-omnia-user-id");
 const helper = execFileSync("jq", ["-r", ".value"], {input:'{"value":"jq"}'}).toString().trim();
 return <main>{`machine-next:${isNumber(42)}:${helper}:${identity}`}</main>;
}
"""
    files["src/is-number.d.ts"] = (
        'declare module "is-number" {export default function isNumber(value: unknown): boolean;}\n'
    )
    files["tests/product.test.mjs"] = """import test from 'node:test';
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import isNumber from 'is-number';
test('installed library and real system helper', () => {
 assert.equal(isNumber(42), true); assert.equal(isNumber('not-a-number'), false);
 assert.equal(execFileSync('jq', ['-r','.value'], {input:'{"value":"retained"}'}).toString().trim(), 'retained');
});
"""
    return files


def request(address, path, cookie=None):
    connection = http.client.HTTPConnection(address, 3000, timeout=150)
    try:
        connection.request("GET", path, headers={"Cookie": cookie} if cookie else {})
        response = connection.getresponse()
        return (
            response.status,
            response.read(),
            response.getheader("Set-Cookie"),
            response.getheader("Location"),
        )
    finally:
        connection.close()


async def run(args):
    client = docker.from_env()
    root = Path(tempfile.mkdtemp(prefix="omnia-machine-boundary-"))
    workspace, project, owner = uuid4(), uuid4(), uuid4()
    stem = "omnia-machine-test-" + workspace.hex
    labels = {"omnia.workspace_id": str(workspace), "omnia.namespace": "test"}
    selector = {"label": [f"omnia.workspace_id={workspace}", "omnia.namespace=test"]}
    print("BOUNDARY_ARTIFACT_ROOT=" + str(root), flush=True)
    try:
        network = create_pool_network(
            client, args.pool, stem + "-internal", driver="bridge", internal=True, labels=labels
        )
        source = client.volumes.create(name=stem + "-source", labels=labels)
        credentials = CellCredentialStore(root / "credentials")
        password = credentials.load_or_create(workspace).postgres_password
        pg = client.containers.create(
            args.postgres_image,
            name=stem + "-postgres",
            labels=labels,
            network=network.name,
            detach=True,
            environment={"POSTGRES_PASSWORD": password},
            mem_limit=256 * 1024**2,
            nano_cpus=250_000_000,
        )
        pg.start()
        deadline = time.monotonic() + 60
        while pg.exec_run(["pg_isready", "-U", "postgres"]).exit_code:
            if time.monotonic() > deadline:
                raise AssertionError("test postgres readiness timeout")
            await asyncio.sleep(0.3)
        names = SimpleNamespace(
            internal_network=network.name,
            workspace_volume=source.name,
            postgres_container=pg.name,
            redis_container=stem + "-unused-redis",
        )
        state = SimpleNamespace(
            workspace_id=workspace,
            project_id=project,
            owner_id=owner,
            resource_names=names,
            active_generation_run_id=uuid4(),
            active_generation_fencing_epoch=7,
            fencing_epoch=7,
        )
        manager = SimpleNamespace(
            state_store=SimpleNamespace(root=root / "states", load=lambda _: state),
            credential_store=credentials,
            namespace="test",
            profile=SimpleNamespace(
                postgres_image=args.postgres_image,
                executor_cpu_cores=0.5,
                executor_memory_bytes=1024 * 1024**2,
                required_free_disk_bytes=4 * 1024**3,
                draft_cpu_cores=0.75,
                draft_memory_bytes=768 * 1024**2,
            ),
            docker=SimpleNamespace(_client_obj=lambda: client),
        )
        settings = SimpleNamespace(
            cell_machine_base_image=args.base_image,
            cell_machine_guard_image=args.guard_image,
            cell_network_pool=args.pool,
            cell_machine_denied_cidrs=args.deny,
        )
        adapter = machine_adapter.MachineAdapter(manager, settings)
        adapter.validate_available()
        machine_adapter.get_stack = lambda _: SimpleNamespace(image_tag=args.core_image)
        manifest_json = json.dumps(
            {
                "version": 1,
                "tasks": [
                    {"name": "seed", "role": "bootstrap", "argv": ["python3", "seed.py"]},
                    {
                        "name": "compile",
                        "role": "build",
                        "argv": ["python3", "-m", "py_compile", "server.py"],
                    },
                    {"name": "check", "role": "test", "argv": ["python3", "check.py"]},
                    {
                        "name": "quiesce",
                        "role": "quiesce",
                        "argv": ["python3", "quiesce.py"],
                        "timeout_seconds": 30,
                    },
                    {
                        "name": "restore_check",
                        "role": "restore_check",
                        "argv": ["python3", "restore_check.py"],
                        "timeout_seconds": 30,
                    },
                ],
                "services": [
                    {
                        "name": "web",
                        "argv": ["python3", "server.py"],
                        "mounts": [{"volume": "sqlite_data", "target": "/data"}],
                        "readiness": {"port": 8080, "path": "/"},
                    }
                ],
                "routes": [{"path": "/", "service": "web", "port": 8080}],
                "data_stores": [
                    {
                        "name": "sqlite",
                        "volumes": ["sqlite_data"],
                        "quiesce_task": "quiesce",
                        "restore_check_task": "restore_check",
                    }
                ],
            }
        )
        source_code = (
            "from http.server import BaseHTTPRequestHandler,HTTPServer\n"
            "class Handler(BaseHTTPRequestHandler):\n"
            " def do_GET(self):\n"
            "  self.send_response(200); self.end_headers(); "
            "self.wfile.write(('portable-python:'+self.headers.get('X-Omnia-User-ID','none')).encode())\n"
            "HTTPServer(('0.0.0.0',8080),Handler).serve_forever()\n"
        )
        check_code = (
            "import sqlite3\nfrom pathlib import Path\n"
            "db=sqlite3.connect('/data/app.sqlite')\n"
            "assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'\n"
            "assert db.execute('SELECT value FROM records WHERE id=1').fetchone()[0]=='retained-row'\n"
            "db.close()\n"
        )
        fixture_files = {
            "server.py": source_code,
            ".omnia/cell.json": manifest_json,
            "seed.py": (
                "import sqlite3\ndb=sqlite3.connect('/data/app.sqlite')\n"
                "db.execute('CREATE TABLE IF NOT EXISTS records(id INTEGER PRIMARY KEY,value TEXT)')\n"
                "db.execute(\"INSERT OR IGNORE INTO records VALUES(1,'retained-row')\")\n"
                "db.commit();db.close()\n"
            ),
            "check.py": check_code,
            "quiesce.py": (
                "import sqlite3\nfrom pathlib import Path\n"
                "db=sqlite3.connect('/data/app.sqlite');db.execute('PRAGMA wal_checkpoint(TRUNCATE)');db.close()\n"
                "Path('/data/quiesced').write_text('yes')\n"
            ),
            "restore_check.py": check_code
            + "assert Path('/data/quiesced').read_text()=='yes'\nPath('/data/restored').write_text('yes')\n",
        }
        if args.next:
            fixture_files = next_fixture()
        manifest = MachineManifest.from_files(fixture_files)
        machine, backend = adapter.parts(state)
        await machine.ensure(manifest, LifecycleMutation(uuid4(), 7, "a" * 64))
        for path, value in fixture_files.items():
            assert backend._container().put_archive(
                "/workspace", _archive_file(path, value.encode())
            )
        if args.next:
            from omnia_orchestrator.schemas.workspace import WorkspaceAgentExecRequest

            installed = await adapter.execute(
                state,
                manifest,
                WorkspaceAgentExecRequest(
                    generation_run_id=state.active_generation_run_id,
                    fencing_epoch=7,
                    expected_revision="a" * 64,
                    timeout_seconds=900,
                    cmd="pnpm add is-number@7.0.0 && apt-get -o Acquire::Retries=2 -o Acquire::http::Timeout=30 update && "
                    "DEBIAN_FRONTEND=noninteractive apt-get -o Acquire::Retries=2 -o Acquire::http::Timeout=30 "
                    "install -y --no-install-recommends jq",
                ),
            )
            assert installed.exit_code == 0, installed.output
            print("NEXT_MAIN_STACK_DEPENDENCY_AND_SYSTEM_HELPER_INSTALLED", flush=True)
        result = await adapter.apply(
            state,
            manifest,
            SimpleNamespace(
                generation_run_id=state.active_generation_run_id,
                fencing_epoch=7,
                expected_revision="a" * 64,
            ),
        )
        assert result.exit_code == 0, result.output
        print("ADAPTER_MANIFEST_BUILD_TEST_SERVICE_PASS", flush=True)
        gateway_state, address = adapter.preview(state)
        assert gateway_state == "running"
        assert request(address, "/")[0] == 401
        assert request(address, "/", "__Host-max_session=bad.bad")[0] == 401
        expires = int(time.time()) + 120

        def bootstrap(project_id, expires_at):
            signature = _max_preview_bootstrap_signature(
                adapter.secret(workspace), str(project_id), expires_at
            )
            return f"/api/omnia/preview-session?expires={expires_at}&signature={signature}"

        assert request(address, bootstrap(uuid4(), expires))[0] == 404
        assert request(address, bootstrap(project, 1))[0] == 404
        status, _body, set_cookie, location = request(address, bootstrap(project, expires))
        assert status == 307 and location == "/" and set_cookie, (status, location)
        session_cookie = set_cookie.split(";", 1)[0]
        assert "HttpOnly" in set_cookie and "Secure" in set_cookie
        status, body, _, _ = request(address, "/", session_cookie)
        expected_body = b"machine-next:true:jq:preview" if args.next else b"portable-python:preview"
        assert status == 200 and expected_body in body, (status, body[:2000])
        status, body, _, _ = request(address, "/__omnia/identity", session_cookie)
        assert status == 200 and json.loads(body)["project_id"] == str(project)
        status, body, _, _ = request(address, "/api/omnia/actions?limit=1", session_cookie)
        assert status == 200 and isinstance(json.loads(body)["actions"], list)
        # Credentials never enter generated image configuration or its mounts.
        config = backend._container().attrs["Config"]
        serialized = json.dumps(config)
        assert password not in serialized and adapter.secret(workspace) not in serialized
        print("REAL_MAX_BOOTSTRAP_REDIRECT_COOKIE_PRODUCT_IDENTITY_DB_NEGATIVES_PASS", flush=True)
        await adapter.halt(state)
        assert not backend.is_running()
        if args.next:
            image_id = backend._metadata()["environment_ref"]["image_id"]
            client.images.remove(image_id, force=True)
        await adapter.resume_preview(state)
        assert expected_body in request(adapter.preview(state)[1], "/", session_cookie)[1]
        print("BOUNDARY_REMOVE_RECREATE_PASS", flush=True)
        if args.next:
            outcome = backend._container().exec_run(["pnpm", "test"], workdir="/workspace")
            assert outcome.exit_code == 0, outcome.output
            print("NEXT_LIBRARY_HELPER_BUILD_TEST_HTTP_IMAGE_RECREATION_PASS", flush=True)
            return
        checked = backend._container().exec_run(
            ["python3", "-c", check_code + "assert Path('/data/restored').read_text()=='yes'"]
        )
        assert checked.exit_code == 0, checked.output
        good = MachineEnvironmentRef.model_validate(backend._metadata()["environment_ref"])
        assert backend._container().put_archive(
            "/workspace", _archive_file("restore_check.py", b"raise SystemExit(41)\n")
        )
        bad = await adapter.checkpoint(state)
        backend.remove()
        environments = MachineEnvironmentStore(
            adapter.root / "artifacts", workspace, backend, max_bytes=backend.disk_bytes
        )
        try:
            await asyncio.to_thread(environments.restore, bad, manifest_digest=manifest.digest())
        except Exception as exc:
            assert "restore check" in str(exc), str(exc)
        else:
            raise AssertionError("failed restore checker activated")
        assert backend._metadata()["restore_in_progress"] is True
        try:
            backend.ensure(manifest, 7)
        except Exception as exc:
            assert "incomplete" in str(exc), str(exc)
        else:
            raise AssertionError("incomplete restore started machine")
        await asyncio.to_thread(environments.restore, good, manifest_digest=manifest.digest())
        assert backend._metadata()["restore_in_progress"] is False
        print("SQLITE_ROW_QUIESCE_RESTORE_CHECK_FAILURE_FENCE_RECOVERY_PASS", flush=True)
    except BaseException:
        for container in client.containers.list(all=True, filters=selector):
            print("FIXTURE_FAILURE_STATE=" + json.dumps({"name": container.name,
                  "state": container.attrs.get("State", {})}), flush=True)
            if container.status == "running":
                result = container.exec_run(["cat", "/sys/fs/cgroup/memory.events"])
                print("FIXTURE_MEMORY_EVENTS=" + container.name + ":" +
                      result.output.decode("utf-8", errors="replace"), flush=True)
        raise
    finally:
        for container in client.containers.list(all=True, filters=selector):
            container.remove(force=True, v=True)
        for network in client.networks.list(filters=selector):
            network.remove()
        for volume in client.volumes.list(filters=selector):
            volume.remove()
        print("BOUNDARY_TEST_RESOURCES_REMOVED", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for name in ("base-image", "guard-image", "postgres-image", "core-image", "pool"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--deny", action="append", required=True)
    parser.add_argument(
        "--next",
        action="store_true",
        help="Authored default Next/TS library + system helper fixture",
    )
    asyncio.run(run(parser.parse_args()))
