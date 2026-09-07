import importlib
import importlib.util
import os
import stat
import subprocess
from types import SimpleNamespace
from uuid import uuid4

import pytest

from omnia_orchestrator.core.project_machine import MachineManifest
from tests.test_project_machine_manifest import payload


def backend(tmp_path, **overrides):
    name = "omnia_orchestrator.services.docker_machine_backend"
    assert importlib.util.find_spec(name) is not None, "Docker machine execution is missing"
    module = importlib.import_module(name)
    values = dict(
        client=object(),
        workspace_id=uuid4(),
        project_id=uuid4(),
        owner_id=uuid4(),
        root=tmp_path,
        internal_network="test-internal",
        workspace_volume="test-source",
        base_image="sha256:" + "a" * 64,
        guard_image="sha256:" + "b" * 64,
        postgres_image="postgres@sha256:" + "c" * 64,
        project_postgres_password="test-project-postgres-password",
        project_postgres_memory_bytes=128 * 1024 * 1024,
        project_postgres_cpu_cores=0.1,
        network_pool="10.253.240.0/24",
        denied_cidrs=("170.168.72.200/32",),
        cpu_cores=1.0,
        memory_bytes=512 * 1024 * 1024,
        disk_bytes=4 * 1024**3,
        pids=256,
    )
    values.update(overrides)
    return module.DockerMachineBackend(**values)


def test_development_pid_one_handles_stop_signal_without_waiting_for_kill(tmp_path, monkeypatch):
    import signal
    import sys

    runtime = backend(tmp_path)
    options = runtime.container_options(MachineManifest.model_validate(payload()), "guard", 7)
    handlers = {}

    def pause():
        assert signal.SIGTERM in handlers, "namespace PID1 ignores default SIGTERM"
        handlers[signal.SIGTERM](signal.SIGTERM, None)

    monkeypatch.setitem(sys.modules, "signal", SimpleNamespace(
        SIGTERM=signal.SIGTERM, signal=lambda sig, handler: handlers.update({sig: handler}),
        pause=pause,
    ))
    assert options["entrypoint"] == ["python3", "-c"]
    with pytest.raises(SystemExit) as stopped:
        exec(options["command"][0], {})
    assert stopped.value.code == 0


def retained_preview_fixture(tmp_path):
    import docker

    from omnia_orchestrator.services.machine_environment import (
        MachineEnvironmentRef,
        VolumeEnvironmentRef,
    )
    from omnia_orchestrator.services.project_machine import write_controller_json

    runtime = backend(tmp_path)
    manifest = MachineManifest.model_validate(payload())
    volumes = {}
    for index, name in enumerate(runtime.environment_volume_names(manifest)):
        mountpoint = tmp_path / f"volume-{index}"
        mountpoint.mkdir()
        labels = runtime.labels("project-volume")
        if name == runtime.workspace_volume:
            labels = {
                "omnia.managed": "true", "omnia.project_cell": "true",
                "omnia.workspace_id": str(runtime.workspace_id),
                "omnia.project_id": str(runtime.project_id),
                "omnia.owner_id": str(runtime.owner_id),
                "omnia.provider": "docker_owner_canary", "omnia.resource_kind": "workspace",
                "omnia.profile_version": runtime.resource_profile_version,
            }
        volumes[name] = SimpleNamespace(attrs={
            "Name": name, "CreatedAt": "2026-09-07T12:00:00Z", "Driver": "local",
            "Scope": "local", "Mountpoint": str(mountpoint), "Labels": labels,
        })
    image_id = "sha256:" + "d" * 64
    image = SimpleNamespace(id=image_id, attrs={"Config": {
        "Labels": runtime.labels("environment")}})

    def missing(_):
        raise docker.errors.NotFound("absent")

    def get_volume(name):
        return volumes[name] if name in volumes else missing(name)

    attached = []
    helpers = {}

    class ProofHelper:
        id = "stat-helper"

        def __init__(self, options):
            self.options = options
            self.attrs = {"Config": {"Labels": options["labels"]}}

        def start(self):
            pass

        def wait(self, **_):
            return {"StatusCode": 0}

        def logs(self, **_):
            import json

            stats = [os.stat(volumes[name].attrs["Mountpoint"])
                     for name in self.options["volumes"]]
            return json.dumps([[item.st_dev, item.st_ino, item.st_ctime_ns]
                               for item in stats]).encode()

        def remove(self, **_):
            helpers.pop(self.options["name"])

    def create_helper(_image, _command, **options):
        assert options["network_mode"] == "none"
        assert options["read_only"] and options["cap_drop"] == ["ALL"]
        assert all(item["mode"] == "ro" for item in options["volumes"].values())
        helper = ProofHelper(options)
        helpers[options["name"]] = helper
        return helper

    def get_container(name):
        return helpers[name] if name in helpers else missing(name)

    runtime.client = SimpleNamespace(
        info=lambda: {"ID": "local-daemon"},
        images=SimpleNamespace(get=lambda _: image),
        containers=SimpleNamespace(get=get_container, list=lambda **_: attached,
                                   create=create_helper),
        volumes=SimpleNamespace(get=get_volume),
    )
    reference = MachineEnvironmentRef(
        workspace_id=runtime.workspace_id, image_id=image_id, artifact_ref="a" * 32 + ".tar",
        sha256="a" * 64, size=100, base_image=runtime.base_image,
        manifest_digest=manifest.digest(), manifest=manifest,
        volumes=tuple(VolumeEnvironmentRef(
            name=name, artifact_ref=f"{index:032x}.tar", sha256="c" * 64, size=10,
        ) for index, name in enumerate(volumes)),
    )
    write_controller_json(runtime.metadata_path, {
        "environment_ref": reference.model_dump(mode="json"), "restored_image": image_id,
        "manifest": manifest.model_dump(mode="json"), "epoch": 7,
    })
    return runtime, reference, volumes, image, attached


