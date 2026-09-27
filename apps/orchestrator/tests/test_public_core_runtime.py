"""Trusted core role upgrade preserves serving core until replacement is verified."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from yleum_orchestrator.core.cell_resources import CellResourceError
from yleum_orchestrator.services.machine_adapter import MachineAdapter


@pytest.mark.parametrize("public_mode", [True, False])
@pytest.mark.parametrize("failure", ["bootstrap", "health", "config", "auth", None])
def test_core_upgrade_is_staged_and_preserves_project_data(
    tmp_path, monkeypatch, public_mode, failure
):
    from yleum_orchestrator.services import machine_business_config as config

    image = "sha256:" + "a" * 64
    events, items, creates = [], {}, []
    network = {"qa-network": {"IPAddress": "127.0.0.1"}}
    manager = SimpleNamespace(
        state_store=SimpleNamespace(root=tmp_path / "cells"),
        profile=SimpleNamespace(
            is_v2=True, managed_core_memory_bytes=768 * 1024**2, managed_core_cpu_cores=0.2
        ),
    )
    adapter = MachineAdapter(
        manager, SimpleNamespace(cell_preview_core_image=image, cell_public_core_image=image)
    )
    state = SimpleNamespace(
        workspace_id=uuid4(),
        owner_id=uuid4(),
        project_id=uuid4(),
        resource_names=SimpleNamespace(
            internal_network="qa-network", postgres_container="qa-pg", redis_container="qa-redis"
        ),
    )
    secret = adapter.secret(state.workspace_id)

    class Container:
        def __init__(self, name, env=None, labels=None, command=None):
            self.name, self.id, self.status = name, name, "running"
            self.attrs = {
                "Image": image,
                "Config": {
                    "Env": [f"{k}={v}" for k, v in (env or {}).items()],
                    "Labels": labels or {},
                    "Cmd": command,
                },
                "NetworkSettings": {"Networks": network},
            }
            items[name] = self

        def reload(self):
            pass

        def start(self):
            events.append("start:" + self.name)

        def update(self, **_):
            pass

        def rename(self, name):
            events.append("rename:" + self.name)
            items.pop(self.name)
            self.name = name
            items[name] = self

        def remove(self, **_):
            events.append("remove:" + self.name)
            items.pop(self.name)

    old = Container("qa-max-core", {"AUTH_SECRET": secret})
    gateway = Container("qa-gateway")
    product, pg = object(), object()
    items.update({"qa-product": product, "qa-pg": pg})

    def create(_image, *args, **kwargs):
        creates.append(kwargs)
        return Container(
            kwargs["name"], kwargs.get("environment"), kwargs["labels"], kwargs.get("command", args)
        )

    sock = SimpleNamespace(
        settimeout=lambda _: None,
        sendall=lambda _: None,
        shutdown=lambda _: None,
        recv=lambda _: b"",
        close=lambda: None,
    )
    client = SimpleNamespace(
        containers=SimpleNamespace(create=create),
        images=SimpleNamespace(
            get=lambda _: SimpleNamespace(
                id=image,
                labels={
                    "omnia.max-core.protocol": "1",
                    "omnia.max-core.preview-protocol": "1",
                    "omnia.max-core.db-role-protocol": "1",
                },
            )
        ),
        api=SimpleNamespace(
            exec_create=lambda *_a, **_kw: {"Id": "upload"},
            exec_start=lambda *_a, **_kw: SimpleNamespace(_sock=sock, close=lambda: None),
            exec_inspect=lambda _: {"Running": False, "ExitCode": 0},
        ),
    )
    backend = SimpleNamespace(
        stem="qa",
        client=client,
        guard_image=image,
        _lookup=lambda _, name, _kind: items.get(name),
        labels=lambda kind: {"kind": kind},
        _network=lambda *_a, **_kw: SimpleNamespace(connect=lambda _: None),
        address=lambda: "127.0.0.2",
        trusted_container_identity=lambda *_: None,
    )

    def bootstrap(*_args, **_kw):
        events.append("bootstrap")
        assert items["qa-max-core"] is old and items["qa-gateway"] is gateway
        if failure == "bootstrap":
            raise CellResourceError("bootstrap")

    def ready(target, _address, path, **kwargs):
        events.append(path)
        if path != "/__omnia/identity":
            assert items["qa-max-core"] is old and items["qa-gateway"] is gateway
        if failure == "health" and path == "/api/health":
            raise CellResourceError("health")
        if failure == "auth" and path == "/api/max/session":
            raise CellResourceError("auth")

    def apply_config(*_):
        if failure == "config":
            raise CellResourceError("config")

    monkeypatch.setattr(adapter, "bootstrap_core_database", bootstrap)
    monkeypatch.setattr(adapter, "_wait_http", ready)
    monkeypatch.setattr(adapter, "_public_auth_secret", lambda *_: secret)
    monkeypatch.setattr(
        adapter, "parts", lambda _: (SimpleNamespace(path=tmp_path / "machine.json"), backend)
    )
    monkeypatch.setattr(config, "apply_public_core_overlay", lambda _: None)
    monkeypatch.setattr(config, "apply_core_config", apply_config)
    monkeypatch.setattr(config, "boundary_source", lambda: "trusted")
    manifest = SimpleNamespace(routes=[])
    expected_failure = failure is not None and (failure != "auth" or public_mode)

    def start():
        adapter._start_boundary(
            state,
            manifest,
            backend,
            1,
            public_mode=public_mode,
            runtime_env={"MAX_BOT_TOKEN": "synthetic"} if public_mode else None,
            business_config_override={},
        )

    if expected_failure:
        with pytest.raises(CellResourceError, match=failure):
            start()
        assert items["qa-max-core"] is old and items["qa-gateway"] is gateway
        assert "qa-max-core-candidate" not in items
        assert not any(e.startswith("rename:") for e in events)
    else:
        start()
        assert items["qa-max-core"] is not old and "qa-max-core-retired" not in items
        assert events.index("/api/health") < events.index("rename:qa-max-core")
        assert events.index("/__omnia/identity") < events.index("remove:qa-max-core-retired")
        # Reusing a healthy upgraded core does not re-bootstrap or rotate its password.
        monkeypatch.setattr(adapter, "_wait_http", lambda *_a, **_kw: None)
        start()
        assert events.count("bootstrap") == 1
    assert items["qa-product"] is product and items["qa-pg"] is pg
    for created in creates:
        if created["name"] != "qa-max-core-candidate":
            continue
        env = created["environment"]
        assert env["DATABASE_URL"].startswith("postgresql://omnia_core_runtime:")
        assert not {"CORE_RUNTIME_PASSWORD", "POSTGRES_PASSWORD", "PGPASSWORD"} & env.keys()
        assert env["AUTH_SECRET"] == secret
        assert created["command"] == ["node", "server.js"]


def test_legacy_uncompiled_core_fails_before_any_mutation(tmp_path):
    adapter = MachineAdapter(SimpleNamespace(), SimpleNamespace())
    with pytest.raises(CellResourceError, match="compiled"):
        adapter._start_boundary(
            SimpleNamespace(resource_names=None), None, SimpleNamespace(client=None), 1
        )


@pytest.mark.parametrize(
    "image_ref, protocol", [("", "1"), ("untrusted:latest", "1"), ("sha256:" + "a" * 64, "0")]
)
def test_invalid_public_image_is_rejected_before_changing_live_auth(image_ref, protocol):
    from yleum_orchestrator.core.cell_resources import CellResourceError

    adapter = MachineAdapter(SimpleNamespace(), SimpleNamespace(cell_public_core_image=image_ref))

    def no_auth_changes(*_):
        pytest.fail("must validate the image before changing live auth")

    adapter._public_auth_secret = no_auth_changes
    backend = SimpleNamespace(
        client=SimpleNamespace(
            images=SimpleNamespace(
                get=lambda _: SimpleNamespace(
                    id=image_ref, labels={"omnia.max-core.protocol": protocol}
                )
            )
        )
    )
    with pytest.raises(CellResourceError, match="image"):
        adapter._start_boundary(
            SimpleNamespace(resource_names=None), None, backend, 1, public_mode=True
        )


@pytest.mark.parametrize(
    "image_ref, protocol",
    [
        ("untrusted:latest", "1"),
        ("sha256:" + "a" * 64, "0"),
    ],
)
def test_preview_rejects_unpinned_or_incompatible_image_before_any_runtime_change(
    image_ref,
    protocol,
):
    from yleum_orchestrator.core.cell_resources import CellResourceError

    adapter = MachineAdapter(SimpleNamespace(), SimpleNamespace(cell_preview_core_image=image_ref))
    adapter.secret = lambda _: pytest.fail("must validate image before accessing live secrets")
    backend = SimpleNamespace(
        client=SimpleNamespace(
            images=SimpleNamespace(
                get=lambda _: SimpleNamespace(
                    id=image_ref,
                    labels={
                        "omnia.max-core.protocol": "1",
                        "omnia.max-core.preview-protocol": protocol,
                    },
                )
            )
        )
    )
    with pytest.raises(CellResourceError, match="image"):
        adapter._start_boundary(SimpleNamespace(resource_names=None), None, backend, 1)


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
        attrs={"Image": image_id if core_state != "outdated" else "sha256:" + "b" * 64,
               "Config": {"Env": ["OMNIA_OWNER_PREVIEW=1"]}},
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


@pytest.mark.parametrize("mismatch", [False, True])
def test_existing_runtime_secret_failure_precedes_public_auth_rotation(tmp_path, mismatch):
    image = "sha256:" + "a" * 64
    adapter = MachineAdapter(SimpleNamespace(state_store=SimpleNamespace(root=tmp_path)),
                             SimpleNamespace(cell_public_core_image=image))
    state = SimpleNamespace(workspace_id=uuid4(),
                            resource_names=SimpleNamespace(postgres_container="qa-pg"))
    if mismatch:
        adapter.core_runtime_credentials.load_or_create(state.workspace_id)
    core = SimpleNamespace(attrs={"Config": {
        "Labels": {"omnia.max-core.db-role-protocol": "1"},
        "Env": ["DATABASE_URL=postgresql://omnia_core_runtime:wrong@qa-pg:5432/postgres"],
    }})
    client = SimpleNamespace(images=SimpleNamespace(get=lambda _: SimpleNamespace(id=image,
        labels={"omnia.max-core.protocol": "1", "omnia.max-core.db-role-protocol": "1"})),
        containers=None)
    backend = SimpleNamespace(client=client, stem="qa", _lookup=lambda *_: core)
    adapter._public_auth_secret = lambda *_: pytest.fail("auth rotated before credential check")
    with pytest.raises(CellResourceError, match="credential"):
        adapter._start_boundary(state, None, backend, 1, public_mode=True,
                                runtime_env={"MAX_BOT_TOKEN": "changed-synthetic"})


@pytest.mark.parametrize("key", ["DATABASE_URL", "CORE_RUNTIME_PASSWORD", "PGUSER"])
def test_docker_reserved_database_environment_fails_before_any_access(key):
    adapter = MachineAdapter(SimpleNamespace(), SimpleNamespace())
    with pytest.raises(CellResourceError, match="reserved"):
        adapter._start_boundary(SimpleNamespace(resource_names=None), None,
                                SimpleNamespace(client=None), 1, runtime_env={key: "ignored"})


@pytest.mark.parametrize("maintenance", [False, True])
@pytest.mark.parametrize("failed", [False, True])
def test_admin_bootstrap_is_bounded_ephemeral_and_exact_database_bound(
    tmp_path, maintenance, failed,
):
    from yleum_orchestrator.core.cell_resources import identity_labels
    image = "sha256:" + "a" * 64
    state = SimpleNamespace(workspace_id=uuid4(), project_id=uuid4(), owner_id=uuid4(),
                            profile_version="synthetic-v2")
    pg = SimpleNamespace(id="exact-pg", status="running", reload=lambda: None,
        attrs={"Config": {"Labels": identity_labels(
            state, "postgres-maintenance" if maintenance else "postgres")}})
    calls, removed = [], []
    def create(_image, command, **kwargs):
        calls.append((command, kwargs))
        def wait(**options):
            assert options == {"timeout": 80}
            if failed:
                raise RuntimeError("synthetic-secret-must-not-propagate")
            return {"StatusCode": 0}
        return SimpleNamespace(attrs={"Config": {"Labels": kwargs["labels"]}},
            start=lambda: None, wait=wait, remove=lambda **_: removed.append(True))
    client = SimpleNamespace(containers=SimpleNamespace(get=lambda _: pg, create=create),
        images=SimpleNamespace(get=lambda _: SimpleNamespace(id=image, labels={
            "omnia.max-core.protocol": "1", "omnia.max-core.preview-protocol": "1",
            "omnia.max-core.db-role-protocol": "1"})))
    manager = SimpleNamespace(state_store=SimpleNamespace(root=tmp_path / "state"),
        docker=SimpleNamespace(_client_obj=lambda: client),
        credential_store=SimpleNamespace(load_or_create=lambda _: SimpleNamespace(
            postgres_password="synthetic-admin-password")))
    adapter = MachineAdapter(manager, SimpleNamespace(cell_preview_core_image=image))
    if failed:
        with pytest.raises(CellResourceError, match=r"^trusted core database bootstrap failed$"):
            adapter.bootstrap_core_database(state, "exact-pg", maintenance=maintenance,
                                             role_only=maintenance)
    else:
        adapter.bootstrap_core_database(state, "exact-pg", maintenance=maintenance,
                                         role_only=maintenance)
    assert removed == [True]
    command, options = calls[0]
    assert command[:4] == ["timeout", "70", "node", "scripts/bootstrap-database.mjs"]
    assert ("--role-only" in command) is maintenance
    assert options["network_mode"] == "container:exact-pg"
    assert "ports" not in options and "volumes" not in options
    assert set(options["environment"]) == {"DATABASE_URL", "CORE_RUNTIME_PASSWORD"}
    assert options["read_only"] is True
