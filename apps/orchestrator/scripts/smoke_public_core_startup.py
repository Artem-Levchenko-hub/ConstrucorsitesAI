"""Isolated cold MAX login proof; never contacts a real bot or production database.

Run on Linux with existing immutable images and a private --qa-parent directory.
Only this run's labelled containers/network are removed. PostgreSQL uses tmpfs.
"""
from __future__ import annotations

import argparse
import hmac
import http.client
import json
import os
import re
import secrets
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode
from uuid import uuid4


def require(value, message):
    if not value:
        raise RuntimeError(message)


def emit(event, **values):
    print(json.dumps({"event": event, **values}), flush=True)


def launch_data(token, user):
    pairs = {"auth_date": str(int(time.time())), "query_id": "qa-" + secrets.token_hex(8),
             "user": json.dumps({"id": int(user), "first_name": "QA"}, separators=(",", ":"))}
    key = hmac.digest(b"WebAppData", token.encode(), "sha256")
    pairs["hash"] = hmac.digest(
        key, "\n".join(f"{k}={v}" for k, v in sorted(pairs.items())).encode(), "sha256",
    ).hex()
    return urlencode(pairs)


def request(address, path, *, cookie="", body=None):
    connection = http.client.HTTPConnection(address, 3000, timeout=60)
    headers = {"Origin": "https://core-canary.example.test"}
    if cookie:
        headers["Cookie"] = cookie
    if body is not None:
        headers["Content-Type"] = "application/json"
    try:
        connection.request("POST" if body is not None else "GET", path,
                           body=json.dumps(body) if body is not None else None, headers=headers)
        response = connection.getresponse()
        return response.status, response.read(1024 * 1024), response.getheaders()
    finally:
        connection.close()