def test_completed_halt_receipt_is_consumed_once(tmp_path):
    runtime, reference, *_ = retained_preview_fixture(tmp_path)
    assert runtime.record_retained_preview(reference, epoch=7)
    assert runtime._metadata().get("retained_preview_receipt")
    assert runtime.consume_retained_preview(reference, epoch=7)
    assert "retained_preview_receipt" not in runtime._metadata()
    assert not runtime.consume_retained_preview(reference, epoch=7)


@pytest.mark.parametrize("fault", [None, "restarted", "replaced", "foreign", "attached",
                                 "missing", "changed_config", "product_alive"])
def test_retained_receipt_binds_trusted_runtime_and_still_fences_product(tmp_path, fault):
    from omnia_orchestrator.core.cell_resources import CellIdentityConflict

    runtime, reference, _volumes, _image, attached = retained_preview_fixture(tmp_path)
    original_get = runtime.client.containers.get
    trusted = SimpleNamespace(id="trusted-core", status="running", reload=lambda: None, attrs={
        "Id": "trusted-core", "Image": "sha256:" + "e" * 64,
        "State": {"StartedAt": "2026-09-07T10:00:00Z"},
        "Config": {"Labels": runtime.labels("managed-max-core"), "Env": ["SECRET=private"]},
        "HostConfig": {"Privileged": False}, "NetworkSettings": {}, "Mounts": [],
    })
    resources = {runtime.stem + "-max-core": trusted}
    runtime.client.containers.get = lambda name: (
        resources[name] if name in resources else original_get(name)
    )
    assert not runtime.record_retained_preview(reference, epoch=7)
    assert runtime.record_retained_preview(reference, epoch=7, retain_trusted=True)
    assert "private" not in runtime.metadata_path.read_text()
    if fault == "restarted":
        trusted.attrs["State"]["StartedAt"] = "2026-09-07T11:00:00Z"
    elif fault == "replaced":
        trusted.id = "replacement"
        trusted.attrs["Id"] = "replacement"
    elif fault == "foreign":
        trusted.attrs["Config"]["Labels"]["omnia.owner_id"] = str(uuid4())
    elif fault == "attached":
        attached.append(trusted)
    elif fault == "missing":
        resources.clear()
    elif fault == "changed_config":
        trusted.attrs["Config"]["Env"] = ["SECRET=changed"]
    elif fault == "product_alive":
        resources[runtime.machine_name] = SimpleNamespace(attrs={
            "Config": {"Labels": runtime.labels("development")},
        })
    if fault == "foreign":
        with pytest.raises(CellIdentityConflict):
            runtime.consume_retained_preview(reference, epoch=7)
    else:
        assert runtime.consume_retained_preview(reference, epoch=7) is (fault is None)
    assert "retained_preview_receipt" not in runtime._metadata()


@pytest.mark.parametrize("running", [True, False])
@pytest.mark.parametrize("include_logs", [True, False])
def test_owner_readiness_can_skip_unused_success_logs_but_keeps_failure_logs(
    tmp_path, running, include_logs,
):
    runtime = backend(tmp_path)
    service = MachineManifest.model_validate(payload()).services[0].model_copy(
        update={"readiness": None},
    )
    runtime._metadata = lambda: {"services": {
        service.name: {"epoch": 7, "exec_id": "service", "log": "service.log"},
    }}
    runtime.client = SimpleNamespace(api=SimpleNamespace(
        exec_inspect=lambda _: {"Running": running},
    ))
    reads = []
    runtime._read_log = lambda path: reads.append(path) or "diagnostic"
    result = runtime.service_status(service, 7, include_logs=include_logs)
    assert result["ready"] is running
    assert reads == (["service.log"] if include_logs or not running else [])


@pytest.mark.parametrize("failure", ["timeout", "bad_output"])
def test_interrupted_stat_helper_is_removed_without_certifying_volumes(
    tmp_path, monkeypatch, failure,
):
    import docker

    runtime, reference, *_ = retained_preview_fixture(tmp_path)
    create = runtime.client.containers.create

    def broken_create(*args, **kwargs):
        helper = create(*args, **kwargs)
        if failure == "timeout":
            def expired(**_):
                raise TimeoutError("proof deadline expired")

            helper.wait = expired
        else:
            helper.logs = lambda **_: b"not valid JSON"
        return helper

    monkeypatch.setattr(runtime.client.containers, "create", broken_create)
    if failure == "timeout":
        with pytest.raises(TimeoutError, match="deadline"):
            runtime.record_retained_preview(reference, epoch=7)
    else:
        assert not runtime.record_retained_preview(reference, epoch=7)
    assert "retained_preview_receipt" not in runtime._metadata()
    with pytest.raises(docker.errors.NotFound):
        runtime.client.containers.get(runtime.stem + "-retained-proof")


