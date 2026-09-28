from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest
from scripts import cleanup_orphan_archives as gc

WORKSPACE = "11111111-1111-4111-8111-111111111111"


@pytest.fixture(autouse=True)
def no_docker_for_filesystem_cases(monkeypatch, request):
    if "checkpoint_docker" not in request.node.name:
        monkeypatch.setattr(
            gc, "checkpoint_references", lambda declared, optional: (set(), 0, 0, 0)
        )


def archive(root: Path, letter: str = "a", *, age: int = 9000) -> Path:
    path = root / "project-machines" / "artifacts" / WORKSPACE / (letter * 32 + ".tar")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"archive-data")
    os.utime(path, (time.time() - age, time.time() - age))
    return path


def run(root: Path, monkeypatch: pytest.MonkeyPatch, *, apply: bool = True) -> int:
    monkeypatch.setattr(gc, "STATE", str(root))
    monkeypatch.setattr(gc, "ARTIFACTS", str(root / "project-machines" / "artifacts"))
    monkeypatch.setattr(
        sys, "argv", ["cleanup", "--state-root", str(root)] + (["--apply"] if apply else [])
    )
    return gc.main()


def test_deletes_only_old_unreferenced_and_is_idempotent(tmp_path, monkeypatch):
    orphan = archive(tmp_path)
    referenced = archive(tmp_path, "b")
    fresh = archive(tmp_path, "c", age=60)
    foreign = tmp_path / "restorations" / "other-workspace"
    foreign.mkdir(parents=True)
    (foreign / "history.json").write_text(json.dumps({"nested": {"archive": referenced.name}}))
    assert run(tmp_path, monkeypatch) == 0
    assert not orphan.exists()
    assert referenced.exists() and fresh.exists()
    assert run(tmp_path, monkeypatch) == 0
    assert referenced.exists() and fresh.exists()


@pytest.mark.parametrize("invalid", ["{broken", '{"reference": NaN}', '{"a": 1, "a": 2}'])
def test_invalid_json_aborts_without_deleting(tmp_path, monkeypatch, invalid):
    orphan = archive(tmp_path)
    (tmp_path / "state.json").write_text(invalid)
    assert run(tmp_path, monkeypatch) == 1
    assert orphan.exists()


def test_oversized_json_aborts_without_deleting(tmp_path, monkeypatch):
    orphan = archive(tmp_path)
    monkeypatch.setattr(gc, "MAX_STATE_FILE_BYTES", 10)
    (tmp_path / "state.json").write_text(json.dumps({"metadata": "x" * 50}))
    assert run(tmp_path, monkeypatch) == 1
    assert orphan.exists()


def test_dry_run_does_not_create_locks(tmp_path, monkeypatch):
    orphan = archive(tmp_path)
    assert run(tmp_path, monkeypatch, apply=False) == 0
    assert orphan.exists()
    assert not (tmp_path / "locks").exists()


def test_unreadable_json_aborts_without_deleting(tmp_path, monkeypatch):
    orphan = archive(tmp_path)
    metadata = tmp_path / "state.json"
    metadata.write_text("{}")
    original = os.open

    def unreadable(path, *args, **kwargs):
        if Path(path) == metadata:
            raise PermissionError("private payload must not be printed")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", unreadable)
    assert run(tmp_path, monkeypatch) == 1
    assert orphan.exists()


def test_incomplete_directory_walk_aborts_without_deleting(tmp_path, monkeypatch):
    orphan = archive(tmp_path)
    original = os.walk

    def incomplete(*args, **kwargs):
        yield next(original(*args, **kwargs))
        kwargs["onerror"](PermissionError("inaccessible subtree"))

    monkeypatch.setattr(os, "walk", incomplete)
    assert run(tmp_path, monkeypatch) == 1
    assert orphan.exists()


@pytest.mark.parametrize(
    "phase", ["queued", "building", "pushing", "swapping", "cancelling", "preparing", "recovering"]
)
def test_active_or_unknown_publication_prevents_deletion(tmp_path, monkeypatch, phase):
    orphan = archive(tmp_path)
    publication = tmp_path / "cell-publications" / "another-project" / "publication.json"
    publication.parent.mkdir(parents=True)
    publication.write_text(json.dumps({"history": [{"response": {"phase": phase}}]}))
    assert run(tmp_path, monkeypatch) == 1
    assert orphan.exists()


