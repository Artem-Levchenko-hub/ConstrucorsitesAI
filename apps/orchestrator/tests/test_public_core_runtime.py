"""An outdated public core is replaced once without rotating auth or touching data."""
from types import SimpleNamespace
from uuid import uuid4

import pytest

from omnia_orchestrator.services.machine_adapter import _PUBLIC_CORE_COMMAND, MachineAdapter


@pytest.mark.parametrize("old_runtime", ["dev-command", "image"])
@pytest.mark.parametrize("public_mode", [True, False])
def test_public_core_migrates_runtime_once_preserving_signing_key_and_data(
    tmp_path, monkeypatch, old_runtime, public_mode,
):
    password = "disposable-qa-password"
    manager = SimpleNamespace(
        state_store=SimpleNamespace(root=tmp_path / "cells"),
        profile=SimpleNamespace(is_v2=True, managed_core_memory_bytes=768 * 1024**2,
                                managed_core_cpu_cores=0.2),
        credential_store=SimpleNamespace(load_or_create=lambda _: SimpleNamespace(
            postgres_password=password)),
    )
    image_id = "sha256:" + "a" * 64
    adapter = MachineAdapter(manager, SimpleNamespace(
        cell_public_core_image=image_id, cell_preview_core_image=image_id,
    ))
    state = SimpleNamespace(
        workspace_id=uuid4(), project_id=uuid4(), owner_id=uuid4(),
        resource_names=SimpleNamespace(internal_network="qa-internal", postgres_container="qa-pg",
                                       redis_container="qa-redis"),
    )
    secret = adapter.secret(state.workspace_id)
    env = {"MAX_BOT_TOKEN": "qa-bot", "OMNIA_PUBLIC_APP_ORIGIN": "https://app.example.test"}
    containers = {"qa-product": object(), "qa-pg": object()}
    backend = SimpleNamespace(stem="qa", client=SimpleNamespace(containers=containers))
    backend._lookup = lambda _, name, _kind: containers.get(name)
    if public_mode:
        assert adapter._public_auth_secret(state, backend, env) == secret
    removed, created = [], []

    def core(config):
        def remove(**_):
            removed.append(containers.pop("qa-max-core"))

        return SimpleNamespace(
            attrs={"Config": config, "Image": image_id},
            status="running", reload=lambda: None, remove=remove,
        )

    original = core({"Env": ["AUTH_SECRET=" + secret], "Cmd": ["sh", "./docker-entrypoint.sh"]})
    if old_runtime == "image":
        original.attrs["Image"] = "sha256:" + "b" * 64
        original.attrs["Config"] = {"Env": ["NODE_OPTIONS=--max-old-space-size=384"],
                                     "Cmd": list(_PUBLIC_CORE_COMMAND)}
    containers["qa-max-core"] = original

    def create(_image, **kwargs):
        assert kwargs["name"] == "qa-max-core" and _image == image_id
        created.append(kwargs)
        replacement = core({"Env": [f"{k}={v}" for k, v in kwargs["environment"].items()],
                            "Cmd": kwargs["command"], "Labels": kwargs["labels"]})
        containers["qa-max-core"] = replacement
        return replacement

    backend.client = SimpleNamespace(
        containers=SimpleNamespace(create=create),
        images=SimpleNamespace(get=lambda _: SimpleNamespace(
            id=image_id, labels={"omnia.max-core.protocol": "1",
                                 "omnia.max-core.preview-protocol": "1"})),
    )
    backend.labels = lambda kind: {"kind": kind}
    backend._network = lambda *_a, **_kw: SimpleNamespace(connect=lambda _: None)
    # Stop before HTTP startup; the real cold-run canary exercises that later boundary.
    class ObserveStartup(Exception):
        pass

    def after_reload():
        raise ObserveStartup

    original.reload = after_reload
    network = {"qa-internal": {"IPAddress": "127.0.0.1"}}
    from omnia_orchestrator.services import machine_business_config

    monkeypatch.setattr(machine_business_config, "apply_public_core_overlay",
                        lambda _: (_ for _ in ()).throw(ObserveStartup()))
    original.attrs["NetworkSettings"] = {"Networks": network}
    old_create = create

    def create_ready(*args, **kwargs):
        result = old_create(*args, **kwargs)
        result.attrs["NetworkSettings"] = {"Networks": network}
        return result

    backend.client.containers.create = create_ready
    product, pg = containers["qa-product"], containers["qa-pg"]
    for _ in range(2):
        with pytest.raises(ObserveStartup):
            adapter._start_boundary(
                state, None, backend, 1, public_mode=public_mode,
                runtime_env=env if public_mode else None,
            )
    assert removed == [original] and len(created) == 1
    assert created[0]["mem_limit"] == 768 * 1024**2
    assert created[0]["nano_cpus"] == 200_000_000
    assert created[0]["environment"]["NODE_OPTIONS"] == "--max-old-space-size=384"
    assert created[0]["environment"]["AUTH_SECRET"] == secret
    assert created[0]["environment"]["NODE_ENV"] == "production"
    assert created[0]["command"][-1].endswith("exec node server.js")
    assert adapter.secret(state.workspace_id) == secret
    assert containers["qa-product"] is product and containers["qa-pg"] is pg
    if not public_mode:
        assert created[0]["environment"]["OMNIA_OWNER_PREVIEW"] == "1"
        assert "MAX_BOT_TOKEN" not in created[0]["environment"]
        assert "OMNIA_PUBLIC_APP_ORIGIN" not in created[0]["environment"]


