from dataclasses import fields
from types import SimpleNamespace
from uuid import UUID

import pytest


def published_backend(tmp_path, release_id=None):
    from omnia_orchestrator.services.docker_machine_backend import DockerMachineBackend
    from omnia_orchestrator.services.published_machine_backend import (
        PublishedMachineBackend,
        release_volume_mapping,
    )
    from tests.test_docker_machine_backend import backend

    release_id = release_id or UUID(int=1)
    original = backend(tmp_path, workspace_id=UUID(int=9))
    values = {item.name: getattr(original, item.name) for item in fields(DockerMachineBackend)}
    layout = release_volume_mapping(original.workspace_id, release_id, [])
    values["workspace_volume"] = layout["workspace"]
    return PublishedMachineBackend(**values, release_id=release_id, release_layout=layout)


def interrupted_seed_backend(tmp_path, *, removal_confirmed=True, workspace_id=None):
    import docker

    runtime = published_backend(tmp_path)
    if workspace_id is not None:
        runtime.workspace_id = workspace_id
    runtime.release_id = None
    runtime.release_layout = {}
    resources = {}
    helper = SimpleNamespace(
        id="owned-archive",
        name=runtime.stem + "-archive",
        status="running",
        attrs={"Config": {"Labels": runtime.labels("archive")}},
    )

    def remove(**_kwargs):
        if removal_confirmed:
            resources.pop(helper.id)

    helper.remove = remove
    resources[helper.id] = helper

    def get(name):
        if name in resources:
            return resources[name]
        raise docker.errors.NotFound("absent")

    def list_containers(**options):
        selectors = options.get("filters", {}).get("label", [])
        return [
            item
            for item in resources.values()
            if all(
                item.attrs["Config"]["Labels"].get(key) == value
                for key, value in (selector.split("=", 1) for selector in selectors)
            )
        ]

    runtime.client = SimpleNamespace(
        containers=SimpleNamespace(get=get, list=list_containers),
        networks=SimpleNamespace(get=get),
    )
    return runtime, resources


@pytest.mark.parametrize("removal_confirmed", [False, True])
def test_public_retirement_cleans_interrupted_seed_without_release_journal(
    tmp_path, removal_confirmed
):
    runtime, resources = interrupted_seed_backend(tmp_path, removal_confirmed=removal_confirmed)
    if removal_confirmed:
        runtime.retire_compute()
        assert not resources
    else:
        with pytest.raises(RuntimeError, match=r"unverified|not confirmed"):
            runtime.retire_compute()
        assert resources["owned-archive"].status == "running"


@pytest.mark.parametrize("same_workspace", [False, True])
def test_public_retirement_scopes_inventory_and_refuses_unknown_owned_compute(
    tmp_path, same_workspace
):
    runtime, resources = interrupted_seed_backend(tmp_path)
    labels = runtime.labels("unknown-helper")
    if not same_workspace:
        labels["omnia.workspace_id"] = str(UUID(int=99))
    resources["unexpected"] = SimpleNamespace(
        id="unexpected", status="running", attrs={"Config": {"Labels": labels}}
    )
    if same_workspace:
        with pytest.raises(RuntimeError, match="not confirmed"):
            runtime.retire_compute()
    else:
        runtime.retire_compute()
    assert set(resources) == {"unexpected"}


@pytest.mark.parametrize("quiesce_fails", [False, True])
def test_public_delete_quiesces_data_and_removes_owned_compute_only(tmp_path, quiesce_fails):
    runtime = published_backend(tmp_path)
    events = []
    runtime.client = SimpleNamespace(
        containers=SimpleNamespace(list=lambda **_options: []), networks=object()
    )
    runtime._lookup = lambda _collection, name, _kind: SimpleNamespace(
        remove=lambda **_: events.append(name)
    )

    def quiesce():
        events.append("quiesce")
        if quiesce_fails:
            raise RuntimeError("data flush failed")

    runtime.quiesce_current = quiesce
    runtime.stop = lambda: events.append("stop")
    runtime.remove = lambda: events.append("remove-machine-and-pg")
    if quiesce_fails:
        with pytest.raises(RuntimeError, match="flush failed"):
            runtime.retire_compute()
        assert "remove-machine-and-pg" not in events
    else:
        runtime.retire_compute()
        assert events.index("quiesce") < events.index("remove-machine-and-pg")
        assert events[-1] == runtime.stem + "-public"
    assert events[:2] == [runtime.stem + "-gateway", runtime.stem + "-max-core"]


