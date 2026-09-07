"""A controller update must reach already-running public gateways."""
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from omnia_orchestrator.services import machine_business_config
from omnia_orchestrator.services.machine_adapter import _PUBLIC_CORE_COMMAND, MachineAdapter


@pytest.mark.parametrize("configured", [True, False])
@pytest.mark.parametrize("public_mode", [True, False])
@pytest.mark.parametrize("fault", ["restart", "replacement", "epoch", "machine_ip", "routes",
                                 "stamp_missing", "stamp_digest", "host_config", "health",
                                 "budget", "cancelled"])
def test_public_gateway_reuses_current_code_and_replaces_outdated_code(
    tmp_path, monkeypatch, configured, public_mode, fault,
):
    image_id = "sha256:" + "a" * 64
    adapter = MachineAdapter(
        SimpleNamespace(
            state_store=SimpleNamespace(root=tmp_path / "cells"),
            profile=SimpleNamespace(is_v2=True, managed_core_memory_bytes=768 * 1024**2),
        ),
        SimpleNamespace(cell_public_core_image=image_id, cell_preview_core_image=image_id),
    )
    state = SimpleNamespace(
        workspace_id=uuid4(), project_id=uuid4(), owner_id=uuid4(),
        resource_names=SimpleNamespace(internal_network="isolated-public-test"),
    )
    network = {"isolated-public-test": {"IPAddress": "127.0.0.1"}}
    core = SimpleNamespace(
        status="running", reload=lambda: None,
        attrs={"NetworkSettings": {"Networks": network}, "Image": image_id,
               "Config": {"Env": ["NODE_OPTIONS=--max-old-space-size=384",
                                  "OMNIA_OWNER_PREVIEW=1", "AUTH_SECRET=same-test-secret",
                                  "OMNIA_PROJECT_ID=" + str(state.project_id)],
                          "Cmd": list(_PUBLIC_CORE_COMMAND)}},
    )
    containers = {"public-test-max-core": core}
    delivered = []
    removed = []
    readiness = []

    def create(_image, _command, **kwargs):
        # No product or database container may be recreated by a gateway update.
        assert kwargs["name"] == "public-test-gateway"
        expected = 401 if configured else 503
        if public_mode:
            assert ("POST", "/api/max/session", expected, b'{"initData":""}') in readiness
            assert ("GET", "/api/omnia/actions", 401, None) in readiness

        def remove(**_):
            removed.append(containers.pop("public-test-gateway"))

        gateway = SimpleNamespace(
            id="gateway-test", status="running", start=lambda: None, reload=lambda: None,
            remove=remove, attrs={"NetworkSettings": {"Networks": network},
                                  "State": {"StartedAt": "2026-09-07T00:00:00Z"},
                                  "Image": image_id, "HostConfig": {"Privileged": False},
                                  "Mounts": [], "Config": {"Labels": kwargs["labels"]}},
        )
        containers["public-test-gateway"] = gateway
        return gateway

    transport = SimpleNamespace(
        settimeout=lambda _: None,
        sendall=lambda value: delivered.append(json.loads(value)),
        shutdown=lambda _: None, recv=lambda _: b"",
    )
    client = SimpleNamespace(
        images=SimpleNamespace(get=lambda _: SimpleNamespace(
            id=image_id, labels={"omnia.max-core.protocol": "1",
                                 "omnia.max-core.preview-protocol": "1"})),
        containers=SimpleNamespace(create=create),
        api=SimpleNamespace(
            exec_create=lambda *_args, **_kwargs: {"Id": "upload"},
            exec_start=lambda *_args, **_kwargs: SimpleNamespace(
                _sock=transport, close=lambda: None,
            ),
            exec_inspect=lambda _: {"Running": False, "ExitCode": 0},
        ),
    )
    backend = SimpleNamespace(
        client=client, stem="public-test", guard_image="pinned-guard",
        _lookup=lambda _, name, _kind: containers.get(name),
        address=lambda: "127.0.0.2", labels=lambda kind: {"kind": kind},
    )
    from omnia_orchestrator.services.docker_machine_backend import DockerMachineBackend

    backend.trusted_container_identity = lambda item, kind: (
        DockerMachineBackend.trusted_container_identity(backend, item, kind)
    )
    manifest = SimpleNamespace(routes=[SimpleNamespace(
        model_dump=lambda: {"path": "/", "port": 8080},
    )])
    monkeypatch.setattr(adapter, "_public_auth_secret", lambda *_: "same-test-secret")
    monkeypatch.setattr(adapter, "secret", lambda *_: "same-test-secret")
    def ready(_core, _address, path, **kwargs):
        readiness.append((kwargs.get("method", "GET"), path, kwargs["expected"],
                          kwargs.get("body")))

    monkeypatch.setattr(adapter, "_wait_http", ready)
    monkeypatch.setattr(adapter, "parts", lambda _: (
        SimpleNamespace(path=tmp_path / "machine.json"), backend,
    ))
    monkeypatch.setattr(machine_business_config, "apply_public_core_overlay", lambda _: None)
    monkeypatch.setattr(machine_business_config, "boundary_source", lambda: "first trusted server")

    runtime_env = {"OMNIA_PUBLIC_APP_ORIGIN": "https://app.example.test"}
    if configured:
        runtime_env["MAX_BOT_TOKEN"] = "disposable-test-bot"
    adapter._start_boundary(state, manifest, backend, 7, public_mode=public_mode,
                            runtime_env=runtime_env if public_mode else None)
    first = containers["public-test-gateway"]
    if public_mode:
        assert delivered[-1]["server"] == "first trusted server"
        assert delivered[-1]["config"]["public_origin"] == runtime_env["OMNIA_PUBLIC_APP_ORIGIN"]
    adapter._start_boundary(state, manifest, backend, 7, public_mode=public_mode,
                            runtime_env=runtime_env if public_mode else None)
    assert containers["public-test-gateway"] is first
    assert len(delivered) == 1 and not removed

    monkeypatch.setattr(
        machine_business_config, "boundary_source", lambda: "updated trusted server",
    )
    adapter._start_boundary(state, manifest, backend, 7, public_mode=public_mode,
                            runtime_env=runtime_env if public_mode else None)
    assert containers["public-test-gateway"] is not first
    if public_mode:
        assert delivered[-1]["server"] == "updated trusted server"
        assert delivered[-1]["config"] == delivered[0]["config"]
    assert removed == [first]
    assert containers["public-test-max-core"] is core

    # Any mismatch must replace ingress; a successful HTTP probe alone cannot
    # prove which configuration/code the surviving gateway actually serves.
    restarted = containers["public-test-gateway"]
    epoch = 7
    if fault in {"health", "budget", "cancelled"}:
        import asyncio

        from omnia_orchestrator.core.cell_resources import CellResourceError

        error_type = {"health": CellResourceError, "budget": TimeoutError,
                      "cancelled": asyncio.CancelledError}[fault]

        def unhealthy_old(item, address, path, **kwargs):
            if item is restarted and path == "/__omnia/identity":
                raise error_type("reuse health failed")
            ready(item, address, path, **kwargs)

        monkeypatch.setattr(adapter, "_wait_http", unhealthy_old)
        if fault != "health":
            with pytest.raises(error_type, match="reuse health failed"):
                adapter._start_boundary(state, manifest, backend, epoch, public_mode=public_mode,
                                        runtime_env=runtime_env if public_mode else None)
            assert containers["public-test-gateway"] is restarted
            assert containers["public-test-max-core"] is core
            return
    elif fault == "restart":
        restarted.attrs["State"]["StartedAt"] = "2026-09-07T01:00:00Z"
    elif fault == "replacement":
        restarted.id = "replacement"
    elif fault == "epoch":
        epoch = 8
    elif fault == "machine_ip":
        backend.address = lambda: "127.0.0.3"
    elif fault == "routes":
        manifest.routes.append(SimpleNamespace(model_dump=lambda: {"path": "/api", "port": 8081}))
    elif fault == "host_config":
        restarted.attrs["HostConfig"]["Memory"] = 64 * 1024**2
    else:
        stamp = adapter.root / (
            "public-boundary-runtime" if public_mode else "owner-boundary-runtime"
        ) / f"{state.workspace_id}.json"
        if fault == "stamp_missing":
            stamp.unlink()
        else:
            stamp.write_text('{"digest":"stale"}', encoding="utf-8")
    adapter._start_boundary(state, manifest, backend, epoch, public_mode=public_mode,
                            runtime_env=runtime_env if public_mode else None)
    assert containers["public-test-gateway"] is not restarted
    assert containers["public-test-max-core"] is core