@pytest.mark.parametrize("phase", ["done", "failed", "cancelled"])
def test_finished_publication_does_not_block_collection(tmp_path, monkeypatch, phase):
    orphan = archive(tmp_path)
    publication = tmp_path / "cell-publications" / "another-project" / "publication.json"
    publication.parent.mkdir(parents=True)
    publication.write_text(
        json.dumps(
            {
                "history": [
                    {"response": {"phase": "queued"}},
                    {"response": {"phase": phase}},
                ]
            }
        )
    )
    assert run(tmp_path, monkeypatch) == 0
    assert not orphan.exists()


def symlink(target: Path, link: Path, *, directory=False):
    try:
        link.symlink_to(target, target_is_directory=directory)
    except OSError as error:
        pytest.skip(f"symlink creation unavailable: {type(error).__name__}")


def test_symlink_archive_and_noncanonical_files_preserved(tmp_path, monkeypatch):
    orphan = archive(tmp_path)
    target = tmp_path / "outside.tar"
    target.write_bytes(b"do not touch")
    link = orphan.parent / ("b" * 32 + ".tar")
    symlink(target, link)
    noncanonical = orphan.parent / "backup.tar"
    noncanonical.write_bytes(b"unmanaged")
    assert run(tmp_path, monkeypatch) == 0
    assert link.is_symlink() and target.read_bytes() == b"do not touch"
    assert noncanonical.exists()


@pytest.mark.parametrize("location", ["root", "metadata", "subtree"])
def test_symlink_state_aborts(tmp_path, monkeypatch, location):
    root = tmp_path / "state"
    orphan = archive(root)
    if location == "root":
        link = tmp_path / "linked-state"
        symlink(root, link, directory=True)
        root = link
    elif location == "metadata":
        target = tmp_path / "outside.json"
        target.write_text("{}")
        symlink(target, root / "metadata.json")
    else:
        target = tmp_path / "other-state"
        target.mkdir()
        symlink(target, root / "subtree", directory=True)
    assert run(root, monkeypatch) == 1
    assert orphan.exists()


@pytest.mark.asyncio
async def test_busy_controller_workspace_lock_skips_workspace(tmp_path, monkeypatch, capsys):
    import asyncio
    from uuid import UUID

    from yleum_orchestrator.services.cell_lock import WorkspaceOperationLock

    orphan = archive(tmp_path)
    async with WorkspaceOperationLock(tmp_path).hold(UUID(WORKSPACE)):
        assert await asyncio.to_thread(run, tmp_path, monkeypatch) == 0
    assert orphan.exists()
    report = json.loads(capsys.readouterr().out)
    assert report["busy_workspaces"] == 1
    assert report["deleted_files"] == 0


@pytest.mark.parametrize("change", ["reference", "publication", "replace", "fresh"])
def test_rechecks_after_taking_controller_lock(tmp_path, monkeypatch, change):
    from contextlib import asynccontextmanager

    orphan = archive(tmp_path)
    before = orphan.stat()
    original = gc.WorkspaceOperationLock.hold

    @asynccontextmanager
    async def changed_while_waiting(self, workspace_id):
        async with original(self, workspace_id):
            if change == "reference":
                (tmp_path / "foreign-restoration.json").write_text(
                    json.dumps({"history": [str(orphan)]})
                )
            elif change == "publication":
                publication = tmp_path / "cell-publications" / "other" / "publication.json"
                publication.parent.mkdir(parents=True)
                publication.write_text(json.dumps({"history": [{"response": {"phase": "queued"}}]}))
            elif change == "replace":
                replacement = orphan.with_suffix(".replacement")
                replacement.write_bytes(b"new contents")
                os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
                replacement.replace(orphan)
            else:
                os.utime(orphan, None)
            yield

    monkeypatch.setattr(gc.WorkspaceOperationLock, "hold", changed_while_waiting)
    assert run(tmp_path, monkeypatch) == (1 if change == "publication" else 0)
    assert orphan.exists()


def test_escaped_reference_preserved(tmp_path, monkeypatch):
    referenced = archive(tmp_path)
    (tmp_path / "history.json").write_text('{"archive":"' + r"\u0061" * 32 + '.tar"}')
    assert run(tmp_path, monkeypatch) == 0
    assert referenced.exists()


def test_minimum_age_cannot_disable_grace_period(tmp_path, monkeypatch):
    orphan = archive(tmp_path)
    monkeypatch.setattr(
        sys, "argv", ["cleanup", "--state-root", str(tmp_path), "--min-age-seconds", "0", "--apply"]
    )
    with pytest.raises(SystemExit) as error:
        gc.main()
    assert error.value.code == 2
    assert orphan.exists()