@pytest.mark.parametrize("fault", [
    "missing", "recreated", "same_timestamp_recreated", "foreign_volume", "foreign_workspace",
    "reference", "epoch", "dirty_receipt", "image", "foreign_image", "daemon", "writer",
    "restore_in_progress", "quiesce_failed", "quiesce_pending",
])
def test_retained_preview_never_trusts_stale_or_unsafe_resources(tmp_path, fault):
    from omnia_orchestrator.core.cell_resources import CellIdentityConflict
    from omnia_orchestrator.services.project_machine import write_controller_json

    runtime, reference, volumes, image, attached = retained_preview_fixture(tmp_path)
    assert runtime.record_retained_preview(reference, epoch=7)
    name = runtime.project_postgres_volume
    metadata = runtime._metadata()
    if fault == "missing":
        del volumes[name]
    elif fault == "recreated":
        volumes[name].attrs["CreatedAt"] = "2026-09-07T13:00:00Z"
    elif fault == "same_timestamp_recreated":
        mountpoint = volumes[name].attrs["Mountpoint"]
        os.rename(mountpoint, mountpoint + "-old")
        os.mkdir(mountpoint)
    elif fault in {"foreign_volume", "foreign_workspace"}:
        target = runtime.workspace_volume if fault == "foreign_workspace" else name
        volumes[target].attrs["Labels"]["omnia.owner_id"] = str(uuid4())
    elif fault == "reference":
        reference = reference.model_copy(update={"artifact_ref": "f" * 32 + ".tar"})
    elif fault == "epoch":
        metadata["epoch"] = 8
    elif fault == "dirty_receipt":
        metadata["retained_preview_receipt"]["epoch"] = 8
    elif fault == "image":
        metadata["restored_image"] = "sha256:" + "e" * 64
    elif fault == "foreign_image":
        image.attrs["Config"]["Labels"]["omnia.owner_id"] = str(uuid4())
    elif fault == "daemon":
        runtime.client.info = lambda: {"ID": "different-daemon"}
    elif fault == "writer":
        attached.append(SimpleNamespace(id="unexpected-container"))
    elif fault == "restore_in_progress":
        metadata["restore_in_progress"] = True
    else:
        metadata["quiesce_state"] = fault.removeprefix("quiesce_")
    write_controller_json(runtime.metadata_path, metadata)
    if fault in {"foreign_volume", "foreign_workspace", "foreign_image"}:
        with pytest.raises(CellIdentityConflict, match="identity"):
            runtime.consume_retained_preview(reference, epoch=7)
    else:
        assert not runtime.consume_retained_preview(reference, epoch=7)
    assert "retained_preview_receipt" not in runtime._metadata()


@pytest.mark.parametrize("operation", ["ensure", "exec", "restore"])
def test_mutation_invalidates_halted_receipt_before_first_effect(tmp_path, monkeypatch, operation):
    runtime, reference, *_ = retained_preview_fixture(tmp_path)
    assert runtime.record_retained_preview(reference, epoch=7)

    def interrupted(*_):
        assert "retained_preview_receipt" not in runtime._metadata()
        raise RuntimeError("interrupted before Docker mutation")

    if operation == "ensure":
        monkeypatch.setattr(runtime, "container_options", interrupted)

        def call():
            runtime.ensure(reference.manifest, 7)
    elif operation == "exec":
        monkeypatch.setattr(runtime, "_container", interrupted)

        def call():
            runtime.exec_start(["true"], ".", str(uuid4()))
    else:
        monkeypatch.setattr(runtime, "_reconcile_recovery_helpers", interrupted)

        def call():
            runtime.begin_restore(reference)
    with pytest.raises(RuntimeError, match="interrupted"):
        call()


def test_project_root_can_install_userland_but_cannot_control_network_or_host(tmp_path):
    runtime = backend(tmp_path)
    runtime._metadata = lambda: {"proxy_ip": "10.0.0.2"}
    options = runtime.container_options(MachineManifest.model_validate(payload()), "guard-id", 7)
    assert options["network_mode"] == "container:guard-id"
    assert options["cap_drop"] == ["ALL"]
    assert {"CHOWN", "SETUID", "SETGID"} <= set(options["cap_add"])
    assert not {"NET_ADMIN", "NET_RAW", "SYS_ADMIN", "SYS_PTRACE"} & set(options["cap_add"])
    assert options["privileged"] is False
    assert options["security_opt"] == ["no-new-privileges:true"]
    assert not options["ports"]
    assert options["memswap_limit"] == options["mem_limit"]
    assert "AUTH_SECRET" not in options["environment"]
    assert (
        options["environment"]["DATABASE_URL"]
        == "postgresql://postgres:test-project-postgres-password@127.0.0.1:5432/postgres"
    )
    assert options["environment"]["PGHOST"] == "127.0.0.1"
    assert options["environment"]["PGPASSWORD"] == "test-project-postgres-password"
    assert options["volumes"]["test-source"]["bind"] == "/workspace"
    assert not any(name.startswith("/") for name in options["volumes"])