def test_preparing_next_release_does_not_replace_live_service_metadata(tmp_path):
    from omnia_orchestrator.services.project_machine import write_controller_json

    first = published_backend(tmp_path)
    second = published_backend(tmp_path, UUID(int=2))
    write_controller_json(
        first.metadata_path, {"services": {"web": {"exec_id": "live"}}, "manifest": {"version": 1}}
    )
    write_controller_json(second.metadata_path, {"services": {}, "manifest": {"version": 2}})
    assert first._metadata()["services"]["web"]["exec_id"] == "live"


def test_healthy_public_reconcile_preserves_service_processes_and_never_restores_data(tmp_path):
    from omnia_orchestrator.core.project_machine import MachineManifest
    from omnia_orchestrator.services.project_machine import write_controller_json
    from tests.test_project_machine_manifest import payload

    runtime = published_backend(tmp_path)
    manifest = MachineManifest.model_validate(payload())
    write_controller_json(
        runtime.metadata_path,
        {
            "manifest": manifest.model_dump(mode="json"),
            "epoch": 7,
            "restored_image": "sha256:" + "a" * 64,
            "services": {"web": {"exec_id": "live"}},
        },
    )
    runtime.assert_live_volumes = lambda _manifest: None
    runtime.restart_infrastructure = lambda: None
    attrs = {
        "Image": "sha256:" + "a" * 64,
        "Config": {"Labels": {"omnia.fencing_epoch": "7"}},
        "Mounts": [
            {"Name": name, "Destination": item["bind"], "RW": True}
            for name, item in runtime.volume_mapping(manifest).items()
        ]
        + [{"Name": runtime.stem + "-logs", "Destination": "/run/omnia-logs", "RW": True}],
    }
    runtime._container = lambda: SimpleNamespace(status="running", reload=lambda: None, attrs=attrs)

    def unexpected(*args, **kwargs):
        raise AssertionError("healthy runtime must not recreate or restore")

    runtime.ensure = unexpected
    runtime.import_volume = unexpected
    runtime.ensure_published(manifest, "sha256:" + "a" * 64, 7)
    assert runtime._metadata()["services"]["web"]["exec_id"] == "live"


@pytest.mark.parametrize("physical_change", ["image", "mount", "epoch"])
def test_interrupted_candidate_cannot_be_reconciled_as_previous_release(tmp_path, physical_change):
    from omnia_orchestrator.core.project_machine import MachineManifest
    from omnia_orchestrator.services.project_machine import write_controller_json
    from tests.test_project_machine_manifest import payload

    runtime = published_backend(tmp_path)
    manifest = MachineManifest.model_validate(payload())
    image_id = "sha256:" + "a" * 64
    write_controller_json(
        runtime.metadata_path,
        {
            "manifest": manifest.model_dump(mode="json"),
            "epoch": 7,
            "restored_image": image_id,
            "services": {"web": {"exec_id": "old-exec"}},
        },
    )
    attrs = {
        "Image": image_id,
        "Config": {"Labels": {"omnia.fencing_epoch": "7"}},
        "Mounts": [
            {"Name": name, "Destination": item["bind"], "RW": True}
            for name, item in runtime.volume_mapping(manifest).items()
        ]
        + [{"Name": runtime.stem + "-logs", "Destination": "/run/omnia-logs", "RW": True}],
    }
    if physical_change == "image":
        attrs["Image"] = "sha256:" + "b" * 64
    elif physical_change == "mount":
        attrs["Mounts"][0]["Name"] = "candidate-source"
    else:
        attrs["Config"]["Labels"]["omnia.fencing_epoch"] = "8"
    runtime.assert_live_volumes = lambda _manifest: None
    runtime.restart_infrastructure = lambda: None
    runtime._container = lambda: SimpleNamespace(status="running", reload=lambda: None, attrs=attrs)
    with pytest.raises(RuntimeError, match="physical"):
        runtime.ensure_published(manifest, image_id, 7)


