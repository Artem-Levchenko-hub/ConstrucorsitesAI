from contextlib import nullcontext
from types import SimpleNamespace

import docker
import pytest

from tests._generation_reader_settings import legacy_reader_process_settings  # noqa: F401
from tests.test_project_machine_manifest import payload
from tests.test_published_machine_backend import published_backend
from yleum_orchestrator.core.project_machine import MachineManifest
from yleum_orchestrator.services.owner_management_identity import owner_management_environment
from yleum_orchestrator.services.project_database_credentials import ProjectDatabaseCredentials
from yleum_orchestrator.services.project_machine import write_controller_json


class DockerFixture:
    """Fake Docker IO; publication, metadata, capture and ensure stay real."""

    def __init__(self, runtime, monkeypatch):
        self.runtime = runtime
        self.objects = {}
        self.events = []
        self.sql = b""
        self.volumes = {}
        self.containers = SimpleNamespace(get=self.get, create=self.create, list=self.list)
        self.networks = SimpleNamespace(get=self.network)
        self.api = SimpleNamespace(
            exec_create=lambda *a, **kw: {"Id": "sql"},
            exec_start=lambda *a, **kw: SimpleNamespace(_sock=self, close=lambda: None),
            exec_inspect=lambda _: {"Running": False, "ExitCode": 0},
            import_image=self.import_image,
        )
        self.images = SimpleNamespace(get=lambda name: SimpleNamespace(
            id=name, save=lambda **kw: iter([b"captured-rootfs"]),
        ))
        runtime.client = SimpleNamespace(
            containers=self.containers, networks=self.networks, api=self.api, images=self.images,
            volumes=SimpleNamespace(get=self.volume),
        )
        monkeypatch.setattr(
            "yleum_orchestrator.services.docker_machine_backend.socket.create_connection",
            lambda *a, **kw: nullcontext(),
        )

    def settimeout(self, _value):
        pass

    def sendall(self, value):
        self.sql = value

    def shutdown(self, _value):
        pass

    def recv(self, _size):
        value = b"t\n" if b"pg_reload_conf" in self.sql else b""
        self.sql = b""
        return b"\x01\0\0\0" + len(value).to_bytes(4, "big") + value if value else b""

    def import_image(self, **_kwargs):
        self.events.append("capture-image")
        return '{"status":"sha256:' + "b" * 64 + '"}'

    def get(self, name):
        if name not in self.objects:
            raise docker.errors.NotFound("missing")
        return self.objects[name]

    def list(self, **_kwargs):
        return list(self.objects.values())

    def network(self, name):
        return SimpleNamespace(name=name, attrs={
            "Internal": name == self.runtime.internal_network,
            "Labels": self.runtime.labels("public-egress"),
        })

    def volume(self, name):
        if name not in self.volumes:
            raise docker.errors.NotFound("missing volume")
        return SimpleNamespace(attrs={"Labels": self.runtime.labels("project-volume")})

    def create(self, image, *args, **options):
        self.events.append("create:" + options["labels"]["omnia.resource_kind"])
        item = self.add(image, options)
        item.status = "created"
        return item

    def add(self, image, options):
        fixture = self

        class Container:
            def __init__(self):
                self.name = options["name"]
                self.id = self.name
                self.labels = options["labels"]
                self.status = "running"
                self.attrs = {
                    "Image": image,
                    "Config": {"Labels": self.labels, "Env": [
                        f"{key}={value}" for key, value in options.get("environment", {}).items()
                    ], "User": options.get("user"), "Cmd": options.get("command"),
                        "Entrypoint": options.get("entrypoint")},
                    "Mounts": [{"Name": name, "Type": "volume", "Destination": mount["bind"],
                                "RW": mount["mode"] == "rw"}
                               for name, mount in options.get("volumes", {}).items()],
                    "HostConfig": {key: options.get(source) for key, source in {
                        "NetworkMode": "network_mode", "Privileged": "privileged",
                        "ReadonlyRootfs": "read_only", "CapAdd": "cap_add", "CapDrop": "cap_drop",
                        "PortBindings": "ports", "SecurityOpt": "security_opt", "Tmpfs": "tmpfs",
                        "Memory": "mem_limit", "MemorySwap": "memswap_limit",
                        "NanoCpus": "nano_cpus",
                        "PidsLimit": "pids_limit",
                    }.items()},
                    "NetworkSettings": {"Networks": {
                        fixture.runtime.internal_network: {"IPAddress": "10.0.0.2"},
                    }},
                }

            def reload(self):
                pass

            def stop(self, **_kwargs):
                fixture.events.append("stop:" + self.labels["omnia.resource_kind"])
                self.status = "exited"

            def start(self):
                fixture.events.append("start:" + self.labels["omnia.resource_kind"])
                self.status = "running"

            def remove(self, **_kwargs):
                fixture.events.append("remove:" + self.labels["omnia.resource_kind"])
                fixture.objects.pop(self.name)

            def export(self):
                fixture.events.append("export-rootfs")
                return iter([b"rootfs"])

            def get_archive(self, _path):
                fixture.events.append("export-volume")
                return iter([b"volume archive"]), {}

            def put_archive(self, *_args):
                pytest.fail("capability refresh must never import volume contents")

            def commit(self, **_kwargs):
                pytest.fail("capability refresh must never commit an image")

            def logs(self, **_kwargs):
                return ("POLICY_READY=" + self.labels["omnia.policy_digest"]).encode()

            def exec_run(self, *_args, **_kwargs):
                return SimpleNamespace(exit_code=0, output=b"")

        item = Container()
        self.objects[item.name] = item
        return item