@pytest.mark.parametrize("fault", [None, "empty_null", "dirty", "unknown_diff", "diff_error",
                                  "image", "false_diff", "string_diff",
                                  "base", "running", "owner", "image_owner", "workspace",
                                  "missing_image", "runtime_config", "deadline"])
def test_rootfs_reuse_requires_stopped_unchanged_trusted_image(tmp_path, fault):
    import docker

    from omnia_orchestrator.core.cell_resources import CellIdentityConflict
    from omnia_orchestrator.services.machine_environment import MachineEnvironmentRef

    runtime = backend(tmp_path)
    image_id = "sha256:" + "d" * 64
    ref = MachineEnvironmentRef(workspace_id=runtime.workspace_id, image_id=image_id,
        artifact_ref="a" * 32 + ".tar", sha256="b" * 64, size=100,
        base_image=runtime.base_image, manifest_digest="c" * 64, volumes=())
    image_labels = runtime.labels("environment")
    container_labels = runtime.labels("development")
    if fault == "owner":
        container_labels["omnia.owner_id"] = str(uuid4())
    if fault == "image_owner":
        image_labels["omnia.owner_id"] = str(uuid4())
    if fault == "workspace":
        ref = ref.model_copy(update={"workspace_id": uuid4()})
    if fault == "base":
        ref = ref.model_copy(update={"base_image": "sha256:" + "e" * 64})

    def diff():
        if fault == "diff_error":
            raise docker.errors.APIError("unavailable")
        if fault == "deadline":
            raise TimeoutError("machine budget exhausted")
        if fault == "empty_null":
            return None
        if fault == "false_diff":
            return False
        if fault == "string_diff":
            return ""
        return [{"Path": "/usr/bin/new", "Kind": 1}] if fault == "dirty" else (
            {} if fault == "unknown_diff" else [])

    machine = SimpleNamespace(status="running" if fault == "running" else "exited",
        reload=lambda: None, diff=diff, attrs={"Image": image_id if fault != "image" else
        "sha256:" + "f" * 64, "Config": {"Labels": container_labels}})
    image = SimpleNamespace(id=image_id, attrs={"Config": {"Labels": image_labels}})
    if fault == "runtime_config":
        image.attrs["Config"]["Env"] = ["UNTRUSTED=1"]

    def get_image(_):
        if fault == "missing_image":
            raise docker.errors.ImageNotFound("gone")
        return image

    runtime.client = SimpleNamespace(containers=SimpleNamespace(get=lambda _: machine),
                                     images=SimpleNamespace(get=get_image))
    if fault in {"owner", "image_owner", "workspace"}:
        with pytest.raises(CellIdentityConflict, match="identity"):
            runtime.can_reuse_image(ref)
    elif fault == "runtime_config":
        with pytest.raises(CellIdentityConflict, match="configuration"):
            runtime.can_reuse_image(ref)
    elif fault == "deadline":
        with pytest.raises(TimeoutError, match="budget"):
            runtime.can_reuse_image(ref)
    else:
        assert runtime.can_reuse_image(ref) is (fault in {None, "empty_null"})


def test_package_workers_and_node_heap_respect_cell_budget(tmp_path, monkeypatch):
    monkeypatch.setattr("os.cpu_count", lambda: 8)
    runtime = backend(tmp_path, memory_bytes=896 * 1024**2)
    options = runtime.container_options(MachineManifest.model_validate(payload()), "guard", 7)
    env = options["environment"]
    assert env["PNPM_WORKERS"] == "8"  # pnpm9 subtracts this from availableParallelism
    assert env["npm_config_child_concurrency"] == "1"
    assert env["npm_config_network_concurrency"] == "2"
    assert env["npm_config_fetch_retries"] == "5"
    assert env["npm_config_fetch_retry_factor"] == "2"
    assert env["npm_config_fetch_retry_mintimeout"] == "2000"
    assert env["npm_config_fetch_retry_maxtimeout"] == "20000"
    assert env["npm_config_fetch_timeout"] == "120000"
    assert env["NODE_OPTIONS"] == "--max-old-space-size=512"


def test_v2_active_machine_uses_two_cpu_two_gib_and_project_caches(tmp_path):
    runtime = backend(
        tmp_path,
        cpu_cores=2.0,
        memory_bytes=2 * 1024**3,
        resource_profile_version="docker-owner-cell-resources-v2",
    )
    runtime._metadata = lambda: {"proxy_ip": "10.0.0.2"}

    options = runtime.container_options(MachineManifest.model_validate(payload()), "guard", 7)

    assert options["nano_cpus"] == 2_000_000_000
    assert options["mem_limit"] == 2 * 1024**3
    assert options["environment"]["NODE_OPTIONS"] == "--max-old-space-size=1280"
    assert options["environment"]["npm_config_child_concurrency"] == "1"
    assert options["environment"]["npm_config_store_dir"] == "/pnpm/store"
    assert options["environment"]["COREPACK_HOME"] == "/root/.cache/node/corepack"
    assert options["volumes"][runtime.pnpm_cache_volume]["bind"] == "/pnpm/store"
    assert options["volumes"][runtime.corepack_cache_volume]["bind"] == (
        "/root/.cache/node/corepack"
    )
    assert options["volumes"][runtime.next_cache_volume]["bind"] == ("/workspace/.next/cache")
    assert all(not name.startswith("/") for name in options["volumes"])