def test_seed_refuses_existing_production_database_before_archive_import(tmp_path):
    runtime = published_backend(tmp_path)
    runtime.client = SimpleNamespace(volumes=object())
    runtime._lookup = lambda *args: object()

    def unexpected(*args):
        raise AssertionError("existing production data must not be cleared")

    runtime.import_volume = unexpected
    with pytest.raises(RuntimeError, match="already exists"):
        runtime.seed_volume(runtime.project_postgres_volume, tmp_path / "source.tar")


def test_release_layout_keeps_business_data_stable_and_source_isolated():
    from omnia_orchestrator.services.published_machine_backend import release_volume_mapping

    production = UUID("11111111-1111-1111-1111-111111111111")
    first = release_volume_mapping(production, UUID(int=1), ["uploads"])
    second = release_volume_mapping(production, UUID(int=2), ["uploads"])
    assert first["workspace"] != second["workspace"]
    assert first["home"] != second["home"]
    assert first["postgres"] == second["postgres"]
    assert first["data:uploads"] == second["data:uploads"]
    assert len(set(first.values())) == len(first)


def test_test_namespace_cannot_seed_production_named_volumes():
    from omnia_orchestrator.services.published_machine_backend import release_volume_mapping

    layout = release_volume_mapping(UUID(int=1), UUID(int=2), [], namespace="test")
    assert all(value.startswith("omnia-machine-test-") for value in layout.values())


def test_restart_stopped_public_guard_proxy_and_database_preserves_records(tmp_path):
    runtime = published_backend(tmp_path)
    records = ["write-after-checkpoint"]

    class Container:
        status = "exited"

        def __init__(self):
            self.labels = {"omnia.policy_digest": "guard-policy"}

        def reload(self):
            pass

        def start(self):
            self.status = "running"

        def logs(self, **kwargs):
            return b"POLICY_READY=guard-policy"

    containers = {
        kind: Container() for kind in ["egress-proxy", "namespace-guard", "project-postgres"]
    }
    runtime.client = SimpleNamespace(containers=object())
    runtime._lookup = lambda _collection, _name, kind: containers.get(kind)
    runtime._wait_project_postgres_ready = lambda container: None
    runtime.restart_infrastructure()
    assert all(container.status == "running" for container in containers.values())
    assert records == ["write-after-checkpoint"]


async def test_managed_infrastructure_restart_requires_existing_data_and_starts_sidecars(tmp_path):
    from unittest.mock import AsyncMock

    from omnia_orchestrator.services.published_machine_backend import ensure_managed_infrastructure

    names = SimpleNamespace(
        postgres_volume="owned-pg-data",
        redis_volume="owned-redis-data",
        postgres_container="owned-pg",
        redis_container="owned-redis",
        internal_network="owned-internal",
        egress_network="owned-egress",
    )
    state = SimpleNamespace(
        resource_names=names,
        workspace_id=UUID(int=1),
        project_id=UUID(int=2),
        owner_id=UUID(int=3),
        profile_version="test",
    )
    volumes = {"owned-pg-data": ["customer-a"], "owned-redis-data": ["queue-item"]}
    containers = {
        "owned-pg": SimpleNamespace(state="exited"),
        "owned-redis": SimpleNamespace(state="exited"),
    }

    async def start(name):
        containers[name].state = "running"

    manager = SimpleNamespace(
        docker=SimpleNamespace(
            get_volume=AsyncMock(side_effect=lambda name: volumes.get(name)),
            get_container=AsyncMock(side_effect=lambda name: containers.get(name)),
            start_container=start,
        ),
        _verify_volume_record=lambda *_args: None,
        _ensure_network=AsyncMock(),
        _verify_container_record=lambda *_args: None,
        _steady_postgres_spec=lambda *_args: SimpleNamespace(name="owned-pg"),
        _steady_redis_spec=lambda *_args: SimpleNamespace(name="owned-redis"),
    )
    await ensure_managed_infrastructure(manager, state)
    assert {name: value.state for name, value in containers.items()} == {
        "owned-pg": "running",
        "owned-redis": "running",
    }
    assert volumes == {"owned-pg-data": ["customer-a"], "owned-redis-data": ["queue-item"]}
    volumes.pop("owned-pg-data")
    with pytest.raises(RuntimeError, match="volume missing"):
        await ensure_managed_infrastructure(manager, state)