def rotation_fixture(tmp_path, monkeypatch, *, status="running", token="old", cache="release"):
    from yleum_orchestrator.services.machine_egress import GuardPolicy

    runtime = published_backend(tmp_path)
    runtime.project_credentials = ProjectDatabaseCredentials("r" * 40, "m" * 40, "a" * 40)
    if cache == 'compatible':
        runtime.release_layout['next'] = runtime.next_cache_volume
        runtime.workspace_volume = runtime.stem + '-code-' + runtime.release_id.hex
        runtime.release_layout['workspace'] = runtime.workspace_volume
    manifest = MachineManifest.model_validate(payload())
    image = "sha256:" + "a" * 64
    write_controller_json(runtime.metadata_path, {
        "manifest": manifest.model_dump(mode="json"), "epoch": 7, "restored_image": image,
        "project_database_role_protocol": 1, "release_layout": runtime.release_layout,
        "services": {"api": {"exec_id": "live", "epoch": 7}},
    })
    fixture = DockerFixture(runtime, monkeypatch)
    fixture.volumes = {name: b"accepted-source-or-live-data" for name in
                       (*runtime.environment_volume_names(manifest), runtime.stem + "-logs")}
    guard_id = runtime.stem + "-guard"
    policy = GuardPolicy(workspace_id=str(runtime.workspace_id), proxy_ip="10.0.0.2")
    fixture.add("guard-image", {"name": guard_id, "labels": {
        **runtime.labels("namespace-guard"), "omnia.policy_digest": policy.digest(),
    }})
    fixture.add("proxy-image", {"name": runtime.stem + "-proxy",
                                "labels": runtime.labels("egress-proxy")})
    fixture.add(runtime.postgres_image, runtime._project_postgres_options(guard_id, 7))
    runtime.owner_management_env = owner_management_environment(
        runtime.project_id, runtime.owner_id, runtime.release_id,
        {"MAX_BOT_TOKEN": token} if token else {},
    )
    product = fixture.add(image, runtime.container_options(manifest, guard_id, 7))
    product.status = status
    return runtime, manifest, fixture, product, image