def run(args):
    import docker
    from docker.errors import NotFound

    from omnia_orchestrator.services import machine_adapter
    from omnia_orchestrator.services.project_machine import machine_budget

    require(os.name == "posix", "Linux required")
    parent = Path(args.qa_parent).resolve(strict=True)
    require(parent.is_dir() and parent not in (Path("/"), Path.home()),
            "dedicated QA path required")
    require(parent.stat().st_mode & 0o077 == 0, "QA parent must be private")
    for value in (args.core_image, args.postgres_image, args.guard_image):
        require(re.fullmatch(r"sha256:[0-9a-f]{64}", value), "immutable image required")
    root = Path(tempfile.mkdtemp(prefix="cold-core-", dir=parent))
    root.chmod(0o700)
    (root / "cells").mkdir(mode=0o700)
    client = docker.from_env(timeout=60)
    for value in (args.core_image, args.postgres_image, args.guard_image):
        client.images.get(value)  # No pulls or global image/tag mutation.
    run_id = uuid4().hex
    label = {"omnia.cold-core-canary": run_id}
    stem = "qa-max-core-" + run_id[:16]
    names = SimpleNamespace(internal_network=stem + "-net", postgres_container=stem + "-pg",
                            redis_container="unused-redis")
    state = SimpleNamespace(workspace_id=uuid4(), project_id=uuid4(), owner_id=uuid4(),
                            resource_names=names)
    password, token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    created, network = [], None
    core = None
    original_stack = machine_adapter.get_stack
    uploads = []

    def owned(container):
        container.reload()
        require(container.labels.get("omnia.cold-core-canary") == run_id, "ownership mismatch")
        return container

    def create(image, *command, **kwargs):
        require(kwargs.get("labels", {}).get("omnia.cold-core-canary") == run_id,
                "unlabelled container refused")
        container = client.containers.create(image, *command, **kwargs)
        created.append(container.id)
        return container

    def lookup(_collection, name, _kind):
        require(name.startswith(stem + "-"), "foreign lookup refused")
        try:
            found = owned(client.containers.get(name))
        except NotFound:
            return None
        # Observe actual writes: mtime alone cannot catch tar entries with mtime=0.
        put = found.put_archive

        def upload(path, data):
            uploads.append((name, path))
            return put(path, data)

        found.put_archive = upload
        return found

    def address(container):
        return owned(container).attrs["NetworkSettings"]["Networks"][names.internal_network][
            "IPAddress"
        ]

    def measure(phase):
        if core is None:
            return
        core.reload()
        values = {"phase": phase, "running": core.status == "running",
                  "oom_killed": core.attrs["State"]["OOMKilled"],
                  "memory_limit_mib": core.attrs["HostConfig"]["Memory"] // 1048576,
                  "cpu_limit": core.attrs["HostConfig"]["NanoCpus"] / 1_000_000_000}
        if core.status == "running":
            result = core.exec_run(["cat", "/sys/fs/cgroup/memory.current",
                                    "/sys/fs/cgroup/memory.peak", "/sys/fs/cgroup/memory.events"])
            if result.exit_code == 0:
                lines = result.output.decode().splitlines()
                values.update(current_mib=round(int(lines[0]) / 1048576, 1),
                              peak_mib=round(int(lines[1]) / 1048576, 1),
                              memory_events=dict(line.split() for line in lines[2:]))
        emit("core_memory", **values)
        require(values["running"] and not values["oom_killed"], "core stopped or OOM")

    try:
        network = client.networks.create(names.internal_network, internal=True, labels=label)
        pg = create(args.postgres_image, name=names.postgres_container, labels=label,
                    network=names.internal_network, detach=True,
                    environment={"POSTGRES_PASSWORD": password},
                    command=["postgres", "-c", "shared_buffers=32MB", "-c", "max_connections=30"],
                    tmpfs={"/var/lib/postgresql/data": "rw,nosuid,nodev,size=256m"},
                    mem_limit=256 * 1024**2, memswap_limit=256 * 1024**2, nano_cpus=150_000_000)
        pg.start()
        deadline = time.monotonic() + 60
        while pg.exec_run(["pg_isready", "-U", "postgres"]).exit_code:
            require(time.monotonic() < deadline, "QA postgres not ready")
            time.sleep(0.3)
        product = create(args.guard_image, ["python3", "-m", "http.server", "3000"],
                         name=stem + "-product", labels=label, network=names.internal_network,
                         detach=True, mem_limit=32 * 1024**2, memswap_limit=32 * 1024**2,
                         nano_cpus=50_000_000, read_only=True, cap_drop=["ALL"])
        product.start()
        manager = SimpleNamespace(
            state_store=SimpleNamespace(root=root / "cells"),
            profile=SimpleNamespace(is_v2=True, managed_core_memory_bytes=768 * 1024**2,
                                    managed_core_cpu_cores=0.2),
            credential_store=SimpleNamespace(load_or_create=lambda _: SimpleNamespace(
                postgres_password=password)),
        )
        backend = SimpleNamespace(
            client=SimpleNamespace(containers=SimpleNamespace(create=create), api=client.api,
                                   images=client.images),
            stem=stem, guard_image=args.guard_image, _lookup=lookup,
            labels=lambda kind: {**label, "kind": kind},
            address=lambda: address(product),
            # No real outbound network: signature verification is fully local.
            _network=lambda *_a, **_kw: SimpleNamespace(connect=lambda _: None),
        )
        adapter = machine_adapter.MachineAdapter(
            manager, SimpleNamespace(cell_public_core_image=args.core_image),
        )
        adapter.parts = lambda _: (SimpleNamespace(path=root / "machine.json"), backend)
        machine_adapter.get_stack = lambda _: SimpleNamespace(image_tag=args.core_image)
        manifest = SimpleNamespace(routes=[SimpleNamespace(
            model_dump=lambda: {"path": "/", "port": 3000})])
        config = {"app_name": "Cold core QA", "operator": {"legal_name": "QA", "inn": "",
                  "address": ""}, "support": {"email": None, "phone": "", "response_time": "QA"},
                  "legal": {"age_rating": "0+", "has_sales": False}}
        runtime_env = {"MAX_BOT_TOKEN": token,
                       "OMNIA_PUBLIC_APP_ORIGIN": "https://core-canary.example.test"}

        def reconcile():
            with machine_budget(300):
                adapter._start_boundary(state, manifest, backend, 1, public_mode=True,
                                        runtime_env=runtime_env, business_config_override=config)

        def login(user):
            started = time.monotonic()
            status, _, headers = request(gateway_ip, "/api/max/session",
                                         body={"initData": launch_data(token, user)})
            elapsed = round((time.monotonic() - started) * 1000)
            require(status == 200, f"valid session HTTP {status}")
            cookie = next(v.split(";", 1)[0] for k, v in headers
                          if k.lower() == "set-cookie" and v.startswith("__Host-max_session="))
            require(request(gateway_ip, "/__omnia/identity", cookie=cookie)[0] == 200,
                    "cookie identity failed")
            require(elapsed < 5000, "first ready login exceeded 5 seconds")
            return cookie, elapsed

        def worker_pid():
            # Next can restart its child for heap pressure without a Docker OOM
            # or container restart. A healthy container alone would miss that.
            processes = owned(core).top(ps_args="-eo pid,comm")["Processes"]
            workers = [row[0] for row in processes if row[1].startswith("next-server")]
            require(len(workers) == 1, "expected one Next server worker")
            return workers[0]

        for cycle in range(4):
            if cycle:
                owned(core).stop(timeout=10)
            started = time.monotonic()
            emit("cold_start", cycle=cycle)
            reconcile()
            core = lookup(None, stem + "-max-core", "")
            gateway_ip = address(lookup(None, stem + "-gateway", ""))
            # FIRST application login after controller readiness: no test warmup POST.
            cookie_a, elapsed = login("10001")
            emit("first_login", cycle=cycle, elapsed_ms=elapsed,
                 readiness_seconds=round(time.monotonic() - started, 1))
            if cycle == 0:
                status, _, _ = request(
                    gateway_ip, "/api/omnia/actions", cookie=cookie_a,
                    body={"actionType": "cold_core_qa", "payload": {"kept": True}},
                )
                require(status == 201, "action write failed")
            status, body, _ = request(gateway_ip, "/api/omnia/actions", cookie=cookie_a)
            require(status == 200 and len(json.loads(body)["actions"]) == 1,
                    "saved action lost")
            cookie_b, _ = login("10002")
            status, body, _ = request(gateway_ip, "/api/omnia/actions", cookie=cookie_b)
            require(status == 200 and json.loads(body)["actions"] == [], "cross-user leak")
            require(owned(pg).status == "running" and owned(product).status == "running",
                    "data or product process interrupted")
            measure(f"cold-{cycle}")
            if cycle == 0:
                # Falsify growing long-lived memory before spending time restarting.
                writes = len(uploads)
                initial_worker = worker_pid()
                for iteration in range(20):
                    reconcile()
                    require(len(uploads) == writes, "unchanged recovery rewrote source files")
                    require(login("10001")[0], "repeated login failed")
                    require(worker_pid() == initial_worker, "Next worker restarted during recovery")
                    if (iteration + 1) % 5 == 0:
                        measure(f"reconcile-{iteration + 1}")
                config["app_name"] = "Updated cold core QA"
                config["operator"]["legal_name"] = "Updated QA owner"
                config["support"]["response_time"] = "Updated QA support"
                reconcile()
                require(worker_pid() == initial_worker, "metadata update restarted core")
                status, body, _ = request(gateway_ip, "/api/omnia/config")
                require(status == 200 and json.loads(body) == config, "metadata readback failed")
                for path, expected in (("/support", b"Updated QA support"),
                                       ("/legal/privacy", b"Updated QA owner"),
                                       ("/legal/terms", b"Updated cold core QA")):
                    status, body, _ = request(gateway_ip, path)
                    require(status == 200 and expected in body, "legal metadata stale")
                    require(b"Updated cold core QA" in body, "legal page title stale")
                emit("metadata_update", applied=True, restarted=False)
        emit("passed", cold_logins=4, reconciliations=20, persistence=True, cross_user_denied=True)
    except Exception as error:
        emit("failed", error_type=type(error).__name__,
             reason=str(error) if type(error) is RuntimeError else "runtime operation failed")
        if core is None:
            try:
                core = lookup(None, stem + "-max-core", "")
            except Exception:
                pass
        try:
            measure("failure")
        except Exception:
            pass
        raise SystemExit(1) from None
    finally:
        machine_adapter.get_stack = original_stack
        for container_id in reversed(created):
            try:
                owned(client.containers.get(container_id)).remove(force=True, v=True)
            except NotFound:
                pass
        if network is not None:
            network.reload()
            require(network.attrs.get("Labels", {}).get("omnia.cold-core-canary") == run_id,
                    "network ownership mismatch")
            network.remove()
        client.close()
        emit("cleanup_complete")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("core-image", "postgres-image", "guard-image", "qa-parent"):
        parser.add_argument("--" + name, required=True)
    run(parser.parse_args())