@pytest.mark.parametrize("quiesce_fails", [False, True])
def test_update_quiesces_actual_previous_manifest_before_removal(
    tmp_path, monkeypatch, quiesce_fails
):
    from omnia_orchestrator.core.project_machine import MachineManifest
    from omnia_orchestrator.services.project_machine import write_controller_json
    from omnia_orchestrator.services.published_machine_backend import PublishedMachineBackend
    from tests.test_project_machine_manifest import payload

    old = published_backend(tmp_path, UUID(int=1))
    incoming = published_backend(tmp_path, UUID(int=2))
    manifest = MachineManifest.model_validate(payload())
    write_controller_json(
        old.metadata_path,
        {
            "release_layout": old.release_layout,
            "manifest": manifest.model_dump(mode="json"),
            "services": {"store": {"exec_id": "accepted-live"}},
        },
    )
    current = SimpleNamespace(
        reload=lambda: None,
        attrs={"Config": {"Labels": {"omnia.public_release_id": str(UUID(int=1))}}},
    )
    events = []
    incoming._container = lambda: current
    incoming.assert_live_volumes = lambda _manifest: None
    incoming.remove = lambda: events.append("remove")
    incoming.restart_infrastructure = lambda: None
    incoming.ensure = lambda *_args: events.append("start-candidate")

    def capture(runtime):
        assert runtime.release_id == UUID(int=1)
        assert runtime.workspace_volume == old.release_layout["workspace"]
        events.append("quiesce-accepted")
        if quiesce_fails:
            raise RuntimeError("quiesce failed")

    monkeypatch.setattr(PublishedMachineBackend, "prepare_capture", capture)
    monkeypatch.setattr(PublishedMachineBackend, "stop", lambda _runtime: events.append("stop"))
    if quiesce_fails:
        from omnia_orchestrator.services.published_machine_backend import (
            PublicationRecoveryRequired,
        )

        with pytest.raises(PublicationRecoveryRequired, match="quiesce failed"):
            incoming.switch_code(manifest, "sha256:" + "a" * 64, 2)
        assert events == ["quiesce-accepted"]
    else:
        incoming.switch_code(manifest, "sha256:" + "a" * 64, 2)
        assert events == ["quiesce-accepted", "stop", "remove", "start-candidate"]


def test_incompatible_update_fails_before_data_or_code_changes():
    from omnia_orchestrator.services.published_machine_backend import assert_compatible_update

    old = {"schema_digest": "a" * 64, "data_contract_digest": "b" * 64}
    assert_compatible_update(old, old)
    with pytest.raises(RuntimeError, match="migration_required"):
        assert_compatible_update(old, {**old, "schema_digest": "c" * 64})
    with pytest.raises(RuntimeError, match="migration_required"):
        assert_compatible_update(old, {**old, "data_contract_digest": "d" * 64})


def test_publication_rejects_unsafe_slug_and_unscoped_secrets():
    from omnia_orchestrator.schemas.cell_publication import CellDeployRequest

    request = dict(
        workspace_id=UUID(int=1),
        project_id=UUID(int=2),
        owner_id=UUID(int=3),
        snapshot_id=UUID(int=4),
        candidate_id=UUID(int=5),
        slug="real-product",
        commit_sha="a" * 40,
        source_revision="b" * 64,
        fencing_epoch=3,
        proof_key="c" * 64,
        schema_data_digest="d" * 64,
        build_ref="built",
        verification_ref="verified",
        idempotency_key="publish-one",
    )
    assert CellDeployRequest(**request).slug == "real-product"
    with pytest.raises(ValueError):
        CellDeployRequest(**{**request, "slug": "../other"})
    with pytest.raises(ValueError):
        CellDeployRequest(**request, runtime_env={"DATABASE_URL": "forbidden"})