def test_command_timeout_targets_only_the_exec_process_group(tmp_path):
    runtime = backend(tmp_path)
    calls = []

    class Machine:
        id = "machine-id"

        def exec_run(self, argv, *, user):
            calls.append((argv, user))
            return SimpleNamespace(exit_code=0)

    runtime._metadata = lambda: {
        "exec_logs": {"exec-id": "/run/omnia-logs/command-test.log"},
        "exec_pids": {"exec-id": "/run/omnia-logs/command-test.pid"},
    }
    runtime._container = lambda: Machine()
    runtime.client = SimpleNamespace(
        api=SimpleNamespace(
            exec_inspect=lambda exec_id: {
                "ContainerID": "machine-id",
                "Running": exec_id == "exec-id",
            }
        )
    )

    runtime.terminate_exec("exec-id", 17)

    assert len(calls) == 1
    argv, user = calls[0]
    assert argv[:4] == ["python3", "-I", "-S", "-c"]
    assert argv[-2:] == ["/run/omnia-logs/command-test.pid", "17"]
    assert argv[4].index("SIGTERM") < argv[4].index("SIGKILL")
    assert user == "0:0"


def test_environment_digest_changes_with_controller_owned_package_inventory(tmp_path):
    runtime = backend(tmp_path)
    inventory = [b'[{"command":["dpkg-query"],"exit_code":0,"output":"base=1"}]']

    class Container:
        def exec_run(self, argv, **kwargs):
            assert argv[:4] == ["python3", "-I", "-S", "-c"]
            assert "environment" not in kwargs
            return SimpleNamespace(exit_code=0, output=inventory[0])

    runtime._container = lambda: Container()
    first = runtime.environment_digest()
    inventory[0] = b'[{"command":["dpkg-query"],"exit_code":0,"output":"base=1\\nnew=2"}]'
    second = runtime.environment_digest()

    assert first != second
    assert len(first) == len(second) == 64


def test_service_budget_is_rejected_before_docker_side_effects(tmp_path):
    runtime = backend(tmp_path, memory_bytes=1)
    with pytest.raises(ValueError, match="resource"):
        runtime.container_options(MachineManifest.model_validate(payload()), "guard-id", 7)


def test_unpinned_images_or_missing_public_host_denies_fail_closed(tmp_path):
    with pytest.raises(ValueError, match="pinned"):
        backend(tmp_path, base_image="python:latest")
    with pytest.raises(ValueError, match="public host"):
        backend(tmp_path, denied_cidrs=())


@pytest.mark.parametrize("phase", ["service", "proxy"])
def test_blocking_readiness_cannot_extend_parent_budget(tmp_path, monkeypatch, phase):
    import http.client
    from types import SimpleNamespace

    from omnia_orchestrator.services import docker_machine_backend, project_machine

    runtime = backend(tmp_path)
    clock = [0.0]
    fake_time = SimpleNamespace(
        monotonic=lambda: clock[0], sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    )
    monkeypatch.setattr(docker_machine_backend, "time", fake_time)
    monkeypatch.setattr(project_machine, "time", fake_time)

    class Connection:
        def __init__(self, *args, **kwargs):
            pass

        def request(self, *args):
            raise OSError("not yet ready")

        def close(self):
            pass

    monkeypatch.setattr(http.client, "HTTPConnection", Connection)
    monkeypatch.setattr(docker_machine_backend.socket, "create_connection", Connection)
    runtime.address = lambda: "127.0.0.1"
    runtime._read_log = lambda path: "not ready"
    runtime._metadata = lambda: {"services": {"api": {"epoch": 7, "exec_id": "x", "log": "x"}}}
    runtime.client = SimpleNamespace(api=SimpleNamespace(exec_inspect=lambda _: {"Running": True}))
    with project_machine.machine_budget(2), pytest.raises(TimeoutError, match="budget"):
        if phase == "service":
            runtime.service_status(MachineManifest.model_validate(payload()).services[0], 7)
        else:
            # Simulate refused connections; unlike readiness, no socket has opened.
            def refused(*args, **kwargs):
                raise OSError("refused")

            monkeypatch.setattr(docker_machine_backend.socket, "create_connection", refused)
            runtime._wait_proxy_ready(SimpleNamespace(status="running", reload=lambda: None), "x")
    assert clock[0] <= 2.2


def test_shared_volume_at_two_destinations_never_silently_drops_a_mount(tmp_path):
    runtime = backend(tmp_path)
    value = payload()
    value["services"][0]["mounts"] = [{"volume": "data", "target": "/data"}]
    value["services"][1]["mounts"] = [{"volume": "data", "target": "/cache"}]
    with pytest.raises(ValueError, match="multiple guest targets"):
        runtime.container_options(MachineManifest.model_validate(value), "guard-id", 7)


def test_environment_snapshot_contract_includes_dedicated_project_postgres_volume(tmp_path):
    runtime = backend(tmp_path)
    manifest = MachineManifest.model_validate(payload())
    assert runtime.environment_volume_names(manifest) == (
        "test-source",
        f"{runtime.stem}-home",
        runtime.pnpm_cache_volume,
        runtime.corepack_cache_volume,
        runtime.next_cache_volume,
        runtime.project_postgres_volume,
    )