@pytest.mark.parametrize("cache", ["release", "compatible"])
@pytest.mark.parametrize("status", ["running", "exited"])
@pytest.mark.parametrize("token", ["new", None])
def test_rotation_and_revoke_preserve_accepted_image_source_and_live_volumes(
    tmp_path, monkeypatch, status, token, cache,
):
    runtime, manifest, docker_io, original, image = rotation_fixture(
        tmp_path, monkeypatch, status=status, cache=cache,
    )
    before = dict(docker_io.volumes)
    runtime.owner_management_env = owner_management_environment(
        runtime.project_id, runtime.owner_id, runtime.release_id,
        {"MAX_BOT_TOKEN": token} if token else {},
    )
    runtime.ensure_published(manifest, image, 7)
    assert runtime._metadata()["restored_image"] == image
    assert runtime._container() is not original
    assert runtime._container().attrs["Image"] == image
    actual = dict(item.split("=", 1) for item in runtime._container().attrs["Config"]["Env"])
    assert actual.get("YLEUM_OWNER_ASSERTION_KEY") == runtime.owner_management_env.get(
        "YLEUM_OWNER_ASSERTION_KEY"
    )
    assert actual.get("YLEUM_BUSINESS_ANALYTICS_KEY") == runtime.owner_management_env.get(
        "YLEUM_BUSINESS_ANALYTICS_KEY"
    )
    assert docker_io.volumes == before
    assert runtime._metadata()["release_layout"] == runtime.release_layout
    assert runtime._metadata()["manifest"] == manifest.model_dump(mode="json")
    assert "environment_ref" not in runtime._metadata()
    assert not {"capture-image", "export-rootfs", "export-volume",
                "stop:project-postgres", "remove:project-postgres"}.intersection(docker_io.events)
    events = list(docker_io.events)
    runtime.ensure_published(manifest, image, 7)
    assert docker_io.events == events


@pytest.mark.parametrize("unconfigured", [False, True])
def test_same_keys_or_readonly_backend_do_not_recreate(tmp_path, monkeypatch, unconfigured):
    runtime, manifest, docker_io, original, image = rotation_fixture(tmp_path, monkeypatch)
    if unconfigured:
        runtime.owner_management_env = None
    runtime.ensure_published(manifest, image, 7)
    assert runtime._container() is original
    assert not docker_io.events




