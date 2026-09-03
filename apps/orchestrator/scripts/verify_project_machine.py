"""Explicit disposable Linux Docker acceptance; no model calls or production env.

Run with immutable --base-image/--guard-image IDs and an inventory-checked --pool.
Every created resource is labelled test and bound to newly allocated UUIDs.
"""

# Embedded cross-language fixture source is kept readable as authored strings.
# ruff: noqa: E501
import argparse
import asyncio
import hashlib
import json
import tempfile
import time
import urllib.request
from pathlib import Path
from uuid import uuid4

import docker

from omnia_orchestrator.core.cell_resources import LifecycleMutation
from omnia_orchestrator.core.project_machine import MachineManifest
from omnia_orchestrator.services.docker_machine_backend import DockerMachineBackend, _archive_file
from omnia_orchestrator.services.machine_environment import MachineEnvironmentStore
from omnia_orchestrator.services.machine_network_allocation import create_pool_network
from omnia_orchestrator.services.machine_services import MachineServices
from omnia_orchestrator.services.project_machine import ProjectMachine


async def command(machine, argv, epoch=1, timeout_seconds=600):
    mutation = LifecycleMutation(
        uuid4(), epoch, hashlib.sha256(json.dumps(argv).encode()).hexdigest()
    )
    operation = await machine.exec_start(argv, ".", mutation)
    started = time.monotonic()
    while time.monotonic() - started < timeout_seconds:
        result = await machine.exec_status(operation, mutation)
        if result.state == "completed":
            print(
                json.dumps(
                    {"command": argv[:2], "exit": result.exit_code, "output": result.output[-4000:]}
                ),
                flush=True,
            )
            return result
        await asyncio.sleep(0.3)
    await machine.cancel(mutation)
    raise AssertionError("command deadline exceeded")


def read_http(url):
    with urllib.request.urlopen(url, timeout=5) as response:
        return response.read()