def test_project_postgres_shares_guard_namespace_without_host_or_core_access(tmp_path):
    runtime = backend(tmp_path)
    options = runtime._project_postgres_options("guard-id", 7)
    assert options["network_mode"] == "container:guard-id"
    assert options["command"] == [
        "postgres",
        "-D",
        "/var/lib/postgresql/data",
        "-c",
        "listen_addresses=127.0.0.1",
        "-c",
        "unix_socket_directories=",
    ]
    assert options["cap_drop"] == ["ALL"]
    assert options["cap_add"] == []
    assert options["privileged"] is False
    assert options["read_only"] is True
    assert options["ports"] == {}
    assert options["mem_limit"] == options["memswap_limit"]
    assert set(options["volumes"]) == {runtime.project_postgres_volume}
    assert options["environment"] == {"PGDATA": "/var/lib/postgresql/data"}


def test_project_postgres_ownership_helper_can_traverse_restored_pgdata(tmp_path):
    runtime = backend(tmp_path)
    created = []

    class Helper:
        def start(self):
            pass

        def wait(self, **_options):
            return {"StatusCode": 0}

        def remove(self, **_options):
            pass

    class Containers:
        def create(self, *args, **options):
            created.append((args, options))
            return Helper()

    runtime.client = SimpleNamespace(containers=Containers())
    runtime._lookup = lambda *_args: None

    runtime._ensure_project_postgres_permissions()

    _args, options = created[0]
    assert options["network_mode"] == "none"
    assert options["cap_drop"] == ["ALL"]
    assert options["cap_add"] == ["CHOWN", "DAC_OVERRIDE"]
    assert options["privileged"] is False
    assert options["read_only"] is True
    assert options["user"] == "0:0"
    assert options["volumes"] == {
        runtime.project_postgres_volume: {
            "bind": "/var/lib/postgresql/data",
            "mode": "rw",
        }
    }


@pytest.mark.skipif(os.name != "posix", reason="PGDATA mode contract requires a POSIX filesystem")
def test_project_postgres_seed_start_repairs_only_pgdata_root_mode_without_reinitializing(tmp_path):
    runtime = backend(tmp_path)
    pgdata = tmp_path / "seeded-pgdata"
    pgdata.mkdir(mode=0o755)
    pgdata.chmod(0o755)
    (pgdata / "PG_VERSION").write_text("17\n")
    customer_file = pgdata / "retained-business-data"
    customer_file.write_bytes(b"created-after-last-release")
    customer_file.chmod(0o640)
    assert stat.S_IMODE(pgdata.stat().st_mode) == 0o755

    class Helper:
        def __init__(self, command, environment):
            self.command = command
            self.environment = environment

        def start(self):
            # Execute the actual controller-emitted init command, substituting
            # only the isolated mountpoint. PG_VERSION must prevent initdb.
            self.result = subprocess.run(
                self.command,
                check=False,
                capture_output=True,
                env={"PATH": os.defpath, **self.environment, "PGDATA": str(pgdata)},
            )

        def wait(self, **_options):
            return {"StatusCode": self.result.returncode}

        def remove(self, **_options):
            pass

    class Containers:
        def create(self, *args, **options):
            return Helper(args[1], options["environment"])

    runtime.client = SimpleNamespace(containers=Containers())
    runtime._lookup = lambda *_args: None
    runtime._initialize_project_postgres_volume()
    assert stat.S_IMODE(pgdata.stat().st_mode) == 0o700
    assert (pgdata / "PG_VERSION").read_text() == "17\n"
    assert customer_file.read_bytes() == b"created-after-last-release"
    assert stat.S_IMODE(customer_file.stat().st_mode) == 0o640


def test_restore_reference_allows_legacy_machine_artifact_without_project_postgres_volume(tmp_path):
    from omnia_orchestrator.services.machine_environment import (
        MachineEnvironmentRef,
        VolumeEnvironmentRef,
    )

    runtime = backend(tmp_path)
    manifest = MachineManifest.model_validate(payload())
    legacy = MachineEnvironmentRef(
        workspace_id=runtime.workspace_id,
        image_id="sha256:" + "d" * 64,
        artifact_ref="a" * 32 + ".tar",
        sha256="b" * 64,
        size=1,
        base_image=runtime.base_image,
        manifest_digest=manifest.digest(),
        manifest=manifest,
        volumes=tuple(
            VolumeEnvironmentRef(
                name=name,
                artifact_ref=f"{index:032x}.tar",
                sha256="c" * 64,
                size=1,
            )
            for index, name in enumerate(runtime.volume_mapping(manifest), start=1)
        ),
    )
    runtime.validate_restore_reference(legacy)