def test_cli_help_without_docker():
    import subprocess

    result = subprocess.run(
        [sys.executable, str(Path(gc.__file__)), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--state-root" in result.stdout


def seal(path: Path, *, age: int = 9000) -> Path:
    marker = path.with_name(path.name + ".ok")
    marker.write_bytes(b"seal-data")
    os.utime(marker, (time.time() - age, time.time() - age))
    return marker


def test_only_old_orphan_seals_deleted(tmp_path, monkeypatch, capsys):
    orphan = archive(tmp_path)
    orphan_seal = seal(orphan)
    referenced = archive(tmp_path, "b")
    referenced_seal = seal(referenced)
    fresh = archive(tmp_path, "c", age=60)
    fresh_seal = seal(fresh)
    old_with_fresh_seal = archive(tmp_path, "d")
    recent_seal = seal(old_with_fresh_seal, age=60)
    (tmp_path / "history.json").write_text(json.dumps({"archive": referenced.name}))
    assert run(tmp_path, monkeypatch) == 0
    assert not orphan.exists() and not orphan_seal.exists()
    assert not old_with_fresh_seal.exists()
    assert all(path.exists() for path in (referenced_seal, fresh_seal, recent_seal))
    report = json.loads(capsys.readouterr().out)
    assert report["deleted_seals"] == 1
    assert report["deleted_seal_bytes"] == 9
    assert isinstance(report["disk_free_bytes_before"], int)
    assert isinstance(report["disk_free_bytes_after"], int)


def test_seal_replaced_after_archive_unlink_is_preserved(tmp_path, monkeypatch):
    orphan = archive(tmp_path)
    marker = seal(orphan)
    before = marker.stat()
    original = Path.unlink

    def replaced(path, *args, **kwargs):
        result = original(path, *args, **kwargs)
        if path == orphan:
            replacement = marker.with_suffix(".replacement")
            replacement.write_bytes(b"new-seal!")
            os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
            replacement.replace(marker)
        return result

    monkeypatch.setattr(Path, "unlink", replaced)
    assert run(tmp_path, monkeypatch) == 0
    assert not orphan.exists()
    assert marker.read_bytes() == b"new-seal!"


def test_symlink_seal_preserved(tmp_path, monkeypatch):
    orphan = archive(tmp_path)
    target = tmp_path / "unrelated"
    target.write_bytes(b"retained")
    marker = orphan.with_name(orphan.name + ".ok")
    symlink(target, marker)
    assert run(tmp_path, monkeypatch) == 0
    assert not orphan.exists()
    assert marker.is_symlink() and target.read_bytes() == b"retained"


def test_checkpoint_docker_c1_survives_host_replacement_by_c2(
    tmp_path,
    monkeypatch,
    fake_docker,
    capsys,
):
    retained_c1 = archive(tmp_path, "a")
    current_c2 = archive(tmp_path, "b")
    orphan = archive(tmp_path, "c")
    (tmp_path / "machine.json").write_text(json.dumps({"environment_ref": current_c2.name}))
    assert run(tmp_path, monkeypatch) == 0
    assert retained_c1.exists() and current_c2.exists()
    assert not orphan.exists()
    report = json.loads(capsys.readouterr().out)
    assert report["checkpoint_volumes"] == 1 and report["checkpoint_files"] == 1


@pytest.fixture
def fake_docker(monkeypatch):
    import io
    import tarfile
    from types import SimpleNamespace

    import docker

    volume_name = "omnia-cell-11111111111141118111111111111111-checkpoints"
    volume = SimpleNamespace(
        name=volume_name,
        attrs={
            "Labels": {"omnia.resource_kind": "checkpoints", "omnia.workspace_id": WORKSPACE},
            "Driver": "local",
            "Mountpoint": f"/var/lib/docker/volumes/{volume_name}/_data",
        },
    )
    state = SimpleNamespace(
        volumes=[volume],
        raw=json.dumps({"reference": {"archive": "a" * 32 + ".tar"}}).encode(),
        exit_code=0,
        link_target="",
        member_type=tarfile.REGTYPE,
        output=b"/checkpoints/c1/machine.json\0",
        helpers=[],
        lookups=[],
        closed=0,
        creates=[],
        reads=[],
        missing_image=False,
        listed=0,
    )

    class Helper:
        def __init__(self):
            self.removed = False
            self.started = False

        def start(self):
            self.started = True

        def exec_run(self, command):
            assert command[0:2] == ["/bin/sh", "-c"]
            return SimpleNamespace(exit_code=state.exit_code, output=state.output)

        def get_archive(self, path):
            state.reads.append(path)
            payload = io.BytesIO()
            with tarfile.open(fileobj=payload, mode="w") as tar:
                member = tarfile.TarInfo("machine.json")
                member.type = state.member_type
                member.size = len(state.raw)
                tar.addfile(member, io.BytesIO(state.raw))
            return iter([payload.getvalue()]), {
                "size": len(state.raw),
                "linkTarget": state.link_target,
            }

        def remove(self, *, force, v):
            assert force is True and v is False
            self.removed = True

    def create(image, command, **kwargs):
        state.creates.append((image, command, kwargs))
        helper = Helper()
        state.helpers.append(helper)
        return helper

    def get_image(name):
        state.lookups.append(name)
        if state.missing_image:
            raise docker.errors.ImageNotFound("private daemon response")
        return SimpleNamespace(id="sha256:" + "f" * 64)

    def list_volumes():
        state.listed += 1
        return state.volumes

    def close():
        state.closed += 1

    client = SimpleNamespace(
        volumes=SimpleNamespace(list=list_volumes),
        images=SimpleNamespace(get=get_image),
        containers=SimpleNamespace(create=create),
        close=close,
    )
    monkeypatch.setattr(docker, "from_env", lambda **kwargs: client)
    return state


@pytest.mark.parametrize("prefix", ["omnia", "yleum"])
def test_checkpoint_docker_legacy_labels_readonly_local_helper(fake_docker, prefix):
    state = fake_docker
    state.volumes[0].attrs["Labels"] = {
        f"{prefix}.resource_kind": "checkpoints",
        f"{prefix}.workspace_id": WORKSPACE,
    }
    refs, volumes, files, missing = gc.checkpoint_references(set(), set())
    assert refs == {"a" * 32 + ".tar"}
    assert (volumes, files, missing) == (1, 1, 0)
    assert state.lookups == ["alpine:latest"]
    image, command, options = state.creates[0]
    assert image == "sha256:" + "f" * 64
    assert command == ["/bin/sh", "-c", "sleep 90"]
    assert options["network_mode"] == "none" and options["read_only"] is True
    assert options["cap_drop"] == ["ALL"]
    assert options["security_opt"] == ["no-new-privileges:true"]
    assert options["mem_limit"] == "96m" and options["pids_limit"] == 16
    assert options["mounts"][0]["Type"] == "bind"
    assert options["mounts"][0]["ReadOnly"] is True
    assert state.reads == ["/checkpoints/c1/machine.json"]
    assert all(helper.removed for helper in state.helpers)
    assert state.closed == 1


@pytest.mark.parametrize(
    "fault",
    [
        "malformed",
        "symlink",
        "tar_symlink",
        "oversize",
        "helper",
        "missing_volume",
        "unknown_identity",
        "missing_image",
    ],
)
def test_checkpoint_docker_bad_inventory_aborts_without_deletion(
    tmp_path,
    monkeypatch,
    capsys,
    fake_docker,
    fault,
):
    import tarfile

    state = fake_docker
    orphan = archive(tmp_path, "c")
    (tmp_path / "state.json").write_text(
        json.dumps(
            {
                "resource_names": {"checkpoint_volume": state.volumes[0].name},
            }
        )
    )
    if fault == "malformed":
        state.raw = b"{bad"
    elif fault == "symlink":
        state.link_target = "outside"
    elif fault == "tar_symlink":
        state.member_type = tarfile.SYMTYPE
    elif fault == "oversize":
        state.raw = b"x" * 500
        monkeypatch.setattr(gc, "MAX_STATE_FILE_BYTES", 200)
    elif fault == "helper":
        state.exit_code = 1
    elif fault == "missing_volume":
        state.volumes = []
    elif fault == "unknown_identity":
        state.volumes[0].attrs["Labels"].pop("omnia.workspace_id")
    else:
        state.missing_image = True
    assert run(tmp_path, monkeypatch) == 1
    assert orphan.exists()
    assert all(helper.removed for helper in state.helpers)
    report = json.loads(capsys.readouterr().out)
    assert report["error"] == "checkpoint_inventory_failed"
    assert report["deleted_files"] == 0


def test_checkpoint_docker_inventory_refreshed_inside_workspace_lock(
    tmp_path,
    monkeypatch,
    fake_docker,
):
    from contextlib import asynccontextmanager

    orphan = archive(tmp_path, "a")
    fake_docker.output = b""
    original = gc.WorkspaceOperationLock.hold

    @asynccontextmanager
    async def appeared_while_waiting(self, workspace_id):
        async with original(self, workspace_id):
            fake_docker.output = b"/checkpoints/c1/machine.json\0"
            yield

    monkeypatch.setattr(gc.WorkspaceOperationLock, "hold", appeared_while_waiting)
    assert run(tmp_path, monkeypatch) == 0
    assert orphan.exists()
    assert fake_docker.listed == 2
    assert all(helper.removed for helper in fake_docker.helpers)


def planned_cell(root: Path, *, checkpoint=False) -> Path:
    from uuid import UUID

    from yleum_orchestrator.core.cell_resources import CellResourceNames, LifecycleMutation
    from yleum_orchestrator.core.workspace_provider import WorkspaceSpec
    from yleum_orchestrator.services.cell_state import CellStateStore

    workspace = UUID(WORKSPACE)
    store = CellStateStore(root / "project-cells")
    spec = WorkspaceSpec(
        workspace_id=workspace,
        project_id=UUID(int=2),
        owner_id=UUID(int=3),
        profile_version="docker-owner-cell-resources-v1",
        generation_run_id=UUID(int=4),
    )
    mutation = LifecycleMutation(UUID(int=5), 1, "a" * 64)
    store.begin(
        spec,
        mutation,
        kind="checkpoint" if checkpoint else "ensure",
        phase="planned",
        resource_names=CellResourceNames.for_workspace(workspace),
        checkpoint_ref="c1" if checkpoint else None,
    )
    store.mark_failed(
        workspace,
        mutation,
        phase="failed",
        provider_ref=None,
        bundle_state="resources_failed",
        detail="provisioning interrupted",
    )
    return store.workspace_path(workspace)


def test_checkpoint_docker_missing_planned_allows_unreferenced_capture_cleanup(
    tmp_path,
    monkeypatch,
    fake_docker,
    capsys,
):
    planned = archive(tmp_path, "d")
    healthy = archive(tmp_path, "c")
    other = healthy.parent.parent / "22222222-2222-4222-8222-222222222222"
    other.mkdir()
    healthy = healthy.rename(other / healthy.name)
    planned_cell(tmp_path)
    fake_docker.volumes = []
    assert run(tmp_path, monkeypatch) == 0
    assert not planned.exists()
    assert not healthy.exists()
    report = json.loads(capsys.readouterr().out)
    assert report["missing_planned_volumes"] == 1


def test_checkpoint_docker_retained_missing_still_aborts(tmp_path, monkeypatch, fake_docker):
    orphan = archive(tmp_path, "c")
    planned_cell(tmp_path, checkpoint=True)
    fake_docker.volumes = []
    assert run(tmp_path, monkeypatch) == 1
    assert orphan.exists()


def test_checkpoint_docker_unknown_declaration_cannot_use_planned_exemption(
    tmp_path,
    monkeypatch,
    fake_docker,
):
    orphan = archive(tmp_path, "c")
    path = planned_cell(tmp_path)
    payload = json.loads(path.read_text())
    (tmp_path / "other.json").write_text(
        json.dumps(
            {
                "checkpoint_volume": payload["workspace"]["resource_names"]["checkpoint_volume"],
            }
        )
    )
    fake_docker.volumes = []
    assert run(tmp_path, monkeypatch) == 1
    assert orphan.exists()


def test_checkpoint_docker_missing_planned_rechecked_under_lock(
    tmp_path,
    monkeypatch,
    fake_docker,
):
    from contextlib import asynccontextmanager

    orphan = archive(tmp_path, "c")
    fake_docker.volumes = []
    original = gc.WorkspaceOperationLock.hold

    @asynccontextmanager
    async def planned_while_waiting(self, workspace_id):
        async with original(self, workspace_id):
            planned_cell(tmp_path)
            (tmp_path / "machine.json").write_text(json.dumps({"environment_ref": orphan.name}))
            yield

    monkeypatch.setattr(gc.WorkspaceOperationLock, "hold", planned_while_waiting)
    assert run(tmp_path, monkeypatch) == 0
    assert orphan.exists()


@pytest.mark.parametrize("invalid", ["version", "filename", "unknown_shape", "nested_ref"])
def test_checkpoint_docker_planned_exemption_requires_complete_known_journal(
    tmp_path,
    monkeypatch,
    fake_docker,
    invalid,
):
    orphan = archive(tmp_path, "c")
    path = planned_cell(tmp_path)
    payload = json.loads(path.read_text())
    if invalid == "version":
        payload["version"] = 2
    elif invalid == "filename":
        path = path.rename(path.with_name("other.json"))
    elif invalid == "unknown_shape":
        payload["workspace"]["unrecognized"] = True
    else:
        payload["workspace"]["operations"][0]["observed_resources"] = {"checkpoint_ref": "c1"}
    path.write_text(json.dumps(payload))
    fake_docker.volumes = []
    assert run(tmp_path, monkeypatch) == 1
    assert orphan.exists()