async def run(args):
    client = docker.from_env()
    created = []
    root = Path(tempfile.mkdtemp(prefix="omnia-machine-acceptance-"))
    print("ARTIFACT_ROOT=" + str(root), flush=True)
    try:
        for stack in ("python",) if args.smoke else ("python", "javascript", "compiled"):
            workspace = uuid4()
            stem = "omnia-machine-test-" + workspace.hex
            labels = {
                "omnia.managed": "true",
                "omnia.project_machine": "true",
                "omnia.workspace_id": str(workspace),
                "omnia.namespace": "test",
            }
            created.append((workspace, None))
            network = create_pool_network(
                client,
                args.pool,
                stem + "-internal",
                driver="bridge",
                internal=True,
                labels=labels,
            )
            volume = client.volumes.create(name=stem + "-source", labels=labels)
            backend = DockerMachineBackend(
                client=client,
                workspace_id=workspace,
                project_id=uuid4(),
                owner_id=uuid4(),
                root=root,
                internal_network=network.name,
                workspace_volume=volume.name,
                base_image=args.base_image,
                guard_image=args.guard_image,
                network_pool=args.pool,
                denied_cidrs=tuple(args.deny),
                cpu_cores=0.75,
                memory_bytes=512 * 1024**2,
                disk_bytes=3 * 1024**3,
                pids=192,
                namespace="test",
            )
            lease = {"epoch": 1}
            machine = ProjectMachine(root, workspace, backend, lease_epoch=lambda lease=lease: lease["epoch"])
            manifest = MachineManifest.model_validate(
                {
                    "version": 1,
                    "services": [
                        {
                            "name": "api",
                            "argv": ["python3", "-m", "http.server", "8080"],
                            "readiness": {"port": 8080, "path": "/"},
                        }
                    ],
                    "routes": [{"path": "/", "service": "api", "port": 8080}],
                }
            )
            mutation = LifecycleMutation(uuid4(), 1, "a" * 64)
            await machine.ensure(manifest, mutation)
            probe = await command(
                machine,
                [
                    "python3",
                    "-c",
                    "import urllib.request; print(urllib.request.urlopen('https://pypi.org/simple/', "
                    "timeout=20).status)",
                ],
            )
            assert probe.exit_code == 0, probe.output
            denied = await command(
                machine,
                [
                    "python3",
                    "-c",
                    "import urllib.request,sys; targets=['http://169.254.169.254/', "
                    "'http://170.168.72.200/', 'http://127.0.0.1:9999/']; "
                    'exec("for target in targets:\\n try: urllib.request.urlopen(target, timeout=2); '
                    'sys.exit(3)\\n except OSError: pass"); '
                    "print('private and host proxy destinations denied')",
                ],
            )
            assert denied.exit_code == 0, denied.output
            direct = await command(
                machine,
                [
                    "python3",
                    "-c",
                    "import socket,sys; s=socket.socket(); s.settimeout(2); "
                    "code=s.connect_ex(('1.1.1.1',443)); print('direct=',code); sys.exit(code==0)",
                ],
            )
            assert direct.exit_code == 0, direct.output
            if args.smoke:
                continue
            if stack == "python":
                install = ["sh", "-c", "python3 -m venv .venv && .venv/bin/pip install flask"]
                service = [".venv/bin/python", "server.py"]
                source = (
                    "from flask import Flask\napp=Flask(__name__)\n"
                    "@app.get('/')\ndef index(): return 'python-flask-machine'\n"
                    "app.run(host='0.0.0.0', port=8080)\n"
                )
                check = [".venv/bin/python", "-c", "import flask; print(flask.__file__)"]
            elif stack == "javascript":
                install = [
                    "sh",
                    "-c",
                    "sed -i 's|http://deb.debian.org|https://deb.debian.org|g' "
                    "/etc/apt/sources.list.d/debian.sources && apt-get -o Acquire::https::Timeout=30 update "
                    "&& DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends nodejs npm jq "
                    "&& npm install is-number",
                ]
                service = ["node", "server.js"]
                source = (
                    "const http=require('http'), n=require('is-number');"
                    "http.createServer((q,r)=>r.end('javascript-'+n(42)))"
                    ".listen(8080,'0.0.0.0');"
                )
                check = [
                    "sh",
                    "-c",
                    "node -e \"console.log(require('is-number')(42))\" && jq --version",
                ]
            else:
                install = [
                    "sh",
                    "-c",
                    "sed -i 's|http://deb.debian.org|https://deb.debian.org|g' "
                    "/etc/apt/sources.list.d/debian.sources && apt-get -o Acquire::https::Timeout=30 update "
                    "&& DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends gcc libc6-dev "
                    "&& printf '#include <stdio.h>\nint main(){puts(\"compiled-machine\");}' "
                    "> hello.c && gcc hello.c -o hello && ./hello",
                ]
                service = ["python3", "-m", "http.server", "8080"]
                source = "compiled-machine"
                check = ["./hello"]
            manifest.tasks = []
            manifest.services[0].argv = service
            machine_obj = backend._container()
            filename = (
                "server.py"
                if stack == "python"
                else "server.js"
                if stack == "javascript"
                else "index.html"
            )
            machine_obj.put_archive("/workspace", _archive_file(filename, source.encode()))
            installed = await command(machine, install, timeout_seconds=900)
            assert installed.exit_code == 0, installed.output
            assert (await command(machine, check)).exit_code == 0
            failure = await command(
                machine, ["sh", "-c", "echo intentional-installer-failure >&2; exit 37"]
            )
            assert failure.exit_code == 37 and "intentional-installer-failure" in failure.output
            await machine.ensure(manifest, mutation)
            supervisor = MachineServices(machine, backend)
            statuses = await supervisor.reconcile(manifest, mutation)
            assert all(status.ready for status in statuses)
            response = await asyncio.to_thread(read_http, f"http://{backend.address()}:8080/")
            print(json.dumps({"stack": stack, "http": response.decode()[:200]}), flush=True)
            environments = MachineEnvironmentStore(
                root / "artifacts", workspace, backend, max_bytes=3 * 1024**3
            )
            reference = await asyncio.to_thread(
                environments.capture,
                manifest_digest=manifest.digest(),
                base_image=args.base_image,
                volumes=tuple(backend.volume_mapping(manifest)),
                manifest=manifest,
            )
            backend.remove()
            client.images.remove(reference.image_id)
            await asyncio.to_thread(
                environments.restore, reference, manifest_digest=manifest.digest()
            )
            await machine.ensure(manifest, mutation)
            assert (await command(machine, check)).exit_code == 0
            await supervisor.reconcile(manifest, mutation)
            response = await asyncio.to_thread(read_http, f"http://{backend.address()}:8080/")
            assert response
            print(
                json.dumps(
                    {
                        "stack": stack,
                        "recreated_image": reference.image_id,
                        "artifact_sha256": reference.sha256,
                        "http": response.decode()[:200],
                    }
                ),
                flush=True,
            )
        print("MACHINE_ACCEPTANCE_PASS", flush=True)
    finally:
        # Exact generated UUID labels and names only; never a daemon-wide prune.
        for workspace, _backend in created:
            selector = {"label": [f"omnia.workspace_id={workspace}", "omnia.namespace=test"]}
            for container in client.containers.list(all=True, filters=selector):
                container.remove(force=True)
            for network in client.networks.list(filters=selector):
                network.remove()
            for volume in client.volumes.list(filters=selector):
                volume.remove()
        print("TEST_RESOURCES_REMOVED; artifacts retained at " + str(root), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-image", required=True)
    parser.add_argument("--guard-image", required=True)
    parser.add_argument("--pool", required=True)
    parser.add_argument("--deny", action="append", required=True)
    parser.add_argument("--smoke", action="store_true")
    asyncio.run(run(parser.parse_args()))