def test_writable_restore_helper_can_traverse_private_project_postgres_volume(tmp_path):
    runtime = backend(tmp_path)
    manifest = MachineManifest.model_validate(payload())
    created = []

    class Containers:
        def create(self, *args, **options):
            created.append((args, options))
            return object()

    runtime.client = SimpleNamespace(containers=Containers())
    runtime._metadata = lambda: {
        "manifest": manifest.model_dump(mode="json"),
        "restore_in_progress": True,
        "restore_target": {"volumes": [{"name": runtime.project_postgres_volume}]},
    }

    runtime._archive_helper(runtime.project_postgres_volume, writable=True)

    _args, options = created[0]
    assert options["network_mode"] == "none"
    assert options["cap_drop"] == ["ALL"]
    assert options["cap_add"] == ["DAC_OVERRIDE"]
    assert options["privileged"] is False
    assert options["read_only"] is True
    assert options["user"] == "0:0"
    assert options["volumes"] == {
        runtime.project_postgres_volume: {"bind": "/volume", "mode": "rw"}
    }

    runtime._archive_helper(runtime.project_postgres_volume, writable=False)
    _args, read_options = created[1]
    assert read_options["cap_add"] == []
    assert read_options["volumes"] == {
        runtime.project_postgres_volume: {"bind": "/volume", "mode": "ro"}
    }


def test_tmpfs_command_logs_use_bounded_exec_read_not_docker_archive(tmp_path):
    from types import SimpleNamespace

    import docker

    runtime = backend(tmp_path)

    class Container:
        def get_archive(self, path):
            raise docker.errors.NotFound("Docker29 archive cannot see runtime tmpfs")

        def exec_run(self, argv, **kwargs):
            assert argv[-1] == "/run/omnia-logs/command-test.log"
            assert "O_NONBLOCK" in argv[-2]
            assert "signal.alarm(2)" in argv[-2]
            return SimpleNamespace(exit_code=0, output=b"intentional stderr\n")

    runtime.client = SimpleNamespace(containers=object())
    runtime._lookup = lambda *args: Container()
    runtime._container = lambda: pytest.fail("must not execute guest-overridable log reader")
    assert runtime._read_log("/run/omnia-logs/command-test.log") == "intentional stderr\n"


def test_restore_removes_old_rootfs_and_only_activates_complete_target(tmp_path):
    from types import SimpleNamespace

    from omnia_orchestrator.services.machine_environment import MachineEnvironmentRef
    from omnia_orchestrator.services.project_machine import write_controller_json

    runtime = backend(tmp_path)
    manifest = MachineManifest.model_validate(payload())
    target = MachineEnvironmentRef(
        workspace_id=runtime.workspace_id,
        image_id="sha256:" + "c" * 64,
        artifact_ref="a" * 32 + ".tar",
        sha256="d" * 64,
        size=1,
        base_image=runtime.base_image,
        manifest_digest=manifest.digest(),
        volumes=(),
    )
    write_controller_json(
        runtime.metadata_path,
        {"manifest": manifest.model_dump(mode="json"), "environment_ref": {"previous": True}},
    )
    removed = []
    resets = []
    runtime._container = lambda: SimpleNamespace()
    runtime._checkpoint_for_recreate = lambda value: None
    runtime.client = SimpleNamespace(containers=SimpleNamespace(list=lambda **kwargs: []))
    runtime.remove = lambda **kwargs: removed.append(True)
    runtime._reset_project_postgres_volume = lambda: resets.append(True)
    runtime.begin_restore(target)
    assert removed == [True]
    assert resets == [True]
    assert runtime._metadata()["rollback_ref"] == {"previous": True}
    with pytest.raises(Exception, match="incomplete"):
        runtime.ensure(manifest, 7)
    metadata = runtime._metadata()
    metadata["pending_image"] = target.image_id
    write_controller_json(runtime.metadata_path, metadata)
    runtime.finish_restore()
    assert runtime._metadata()["environment_ref"] == target.model_dump(mode="json")
    assert runtime._metadata()["restored_image"] == target.image_id
    assert not runtime._metadata()["restore_in_progress"]


def test_remove_fences_project_postgres_sidecar_too(tmp_path):
    runtime = backend(tmp_path)

    class Container:
        def __init__(self, key):
            self.key = key
            self.labels = {"omnia.fencing_epoch": "7"}

        def remove(self, force=True):
            alive[self.key] = False

        def reload(self):
            pass

    alive = {"machine": True, "postgres": True}
    runtime._container = lambda: Container("machine") if alive["machine"] else None
    runtime._project_postgres = lambda: Container("postgres") if alive["postgres"] else None
    runtime.remove(expected_epoch=7)
    assert alive == {"machine": False, "postgres": False}


def test_remove_does_not_partially_delete_when_sidecar_epoch_differs(tmp_path):
    runtime = backend(tmp_path)

    class Container:
        def __init__(self, key, epoch):
            self.key = key
            self.labels = {"omnia.fencing_epoch": str(epoch)}

        def remove(self, force=True):
            alive[self.key] = False

    alive = {"machine": True, "postgres": True}
    machine = Container("machine", 7)
    postgres = Container("postgres", 8)
    runtime._container = lambda: machine if alive["machine"] else None
    runtime._project_postgres = lambda: postgres if alive["postgres"] else None
    runtime.remove(expected_epoch=7)
    assert alive == {"machine": True, "postgres": True}


def test_is_running_reports_live_project_postgres_even_without_machine_process(tmp_path):
    runtime = backend(tmp_path)

    class Container:
        status = "running"

        def reload(self):
            pass

    runtime._container = lambda: None
    runtime._project_postgres = lambda: Container()
    assert runtime.is_running() is True


