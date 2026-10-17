"""Explicit local-only receipt fixture. No application, network or shared volumes."""

import asyncio
import os
import threading
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import docker  # type: ignore[import-untyped]
import httpx
import pytest
from fastapi import FastAPI

from tests.test_workspace_router import _internal_settings as _internal_settings
from tests.test_workspace_router import _ready_provider
from yleum_orchestrator.core.cell_resources import (
    CellIndeterminateOperation,
    CellResourceError,
    CellResourceNames,
)
from yleum_orchestrator.core.errors import OrchestratorError, orchestrator_error_handler
from yleum_orchestrator.routers import workspace
from yleum_orchestrator.services.docker_py_cell_backend import DockerPyCellBackend
from yleum_orchestrator.services.source_write_journal import (
    SourceWriteJournal,
    SourceWriteRecord,
    source_write_scope,
)

PYTHON_IMAGE = "sha256:e5b65587bce7de595f299855d7385fe7fca39b8a74baa261ba1b7147afa78e58"
ALPINE_IMAGE = "sha256:d9e853e87e55526f6b2917df91a2115c36dd7c696a35be12163d44e6e2a4b6bc"


def _assert_create_contract(args, kwargs, name, nonce):
    assert len(args) <= 1 and not (args and "image" in kwargs)
    selected_image = args[0] if args else kwargs["image"]
    assert selected_image in (PYTHON_IMAGE, ALPINE_IMAGE)
    assert kwargs["network"] == "none" and "network_mode" not in kwargs
    assert kwargs["read_only"] and kwargs["privileged"] is False
    assert kwargs["cap_drop"] == ["ALL"] and kwargs["cap_add"] == []
    assert kwargs["security_opt"] == ["no-new-privileges:true"]
    assert kwargs["ports"] == {} and kwargs["environment"] == {}
    assert kwargs["volumes"] in (
        {name: {"bind": "/volume", "mode": "rw"}},
        {name: {"bind": "/volume", "mode": "ro"}},
    )
    assert kwargs["labels"]["task9.receipt_fixture"] == nonce


async def test_receipt_fixture_guard_matches_actual_backend_vector_without_docker():
    import inspect

    from docker.models.containers import ContainerCollection

    from tests.test_docker_py_cell_backend import _labels

    print(
        {
            "sdk": docker.__version__,
            "create_signature": str(
                inspect.signature(
                    ContainerCollection.create,
                )
            ),
        }
    )
    name, nonce = "pure-fixture-volume", "pure-fixture"
    labels = {**_labels("volume-write"), "task9.receipt_fixture": nonce}
    vectors = []

    def capture(*args, **kwargs):
        _assert_create_contract(args, kwargs, name, nonce)
        vectors.append((args, kwargs))
        return SimpleNamespace(start=lambda: None, reload=lambda: None)

    class PureBackend(DockerPyCellBackend):
        def _client_obj(self):
            return SimpleNamespace(containers=SimpleNamespace(create=capture))

    backend = PureBackend("unused", ALPINE_IMAGE, write_helper_image=PYTHON_IMAGE)
    for mode, image in (("ro", None), ("rw", PYTHON_IMAGE), ("rw", None)):
        await backend._start_helper_container(
            name="pure",
            labels=labels,
            volumes={name: {"bind": "/volume", "mode": mode}},
            image=image,
        )
    assert len(vectors) == 3
    args, kwargs = vectors[0]
    bad = {
        "network": "host",
        "read_only": False,
        "privileged": True,
        "cap_drop": [],
        "cap_add": ["SYS_ADMIN"],
        "security_opt": [],
        "ports": {80: 80},
        "environment": {"UNTRUSTED": "present"},
        "volumes": {"foreign": {"bind": "/volume", "mode": "rw"}},
        "labels": {"task9.receipt_fixture": "foreign"},
    }
    for key, value in bad.items():
        with pytest.raises(AssertionError):
            _assert_create_contract(args, {**kwargs, key: value}, name, nonce)
    with pytest.raises(AssertionError):
        _assert_create_contract(("unapproved:tag",), kwargs, name, nonce)
    with pytest.raises(AssertionError):
        _assert_create_contract(args, {**kwargs, "network_mode": "host"}, name, nonce)


