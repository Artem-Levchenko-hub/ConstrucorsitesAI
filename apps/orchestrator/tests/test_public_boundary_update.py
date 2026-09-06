"""A controller update must reach already-running public gateways."""
import json
from types import SimpleNamespace
from uuid import uuid4

from omnia_orchestrator.services import machine_business_config
from omnia_orchestrator.services.machine_adapter import MachineAdapter


def test_public_gateway_reuses_current_code_and_replaces_outdated_code(tmp_path, monkeypatch):
    adapter = MachineAdapter(
        SimpleNamespace(state_store=SimpleNamespace(root=tmp_path / "cells")), SimpleNamespace(),
    )
    state = SimpleNamespace(
        workspace_id=uuid4(), project_id=uuid4(), owner_id=uuid4(),
        resource_names=SimpleNamespace(internal_network="isolated-public-test"),
    )
    network = {"isolated-public-test": {"IPAddress": "127.0.0.1"}}
    core = SimpleNamespace(
        status="running", reload=lambda: None,
        attrs={"NetworkSettings": {"Networks": network}},
    )
    containers = {"public-test-max-core": core}
    delivered = []
    removed = []

    def create(_image, _command, **kwargs):
        # No product or database container may be recreated by a gateway update.
        assert kwargs["name"] == "public-test-gateway"

        def remove(**_):
            removed.append(containers.pop("public-test-gateway"))

        gateway = SimpleNamespace(
            id="gateway-test", status="running", start=lambda: None, reload=lambda: None,
            remove=remove, attrs={"NetworkSettings": {"Networks": network}},
        )
        containers["public-test-gateway"] = gateway
        return gateway

    transport = SimpleNamespace(
        settimeout=lambda _: None,
        sendall=lambda value: delivered.append(json.loads(value)),
        shutdown=lambda _: None, recv=lambda _: b"",
    )
    client = SimpleNamespace(
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
    manifest = SimpleNamespace(routes=[SimpleNamespace(
        model_dump=lambda: {"path": "/", "port": 8080},
    )])
    monkeypatch.setattr(adapter, "_public_auth_secret", lambda *_: "same-test-secret")
    monkeypatch.setattr(adapter, "_wait_http", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(adapter, "parts", lambda _: (
        SimpleNamespace(path=tmp_path / "machine.json"), backend,
    ))
    monkeypatch.setattr(machine_business_config, "apply_public_core_overlay", lambda _: None)
    monkeypatch.setattr(machine_business_config, "boundary_source", lambda: "first trusted server")

    adapter._start_boundary(state, manifest, backend, 7, public_mode=True)
    first = containers["public-test-gateway"]
    assert delivered[-1]["server"] == "first trusted server"
    adapter._start_boundary(state, manifest, backend, 7, public_mode=True)
    assert containers["public-test-gateway"] is first
    assert len(delivered) == 1 and not removed

    monkeypatch.setattr(
        machine_business_config, "boundary_source", lambda: "updated trusted server",
    )
    adapter._start_boundary(state, manifest, backend, 7, public_mode=True)
    assert containers["public-test-gateway"] is not first
    assert delivered[-1]["server"] == "updated trusted server"
    assert delivered[-1]["config"] == delivered[0]["config"]
    assert removed == [first]
    assert containers["public-test-max-core"] is core