def test_manifest_change_checkpoints_and_removes_old_service_container(tmp_path):
    from types import SimpleNamespace

    from omnia_orchestrator.services.project_machine import write_controller_json

    runtime = backend(tmp_path)
    before = MachineManifest.model_validate(payload())
    after = before.model_copy(deep=True)
    after.services[0].argv = ["python3", "new.py"]
    write_controller_json(runtime.metadata_path, {"manifest": before.model_dump(mode="json")})
    runtime._container = lambda: SimpleNamespace(labels={"omnia.fencing_epoch": "7"})
    runtime._project_postgres = lambda: None
    operations = []
    runtime._checkpoint_for_recreate = lambda value: operations.append(("capture", value.digest()))
    runtime.remove = lambda **kwargs: operations.append(("remove", kwargs))

    class ReachedCreate(Exception):
        pass

    def network(name):
        raise ReachedCreate()

    runtime.client = SimpleNamespace(networks=SimpleNamespace(get=network))
    with pytest.raises(ReachedCreate):
        runtime.ensure(after, 7)
    assert operations == [("capture", before.digest()), ("remove", {"expected_epoch": 7})]


def test_snapshot_uses_export_import_without_inherited_docker_commit_config(tmp_path):
    from types import SimpleNamespace

    runtime = backend(tmp_path)
    image_id = "sha256:" + "e" * 64

    class Container:
        status = "exited"

        def reload(self):
            pass

        def commit(self, **kwargs):
            pytest.fail("Docker commit empty arrays inherit secret/proxy environment")

        def export(self):
            yield b"bounded rootfs archive"

    def import_image(*, src, changes):
        assert src.read() == b"bounded rootfs archive"
        assert all(change.startswith("LABEL ") for change in changes)
        return '{"status":"' + image_id + '"}\n'

    runtime._container = lambda: Container()
    runtime.client = SimpleNamespace(
        api=SimpleNamespace(import_image=import_image),
        images=SimpleNamespace(
            get=lambda key: SimpleNamespace(id=key, save=lambda **kwargs: iter([b"clean image"]))
        ),
    )
    actual, chunks = runtime.export_image()
    assert actual == image_id
    assert b"".join(chunks) == b"clean image"


def test_failed_quiesce_cannot_be_bypassed_by_stopped_retry(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from omnia_orchestrator.services.project_machine import write_controller_json

    runtime = backend(tmp_path)
    value = payload()
    value["services"][0]["mounts"] = [{"volume": "data", "target": "/data"}]
    value["tasks"] += [
        {"name": "quiet", "role": "quiesce", "argv": ["sh", "quiet.sh"]},
        {"name": "recover", "role": "restore_check", "argv": ["sh", "recover.sh"]},
    ]
    value["data_stores"] = [
        {
            "name": "local",
            "volumes": ["data"],
            "quiesce_task": "quiet",
            "restore_check_task": "recover",
        }
    ]
    manifest = MachineManifest.model_validate(value)
    write_controller_json(runtime.metadata_path, {"manifest": manifest.model_dump(mode="json")})
    container = SimpleNamespace(status="running", id="exact-container", reload=lambda: None)
    runtime._container = lambda: container
    runtime.stop = lambda: setattr(container, "status", "exited")
    runtime.exec_start = lambda *args: "hung"
    ticks = iter([0, 999999])
    monkeypatch.setattr(
        "omnia_orchestrator.services.docker_machine_backend.time.monotonic", lambda: next(ticks)
    )
    with pytest.raises(Exception, match=r"quiesce.*timed out"):
        runtime.prepare_capture()
    with pytest.raises(Exception, match="quiesce"):
        runtime.prepare_capture()


def test_crash_surviving_recovery_helper_is_removed_before_restore_activation(tmp_path):
    from types import SimpleNamespace

    import docker

    from omnia_orchestrator.services.project_machine import write_controller_json

    runtime = backend(tmp_path)
    events = []
    alive = [True]
    helper = SimpleNamespace(
        id="owned-checker", attrs={"Config": {"Labels": runtime.labels("restore-check")}}
    )

    def remove(**kwargs):
        events.append("remove")
        alive[0] = False

    helper.remove = remove

    def get(name):
        if alive[0]:
            return helper
        events.append("confirmed-absent")
        raise docker.errors.NotFound("removed")

    runtime.client = SimpleNamespace(
        containers=SimpleNamespace(list=lambda **kwargs: [helper] if alive[0] else [], get=get)
    )
    write_controller_json(
        runtime.metadata_path,
        {
            "restore_in_progress": True,
            "pending_image": "sha256:" + "c" * 64,
            "restore_target": {"known": True},
        },
    )
    runtime.finish_restore()
    assert events == ["remove", "confirmed-absent"]
    assert runtime._metadata()["restore_in_progress"] is False


def test_pending_quiesce_cannot_restart_before_explicit_restore(tmp_path):
    from omnia_orchestrator.services.project_machine import write_controller_json

    runtime = backend(tmp_path)
    write_controller_json(runtime.metadata_path, {"quiesce_state": "pending"})
    with pytest.raises(Exception, match="quiesce"):
        runtime.ensure(MachineManifest.model_validate(payload()), 7)