@pytest.mark.parametrize("image_ref, protocol", [("", "1"), ("untrusted:latest", "1"),
                                               ("sha256:" + "a" * 64, "0")])
def test_invalid_public_image_is_rejected_before_changing_live_auth(image_ref, protocol):
    from omnia_orchestrator.core.cell_resources import CellResourceError

    adapter = MachineAdapter(SimpleNamespace(), SimpleNamespace(cell_public_core_image=image_ref))

    def no_auth_changes(*_):
        pytest.fail("must validate the image before changing live auth")

    adapter._public_auth_secret = no_auth_changes
    backend = SimpleNamespace(client=SimpleNamespace(images=SimpleNamespace(
        get=lambda _: SimpleNamespace(id=image_ref, labels={"omnia.max-core.protocol": protocol}))))
    with pytest.raises(CellResourceError, match="image"):
        adapter._start_boundary(SimpleNamespace(resource_names=None), None, backend, 1,
                                public_mode=True)


@pytest.mark.parametrize("image_ref, protocol", [
    ("untrusted:latest", "1"), ("sha256:" + "a" * 64, "0"),
])
def test_preview_rejects_unpinned_or_incompatible_image_before_any_runtime_change(
    image_ref, protocol,
):
    from omnia_orchestrator.core.cell_resources import CellResourceError

    adapter = MachineAdapter(SimpleNamespace(), SimpleNamespace(cell_preview_core_image=image_ref))
    adapter.secret = lambda _: pytest.fail("must validate image before accessing live secrets")
    backend = SimpleNamespace(client=SimpleNamespace(images=SimpleNamespace(
        get=lambda _: SimpleNamespace(id=image_ref, labels={
            "omnia.max-core.protocol": "1", "omnia.max-core.preview-protocol": protocol,
        }))))
    with pytest.raises(CellResourceError, match="image"):
        adapter._start_boundary(SimpleNamespace(resource_names=None), None, backend, 1)


def test_draft_core_receives_current_trusted_routes_before_serving(tmp_path, monkeypatch):
    from omnia_orchestrator.services import machine_business_config

    manager = SimpleNamespace(state_store=SimpleNamespace(root=tmp_path))
    adapter = MachineAdapter(manager, SimpleNamespace())
    state = SimpleNamespace(workspace_id=uuid4(), resource_names=SimpleNamespace(
        internal_network="draft-network"))
    core = SimpleNamespace(status="running", reload=lambda: None, attrs={
        "NetworkSettings": {"Networks": {"draft-network": {"IPAddress": "127.0.0.1"}}}})
    backend = SimpleNamespace(client=SimpleNamespace(containers=None), stem="draft",
                              _lookup=lambda *_: core)

    class OverlayReached(Exception):
        pass

    def overlay(target):
        assert target is core
        raise OverlayReached

    monkeypatch.setattr(machine_business_config, "apply_public_core_overlay", overlay)
    monkeypatch.setattr(adapter, "_wait_http", lambda *_a, **_kw: pytest.fail(
        "draft served before trusted integration routes were updated"))
    with pytest.raises(OverlayReached):
        adapter._start_boundary(state, None, backend, 1)


@pytest.mark.parametrize("core_state", ["absent", "exited", "outdated", "running"])
@pytest.mark.parametrize("digest_reference", [False, True])
def test_preview_recovers_missing_stopped_or_outdated_core(
    core_state, monkeypatch, digest_reference,
):
    image_id = "sha256:" + "a" * 64
    image_ref = "registry.example/core@sha256:" + "b" * 64 if digest_reference else image_id
    adapter = MachineAdapter(SimpleNamespace(), SimpleNamespace(cell_preview_core_image=image_ref))
    gateway = SimpleNamespace(status="running", reload=lambda: None, attrs={
        "NetworkSettings": {"Networks": {"internal": {"IPAddress": "10.253.0.3"}}},
    })
    core = None if core_state == "absent" else SimpleNamespace(
        status="exited" if core_state == "exited" else "running", reload=lambda: None,
        attrs={"Image": image_id if core_state != "outdated" else "sha256:" + "b" * 64},
    )
    backend = SimpleNamespace(
        client=SimpleNamespace(containers=None, images=SimpleNamespace(
            get=lambda _: SimpleNamespace(id=image_id),
        )), stem="qa", internal_network="internal",
        _lookup=lambda _, _name, kind: gateway if kind == "max-gateway" else core,
    )
    monkeypatch.setattr(adapter, "exists", lambda _: True)
    monkeypatch.setattr(adapter, "parts", lambda _: (None, backend))
    result = adapter.preview(SimpleNamespace(workspace_id=uuid4()))
    assert result == ("running" if core_state == "running" else "stopped", "10.253.0.3")