@pytest.mark.skipif(
    os.environ.get("YLEUM_TEST_RECEIPT_DOCKER") != "1",
    reason="explicit nonce-owned offline receipt Docker fixture only",
)
async def test_owned_real_source_receipts(tmp_path, monkeypatch, _internal_settings):
    client = docker.DockerClient(base_url="unix:///var/run/docker.sock", timeout=20)
    for image in (PYTHON_IMAGE, ALPINE_IMAGE):
        assert client.images.get(image).id == image  # Never pull/build/install.
    nonce, wid, pid, owner = uuid4().hex, uuid4(), uuid4(), uuid4()
    name = CellResourceNames.for_workspace(wid, namespace="test").workspace_volume
    labels = {
        "omnia.managed": "true",
        "omnia.project_cell": "true",
        "omnia.provider": "docker_owner_canary",
        "omnia.workspace_id": str(wid),
        "omnia.project_id": str(pid),
        "omnia.owner_id": str(owner),
        "omnia.profile_version": "docker-owner-cell-resources-v1",
        "omnia.resource_kind": "workspace",
        "task9.receipt_fixture": nonce,
    }
    # Deny an existing name, even if the nonce improbably collides.
    with pytest.raises(docker.errors.NotFound):
        client.volumes.get(name)
    volume = client.volumes.create(name=name, labels=labels)
    known = {}
    counts = {"created": 0, "removed": 0, "volume_removed": 0, "mutators": 0}
    ambiguous = False
    # DockerClient.containers returns a new collection on each property access.
    # Bind the guarded collection once and give the backend this exact facade.
    containers = client.containers
    create = containers.create
    backend_client = SimpleNamespace(
        containers=containers,
        volumes=client.volumes,
        api=client.api,
        info=client.info,
    )
    entered, release, retired = threading.Event(), threading.Event(), threading.Event()

    def checked(container):
        assert container.id in known
        container.reload()
        attrs = container.attrs
        assert attrs["Image"] in (PYTHON_IMAGE, ALPINE_IMAGE)
        actual = attrs["Config"]["Labels"]
        assert all(actual.get(k) == v for k, v in labels.items() if k != "omnia.resource_kind")
        assert attrs["HostConfig"]["NetworkMode"] == "none"
        assert not attrs["HostConfig"].get("PortBindings")
        assert attrs["HostConfig"]["ReadonlyRootfs"]
        mounts = [mount for mount in attrs["Mounts"] if mount["Type"] != "tmpfs"]
        assert len(mounts) == 1
        assert (mounts[0]["Type"], mounts[0]["Name"], mounts[0]["Destination"]) == (
            "volume",
            name,
            "/volume",
        )

    def guarded_create(*args, **kwargs):
        _assert_create_contract(args, kwargs, name, nonce)
        container = create(*args, **kwargs)
        known[container.id] = container
        counts["created"] += 1
        if kwargs["labels"]["omnia.resource_kind"] in {"volume-write", "volume-delete"}:
            counts["mutators"] += 1
        checked(container)
        if ambiguous and kwargs["labels"]["omnia.resource_kind"] == "volume-write":
            raise docker.errors.APIError("simulated lost create acknowledgement")
        return container

    monkeypatch.setattr(containers, "create", guarded_create)

    class OwnedBackend(DockerPyCellBackend):
        cancel_transfer = False

        def _client_obj(self):
            return backend_client

        def _guarded_source_write(self, container, archive):
            if self.cancel_transfer:
                entered.set()
                assert release.wait(10), "fixture transfer did not drain"
                raise CellResourceError("cancelled fixture transfer")
            return super()._guarded_source_write(container, archive)

        async def _remove_container_object(self, container):
            try:
                checked(container)
            except docker.errors.NotFound:
                return
            await super()._remove_container_object(container)
            counts["removed"] += 1
            if self.cancel_transfer:
                retired.set()

    backend = OwnedBackend(
        "unix:///var/run/docker.sock",
        ALPINE_IMAGE,
        write_helper_image=PYTHON_IMAGE,
    )

    async def new_intent(suffix):
        # Each failure gets a separate local journal; never clear an unresolved one.
        journal = SourceWriteJournal(tmp_path / suffix / "state", wid)
        value = SourceWriteRecord(
            operation_id=uuid4(),
            workspace_id=wid,
            project_id=pid,
            owner_id=owner,
            generation_run_id=uuid4(),
            fencing_epoch=4,
            physical_volume=name,
            physical_volume_identity=await backend.source_volume_identity(name),
            request_sha256="a" * 64,
            before_revision="b" * 64,
        )
        journal.start(value)
        return journal, value

    try:
        await backend.write_volume_files(name, {"old.txt": b"old"})
        journal, value = await new_intent("success")
        with source_write_scope(journal, value.operation_id):
            await backend.delete_volume_paths(name, ("old.txt",))
            await backend.write_volume_files(name, {"new.txt": b"once"})
        assert await backend.read_volume_files(name) == {"new.txt": b"once"}
        assert await backend.source_volume_identity(name) == value.physical_volume_identity
        assert len(journal.load(value.operation_id).helpers) == 2
        terminal = journal.finish(value, after="c" * 64, written=1, deleted=1)
        created = counts["created"]
        assert SourceWriteJournal(tmp_path / "success" / "state", wid).start(value) == terminal
        assert counts["created"] == created  # Lost ACK returns durable result, no helper.

        journal, value = await new_intent("guarded-failure")
        backend.write_helper_image = ALPINE_IMAGE  # No Python, no unsafe extraction fallback.
        with source_write_scope(journal, value.operation_id), pytest.raises(CellResourceError):
            await backend.write_volume_files(name, {"new.txt": b"forbidden"})
        backend.write_helper_image = PYTHON_IMAGE
        assert await backend.read_volume_files(name) == {"new.txt": b"once"}
        with pytest.raises(CellIndeterminateOperation):
            journal.start(value)

        journal, value = await new_intent("ambiguous-create")
        ambiguous = True
        created = counts["mutators"]
        with source_write_scope(journal, value.operation_id), pytest.raises(docker.errors.APIError):
            await backend.write_volume_files(name, {"new.txt": b"unknown"})
        ambiguous = False
        assert counts["mutators"] == created + 1
        with pytest.raises(CellIndeterminateOperation):
            journal.start(value)
        assert counts["mutators"] == created + 1

        journal, value = await new_intent("cancel")
        backend.cancel_transfer = True
        with source_write_scope(journal, value.operation_id):
            task = asyncio.create_task(backend.write_volume_files(name, {"new.txt": b"cancelled"}))
        assert await asyncio.to_thread(entered.wait, 10)
        task.cancel()
        assert await asyncio.to_thread(retired.wait, 10)
        assert not task.done(), "lock-owning task returned before worker drained"
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 10)
        backend.cancel_transfer = False
        with pytest.raises(CellIndeterminateOperation):
            journal.start(value)
        assert await backend.read_volume_files(name) == {"new.txt": b"once"}
        # Remove the known ambiguous helper before replacing only this owned volume.
        for container in known.values():
            try:
                checked(container)
                container.remove(force=True)
                counts["removed"] += 1
            except docker.errors.NotFound:
                pass
        volume.reload()
        assert volume.name == name and volume.attrs["Labels"] == labels
        assert not client.containers.list(all=True, filters={"volume": name})
        # Actual HTTP receipt + real Docker source; fake manager creates no app/DB resources.
        provider, manager, _fake_docker, run_id = await _ready_provider(tmp_path / "http", wid)
        state = manager.state_store.load(wid)
        manager.state_store._persist_state(
            replace(
                state,
                project_id=pid,
                owner_id=owner,
                resource_names=replace(state.resource_names, workspace_volume=name),
            )
        )
        manager.docker = backend
        monkeypatch.setattr(workspace, "build_workspace_provider", lambda _: provider)
        operation_id = uuid4()
        headers = {"X-Internal-Token": "test-internal-token-not-a-real-secret"}
        body = {
            "operation_id": str(operation_id),
            "generation_run_id": str(run_id),
            "fencing_epoch": 4,
            "expected_revision": workspace._workspace_revision({"new.txt": "once"}),
            "files": {"terminal.txt": "confirmed"},
            "deletes": [],
        }
        app = FastAPI()
        app.add_exception_handler(OrchestratorError, orchestrator_error_handler)
        app.include_router(workspace.router)
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=True)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            response = await http.post(
                f"/internal/workspaces/{wid}/agent/write-files", headers=headers, json=body
            )
            assert response.status_code == 200, response.text
            assert response.json()["receipt"]["physical_volume_identity"]
        old_identity = await backend.source_volume_identity(name)
        volume.remove()
        counts["volume_removed"] += 1
        volume = client.volumes.create(name=name, labels=labels)  # Same name, no sleep.
        assert await backend.source_volume_identity(name) != old_identity
        app = FastAPI()
        app.add_exception_handler(OrchestratorError, orchestrator_error_handler)
        app.include_router(workspace.router)
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=True)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            status = await http.get(
                f"/internal/workspaces/{wid}/agent/write-operations/{operation_id}",
                headers=headers,
                params={"generation_run_id": str(run_id)},
            )
            assert status.status_code == 409
            replay = await http.post(
                f"/internal/workspaces/{wid}/agent/write-files", headers=headers, json=body
            )
            assert replay.status_code == 409
        mutators = counts["mutators"]
        with source_write_scope(journal, value.operation_id), pytest.raises(CellResourceError):
            await backend.write_volume_files(name, {"new.txt": b"wrong incarnation"})
        assert counts["mutators"] == mutators
    finally:
        release.set()
        # Exact inspected object IDs only; no wildcard removal, prune or forced volume removal.
        for container in known.values():
            try:
                checked(container)
                container.remove(force=True)
                counts["removed"] += 1
            except docker.errors.NotFound:
                pass
        volume.reload()
        assert volume.name == name and volume.attrs["Labels"] == labels
        assert not client.containers.list(all=True, filters={"volume": name})
        volume.remove()
        counts["volume_removed"] += 1
        with pytest.raises(docker.errors.NotFound):
            client.volumes.get(name)
        client.close()
        print({"fixture": "task9-source-receipts", **counts})