@pytest.mark.parametrize("binding_state", ["valid", "legacy", "foreign"])
@pytest.mark.parametrize("initial_token", [None, "old"])
async def test_configuration_connect_revoke_reconnect_keeps_same_release(
    tmp_path, monkeypatch, initial_token, binding_state,
):
    from unittest.mock import AsyncMock

    from tests.test_cell_publication import request
    from yleum_orchestrator.services.cell_lock import WorkspaceOperationLock
    from yleum_orchestrator.services.cell_publication import CellPublicationService

    runtime, manifest, docker_io, product, image = rotation_fixture(
        tmp_path / "runtime", monkeypatch, token=initial_token,
    )
    # A managed release has a controller binding even before its first bot key.
    binding = {"YLEUM_OWNER_PROJECT_ID": str(runtime.project_id),
               "YLEUM_OWNER_ID": str(runtime.owner_id),
               "YLEUM_OWNER_PUBLICATION_ID": str(runtime.release_id)}
    configured = dict(item.split("=", 1) for item in product.attrs["Config"]["Env"])
    if binding_state == "legacy":
        configured = {key: value for key, value in configured.items()
                      if not key.startswith("YLEUM_OWNER_")}
        binding = {}
    elif binding_state == "foreign":
        binding["YLEUM_OWNER_PUBLICATION_ID"] = "another-publication"
    product.attrs["Config"]["Env"] = [f"{key}={value}" for key, value in
                                      {**configured, **binding}.items()]
    service = CellPublicationService(SimpleNamespace(), root=tmp_path / "publications")
    value = request(project_id=runtime.project_id, owner_id=runtime.owner_id)
    release = {"release_id": str(runtime.release_id), "manifest": manifest.model_dump(mode="json"),
               "image_id": image, "epoch": 7, "prod_url": "https://example.test",
               "schema_digest": "schema"}
    service._write(value.project_id, {
        "project_id": str(value.project_id), "owner_id": str(value.owner_id),
        "source_workspace_id": str(value.workspace_id),
        "production_workspace_id": str(runtime.workspace_id), "active_release": release,
        "history": [release],
    })
    request_path = service.root / str(value.project_id) / "requests" / f"{runtime.release_id}.json"
    request_path.parent.mkdir(parents=True)
    request_path.write_text(value.model_dump_json(), encoding="utf-8")
    state = SimpleNamespace()
    boundaries = []
    manager = SimpleNamespace(
        operation_lock=WorkspaceOperationLock(tmp_path / "locks"),
        state_store=SimpleNamespace(load=lambda _: state),
        machine_runtime=SimpleNamespace(_start_boundary=lambda *a, **kw: boundaries.append(kw)),
    )
    service._production_manager = lambda _: manager
    service._backend = lambda *_: runtime
    # External infrastructure, process readiness/schema and ingress IO only.
    service._upgrade_docker_project_roles = AsyncMock()
    monkeypatch.setattr(
        "yleum_orchestrator.services.cell_publication.ensure_managed_infrastructure", AsyncMock(),
    )
    runtime.start_service = lambda *_: None
    runtime.service_status = lambda *_: {"ready": True}
    runtime.schema_digest = lambda: "schema"
    docker_io.add("gateway", {"name": runtime.stem + "-gateway",
                              "labels": runtime.labels("max-gateway")})
    monkeypatch.setattr("yleum_orchestrator.services.nginx_writer.publish_http", AsyncMock())
    monkeypatch.setattr(
        "yleum_orchestrator.services.nginx_writer.prod_host", lambda _: "example.test",
    )
    volumes = dict(docker_io.volumes)
    for token in ("connected", None, "reconnected"):
        await service.configure(value.project_id, value.owner_id,
                                runtime_env={"MAX_BOT_TOKEN": token} if token else {})
        actual = dict(item.split("=", 1) for item in runtime._container().attrs["Config"]["Env"])
        if binding_state != "valid":
            assert runtime._container() is product
            assert actual == {**configured, **binding}
            assert not docker_io.events
            continue
        assert all(actual.get(key) == value for key, value in binding.items())
        expected = owner_management_environment(value.project_id, value.owner_id,
                                                runtime.release_id,
                                                {"MAX_BOT_TOKEN": token} if token else {})
        assert actual.get("YLEUM_OWNER_ASSERTION_KEY") == expected.get("YLEUM_OWNER_ASSERTION_KEY")
        assert actual.get("YLEUM_BUSINESS_ANALYTICS_KEY") == expected.get(
            "YLEUM_BUSINESS_ANALYTICS_KEY"
        )
        assert runtime._metadata()["restored_image"] == image
        saved_release = service._read(value.project_id)["active_release"]
        assert {key: saved_release[key] for key in release} == release
        from yleum_orchestrator.services.publication_identity import source_identity
        assert saved_release["source_identity"] == source_identity(value)
        assert docker_io.volumes == volumes
    assert len(boundaries) == 3
    assert not {"capture-image", "export-rootfs", "export-volume"}.intersection(docker_io.events)



@pytest.mark.parametrize("status", ["running", "exited"])
@pytest.mark.parametrize("change", ["readonly", "old_database"])
def test_recovery_and_database_refresh_never_use_development_capture(
    tmp_path, monkeypatch, status, change,
):
    runtime, manifest, docker_io, original, image = rotation_fixture(
        tmp_path, monkeypatch, status=status,
    )
    original_env = dict(item.split("=", 1) for item in original.attrs["Config"]["Env"])
    if change == "readonly":
        runtime.owner_management_env = None
    else:
        original.attrs["Config"]["Env"] = [
            item for item in original.attrs["Config"]["Env"] if not item.startswith("PGPASSWORD=")
        ]
    runtime.ensure_published(manifest, image, 7)
    actual = dict(item.split("=", 1) for item in runtime._container().attrs["Config"]["Env"])
    assert actual["YLEUM_OWNER_ASSERTION_KEY"] == original_env["YLEUM_OWNER_ASSERTION_KEY"]
    assert actual["PGPASSWORD"] == original_env["PGPASSWORD"]
    assert runtime._metadata()["restored_image"] == image
    assert not {"capture-image", "export-rootfs", "export-volume"}.intersection(docker_io.events)
    if change == "readonly":
        assert runtime.owner_management_env is None
        if status == "running":
            assert runtime._container() is original


@pytest.mark.parametrize("status", ["running", "exited"])
@pytest.mark.parametrize("quiesce_fails", [False, True])
def test_rotation_quiesces_live_embedded_store_before_removal_but_skips_stopped_writers(
    tmp_path, monkeypatch, status, quiesce_fails,
):
    runtime, manifest, docker_io, original, image = rotation_fixture(
        tmp_path, monkeypatch, status=status,
    )
    value = manifest.model_dump(mode="json")
    value["services"][0]["mounts"] = [{"volume": "data", "target": "/data"}]
    value["tasks"] += [
        {"name": "quiet", "role": "quiesce", "argv": ["sh", "quiet.sh"]},
        {"name": "recover", "role": "restore_check", "argv": ["sh", "recover.sh"]},
    ]
    value["data_stores"] = [{"name": "local", "volumes": ["data"],
                            "quiesce_task": "quiet", "restore_check_task": "recover"}]
    manifest = MachineManifest.model_validate(value)
    data_volume = runtime.stem + "-data-data"
    runtime.release_layout["data:data"] = data_volume
    docker_io.volumes[data_volume] = b"live-customer-records"
    original.attrs["Mounts"].append({"Name": data_volume, "Type": "volume",
                                     "Destination": "/data", "RW": True})
    metadata = runtime._metadata()
    metadata.update(
        manifest=manifest.model_dump(mode="json"), release_layout=runtime.release_layout,
    )
    write_controller_json(runtime.metadata_path, metadata)

    def create_exec(container, *_args, **_kwargs):
        return {"Id": "quiesce" if container == original.id else "sql"}

    def start_exec(execution, **_kwargs):
        if execution == "quiesce":
            docker_io.events.append("quiesce")
            return None
        return SimpleNamespace(_sock=docker_io, close=lambda: None)

    docker_io.api.exec_create = create_exec
    docker_io.api.exec_start = start_exec
    docker_io.api.exec_inspect = lambda execution: {
        "Running": False, "ExitCode": 1 if execution == "quiesce" and quiesce_fails else 0,
        "ContainerID": original.id,
    }
    runtime.owner_management_env = owner_management_environment(
        runtime.project_id, runtime.owner_id, runtime.release_id, {"MAX_BOT_TOKEN": "new"},
    )
    if status == "running" and quiesce_fails:
        with pytest.raises(RuntimeError, match="production data quiesce failed"):
            runtime.ensure_published(manifest, image, 7)
        assert runtime._container() is original
        assert "remove:development" not in docker_io.events
        assert runtime._metadata()["quiesce_state"] == "failed"
    else:
        runtime.ensure_published(manifest, image, 7)
        assert runtime._container() is not original
        if status == "running":
            assert docker_io.events.index("quiesce") < docker_io.events.index("remove:development")
        else:
            assert "quiesce" not in docker_io.events
    assert runtime._metadata()["restored_image"] == image
    assert docker_io.volumes[data_volume] == b"live-customer-records"
    assert not {"capture-image", "export-rootfs", "export-volume"}.intersection(docker_io.events)
