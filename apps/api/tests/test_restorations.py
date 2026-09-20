"""Restoration contracts; DB cases require the disposable conftest database."""

import base64
import hashlib
import json
from uuid import uuid4

import pytest

from omnia_api.services import repo


def source_binding(**changes):
    value = {
        "version": 2,
        "serving_route_digest": "1" * 64,
        "serving_release_digest": "2" * 64,
        "controller_resource_digest": "3" * 64,
        "controller_incarnation_digest": "4" * 64,
        "controller_generation_digest": "5" * 64,
        "provider_digest": "6" * 64,
        "source_artifact_digest": "7" * 64,
        "database_identity_digest": "8" * 64,
        "database_schema_digest": "9" * 64,
        "database_role_binding_digest": "a" * 64,
        "database_system_identifier": "7612345678901234567",
        "database_export_digest": "b" * 64,
        "source_business_inventory_digest": "c" * 64,
        "candidate_business_inventory_digest": "c" * 64,
        "source_technical_inventory_digest": "d" * 64,
        "candidate_technical_inventory_digest": "d" * 64,
        "candidate_artifact_digest": "e" * 64,
    }
    value.update(changes)
    return value


def source_binding_v3(**changes):
    value = {
        **source_binding(version=3),
        "database_strategy": "preserve_current",
        "witness_digest": None,
        "target_database_artifact_digest": None,
    }
    value.update(changes)
    return value


@pytest.fixture(autouse=True)
def ready_source_resources(monkeypatch):
    from types import SimpleNamespace

    from omnia_api.services import project_cell_runtime

    async def resources(workspace_id):
        return SimpleNamespace(state="resources_ready")

    monkeypatch.setattr(project_cell_runtime, "_get_cell_resources", resources)


def test_prepare_does_not_move_head_and_activation_replays():
    project, operation = uuid4(), uuid4()
    old = repo.init_from_files(project, {"page.txt": "old", "empty.txt": ""}, "seed")
    head = repo.commit_files(project, {"page.txt": "new", "new.txt": "new"}, "edit", old)
    prepared = repo.prepare_restore_commit(project, old, head, operation)
    assert prepared == repo.prepare_restore_commit(project, old, head, operation)
    with repo._open_workdir(project, must_exist=True) as path:
        import pygit2

        assert str(pygit2.Repository(str(path)).head.target) == old
    assert {
        item["path"]: base64.b64decode(item["content_base64"]) for item in prepared["files"]
    } == {"page.txt": b"old", "empty.txt": b""}
    planned = prepared["commit_sha"]
    assert planned != old and planned != head
    assert repo.activate_restore_commit(project, planned, head, operation) == planned
    assert repo.activate_restore_commit(project, planned, head, operation) == planned
    assert repo.read_files(project, planned) == {"page.txt": "old", "empty.txt": ""}


def test_prepare_adaptation_commit_is_exact_and_replays_after_lost_upload(monkeypatch):
    project, operation = uuid4(), uuid4()
    base = repo.init_from_files(
        project,
        {"keep.txt": "old", "delete.txt": "gone", "empty.txt": "was-not-empty"},
        "seed",
    )
    binary_asset = b"\x89PNG\r\n\x1a\n\x00omnia-adaptation"
    with repo._open_workdir(project, must_exist=True) as path:
        import pygit2

        git = pygit2.Repository(str(path))
        parent = git[base]
        index = pygit2.Index()
        index.read_tree(parent.tree)
        asset_id = git.create_blob(binary_asset)
        index.add(pygit2.IndexEntry("public/logo.png", asset_id, 0o100644))
        tree_id = index.write_tree(git)
        signature = pygit2.Signature("Omnia", "dev@omnia.local", parent.commit_time, 0)
        base = str(
            git.create_commit(
                "HEAD", signature, signature, "binary seed", tree_id, [parent.id]
            )
        )
        repo._upload(project, path)
    original_upload = repo._upload
    attempts = 0

    def flaky_upload(project_id, path):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("upload acknowledgement lost")
        original_upload(project_id, path)

    monkeypatch.setattr(repo, "_upload", flaky_upload)
    exact = {"keep.txt": "adapted", "empty.txt": "", "new.txt": "new"}
    with pytest.raises(OSError, match="acknowledgement"):
        repo.prepare_restoration_adaptation_commit(project, exact, base, operation)
    planned = repo.prepare_restoration_adaptation_commit(project, exact, base, operation)
    assert planned == repo.prepare_restoration_adaptation_commit(
        project, exact, base, operation
    )
    assert repo.read_files(project, planned) == exact
    assert repo.read_file(project, planned, "public/logo.png") == binary_asset
    manifest = [
        {
            "path": path,
            "size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
        for path, payload in {
            **{path: content.encode() for path, content in exact.items()},
            "public/logo.png": binary_asset,
        }.items()
    ]
    manifest.sort(key=lambda row: row["path"])
    expected_manifest_digest = hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert (
        repo.restoration_adaptation_source_manifest_digest(project, planned)
        == expected_manifest_digest
    )
    with repo._open_workdir(project, must_exist=True) as path:
        import pygit2

        git = pygit2.Repository(str(path))
        assert str(git.head.target) == base
        adaptation_ref = git.references[
            f"refs/omnia/restoration-adaptations/{operation.hex}"
        ]
        assert str(adaptation_ref.target) == planned
    with pytest.raises(ValueError, match="identity"):
        repo.prepare_restoration_adaptation_commit(
            project, {**exact, "new.txt": "changed"}, base, operation
        )


def test_prepare_overlays_current_platform_files_and_removes_retired_paths():
    project, operation = uuid4(), uuid4()
    old = repo.init_from_files(
        project,
        {
            "src/app/page.tsx": "historical product",
            "src/lib/max/session.ts": "historical platform core",
            "src/lib/secure-data/store.ts": "retired platform core",
        },
        "seed",
    )
    head = repo.commit_files(project, {"src/app/page.tsx": "current product"}, "edit", old)

    prepared = repo.prepare_restore_commit(
        project,
        old,
        head,
        operation,
        overrides={"src/lib/max/session.ts": "current platform core"},
        deletes=("src/lib/secure-data/store.ts",),
    )

    assert prepared == repo.prepare_restore_commit(
        project,
        old,
        head,
        operation,
        overrides={"src/lib/max/session.ts": "current platform core"},
        deletes=("src/lib/secure-data/store.ts",),
    )
    assert repo.read_files(project, prepared["commit_sha"]) == {
        "src/app/page.tsx": "historical product",
        "src/lib/max/session.ts": "current platform core",
    }
    with pytest.raises(ValueError, match="identity"):
        repo.prepare_restore_commit(
            project,
            old,
            head,
            operation,
            overrides={"src/lib/max/session.ts": "changed platform core"},
            deletes=("src/lib/secure-data/store.ts",),
        )


def test_restore_git_rejects_wrong_parent_and_operation_rebinding():
    project, operation = uuid4(), uuid4()
    old = repo.init_from_files(project, {"page.txt": "old"}, "seed")
    head = repo.commit_files(project, {"page.txt": "new"}, "edit", old)
    prepared = repo.prepare_restore_commit(project, old, head, operation)
    with pytest.raises(ValueError, match="identity"):
        repo.prepare_restore_commit(project, head, head, operation)
    latest = repo.commit_files(project, {"page.txt": "latest"}, "concurrent", head)
    with pytest.raises(ValueError, match="identity"):
        repo.activate_restore_commit(project, prepared["commit_sha"], latest, operation)
    assert repo.read_files(project, latest)["page.txt"] == "latest"


def test_restore_bundle_preserves_binary_bytes(tmp_path):
    import pygit2

    project, operation = uuid4(), uuid4()
    old = repo.init_from_files(project, {"page.txt": "old"}, "seed")
    with repo._open_workdir(project, must_exist=True) as path:
        git = pygit2.Repository(str(path))
        tree = git.TreeBuilder(git[old].tree)
        tree.insert("image.bin", git.create_blob(b"\x00\xff\x80"), pygit2.GIT_FILEMODE_BLOB)
        old = str(
            git.create_commit(
                "HEAD",
                repo._signature(),
                repo._signature(),
                "binary",
                tree.write(),
                [git.head.target],
            )
        )
        repo._upload(project, path)
    prepared = repo.prepare_restore_commit(project, old, old, operation)
    binary = next(item for item in prepared["files"] if item["path"] == "image.bin")
    assert base64.b64decode(binary["content_base64"]) == b"\x00\xff\x80"
    assert binary["mode"] == 0o100644
    import hashlib

    current_binary = next(item for item in prepared["current_files"] if item["path"] == "image.bin")
    assert current_binary == {
        "path": "image.bin",
        "mode": 0o100644,
        "sha256": hashlib.sha256(b"\x00\xff\x80").hexdigest(),
    }


def test_restoration_request_and_runtime_contracts_are_strict():
    from pydantic import ValidationError

    from omnia_api.schemas.restoration import RestoreRequest, RuntimeRestoration

    with pytest.raises(ValidationError):
        RestoreRequest(
            target_version_id=uuid4(),
            expected_draft_snapshot_id=uuid4(),
            idempotency_key="short",
            owner_id=str(uuid4()),
        )
    with pytest.raises(ValidationError):
        RuntimeRestoration(
            operation_id=uuid4(),
            workspace_id=uuid4(),
            project_id=uuid4(),
            owner_id=uuid4(),
            state="completed",
            phase="done",
            revision=1,
            candidate_id=uuid4(),
            report=None,
            error=None,
            can_apply=False,
            can_cancel=False,
        )


def test_adaptive_cancel_projection_uses_controller_ponr_not_legacy_exact_flag():
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from omnia_api.services.restorations import public_operation

    operation = SimpleNamespace(
        id=uuid4(),
        project_id=uuid4(),
        source_version_id=uuid4(),
        source_snapshot_id=uuid4(),
        base_draft_snapshot_id=uuid4(),
        state="applying",
        phase="activation_intent",
        updated_at=datetime.now(UTC),
        revision=1,
        candidate_id=None,
        execution_policy="manual",
        selected_branch="adaptive",
        adaptation_run_id=uuid4(),
        report=None,
        source_binding=None,
        source_binding_digest=None,
        apply_digest=None,
        runtime_result={"can_cancel": False},
        activation_effects_admitted=False,
        applied_version_id=None,
        applied_snapshot_id=None,
        error=None,
    )

    assert public_operation(operation).can_cancel is True
    operation.activation_effects_admitted = True
    assert public_operation(operation).can_cancel is False
    operation.activation_effects_admitted = False
    operation.phase = "activation_cancel"
    assert public_operation(operation).can_cancel is False


def test_activation_receipt_journal_accepts_phase_advance_and_rejects_regression():
    from types import SimpleNamespace

    from omnia_api.core.errors import ApiError
    from omnia_api.schemas.restoration import (
        RestorationAdaptationActivationStatus,
        canonical_activation_digest,
    )
    from omnia_api.services.restorations import _validate_activation_receipt_progression
    from tests.test_orchestrator_client import _activation_contract

    _, _command, activated_raw = _activation_contract()

    def status(state: str) -> RestorationAdaptationActivationStatus:
        raw = dict(activated_raw)
        raw["state"] = state
        raw["effects_admitted"] = state in {"target_writers_admitted", "activated"}
        raw["health_digest"] = "9" * 64 if state == "activated" else None
        offer = raw["offer"]
        receipt = {
            "state": state,
            "effects_admitted": raw["effects_admitted"],
            "operation_id": offer["operation_id"],
            "generation_run_id": offer["generation_run_id"],
            "project_id": offer["project_id"],
            "owner_id": offer["owner_id"],
            "activation_id": offer["activation_id"],
            "activation_digest": raw["activation_digest"],
            "proof_digest": offer["proof_digest"],
            "fencing_epoch": raw["fencing_epoch"],
            "source_volume_identity": raw["source_volume_identity"],
            "target_volume_identity": raw["target_volume_identity"],
            "health_digest": raw["health_digest"],
        }
        raw["receipt_digest"] = canonical_activation_digest(receipt)
        return RestorationAdaptationActivationStatus.model_validate(raw)

    prepared = status("target_prepared")
    activated = status("activated")
    operation = SimpleNamespace(activation_receipt=prepared.model_dump(mode="json"))
    _validate_activation_receipt_progression(operation, activated)
    operation.activation_receipt = activated.model_dump(mode="json")
    with pytest.raises(ApiError, match="regressed"):
        _validate_activation_receipt_progression(operation, prepared)


def test_restore_request_policy_is_strict_and_changes_the_durable_request_digest():
    from pydantic import ValidationError

    from omnia_api.schemas.restoration import RestoreRequest
    from omnia_api.services.restorations import _digest, restoration_request_digest

    identity = {
        "target_version_id": uuid4(),
        "expected_draft_snapshot_id": uuid4(),
        "idempotency_key": "one-click-restore",
    }
    manual = RestoreRequest(**identity)
    automatic = RestoreRequest(**identity, execution_policy="automatic_when_safe")

    assert manual.execution_policy == "manual"
    assert automatic.execution_policy == "automatic_when_safe"
    project_id, owner_id = uuid4(), uuid4()
    legacy_wire = manual.model_dump(mode="json")
    legacy_wire.pop("execution_policy")
    legacy = _digest(
        {
            "project_id": str(project_id),
            "owner_id": str(owner_id),
            **legacy_wire,
        }
    )
    assert restoration_request_digest(project_id, owner_id, manual) == legacy
    assert restoration_request_digest(
        project_id, owner_id, automatic
    ) != legacy
    with pytest.raises(ValidationError):
        RestoreRequest(**identity, execution_policy="automatic_unverified")


def test_runtime_observation_must_match_planned_activation():
    from omnia_api.schemas.restoration import RuntimeRestoration
    from omnia_api.services.restorations import validate_runtime_response

    identity = {
        key: str(uuid4()) for key in ("operation_id", "workspace_id", "project_id", "owner_id")
    }
    request = {**identity, "planned_commit_sha": "a" * 40, "fencing_epoch": 9}
    candidate = uuid4()
    response = RuntimeRestoration(
        **identity,
        state="completed",
        phase="done",
        revision=3,
        candidate_id=candidate,
        report=None,
        error=None,
        can_apply=False,
        can_cancel=False,
        observed={
            "candidate_id": candidate,
            "source_commit_sha": "b" * 40,
            "fencing_epoch": 9,
            "applied": True,
        },
    )
    with pytest.raises(ValueError, match="activation"):
        validate_runtime_response(request, response)


class FakeRuntime:
    """Deterministic controller port; never an application/database acceptance proof."""

    def __init__(self):
        self.result = None
        self.prepares = 0
        self.applies = 0
        self.cancel_calls = 0
        self.lose_apply_reply = False

    async def prepare(self, request):
        from omnia_api.schemas.restoration import (
            RuntimeRestoration,
            RuntimeSourceBindingV2,
            RuntimeSourceBindingV3,
        )

        self.prepares += 1
        contract_version = request["binding_contract_version"]
        binding = (
            RuntimeSourceBindingV3.model_validate(source_binding_v3())
            if contract_version == 3
            else RuntimeSourceBindingV2.model_validate(source_binding())
        )
        self.result = RuntimeRestoration(
            **{
                key: request[key]
                for key in ("operation_id", "workspace_id", "project_id", "owner_id")
            },
            state="ready",
            phase="checked",
            revision=1,
            candidate_id=uuid4(),
            report={"revision": 1, "mode": "exact", "retained_data": ["Current business data"]},
            error=None,
            can_apply=True,
            can_cancel=True,
            binding=binding,
            binding_digest=binding.digest(),
        )
        return self.result

    async def status(self, request):
        from omnia_api.core.errors import ApiError

        if self.result is None:
            raise ApiError("not_found", "operation not found", 404)
        return self.result

    async def apply(self, request):
        self.applies += 1
        self.result = self.result.model_copy(
            update={
                "state": "completed",
                "phase": "complete",
                "revision": 2,
                "can_apply": False,
                "can_cancel": False,
            }
        )
        from omnia_api.schemas.restoration import RuntimeObserved

        self.result.observed = RuntimeObserved(
            candidate_id=request["candidate_id"],
            source_commit_sha=request["planned_commit_sha"],
            fencing_epoch=request["fencing_epoch"],
            binding_digest=request["binding_digest"],
            applied=True,
            source_revision="b" * 64,
        )
        if self.lose_apply_reply:
            self.lose_apply_reply = False
            raise TimeoutError("reply lost after controller activation")
        return self.result

    async def cancel(self, request):
        self.cancel_calls += 1
        self.result = self.result.model_copy(
            update={
                "state": "cancelled",
                "phase": "cancelled",
                "revision": 2,
                "can_apply": False,
                "can_cancel": False,
            }
        )
        return self.result


def test_runtime_source_binding_digest_is_canonical_and_secret_free():
    from pydantic import ValidationError

    from omnia_api.schemas.restoration import RuntimeSourceBindingV2, RuntimeSourceBindingV3

    first = RuntimeSourceBindingV2.model_validate(source_binding())
    second = RuntimeSourceBindingV2.model_validate(dict(reversed(list(source_binding().items()))))
    assert first.digest() == second.digest()
    assert "secret" not in first.model_dump_json().lower()
    v3 = RuntimeSourceBindingV3.model_validate(source_binding_v3())
    assert v3.version == 3 and v3.digest() != first.digest()
    replacement = RuntimeSourceBindingV3.model_validate(
        source_binding_v3(
            database_strategy="replace_verified_empty",
            witness_digest="f" * 64,
            target_database_artifact_digest="0" * 64,
        )
    )
    assert replacement.database_strategy == "replace_verified_empty"
    with pytest.raises(ValidationError, match="requires both"):
        RuntimeSourceBindingV3.model_validate(
            source_binding_v3(
                database_strategy="replace_verified_empty",
                witness_digest=None,
                target_database_artifact_digest="f" * 64,
            )
        )
    with pytest.raises(ValidationError, match="must not carry"):
        RuntimeSourceBindingV3.model_validate(
            source_binding_v3(witness_digest="f" * 64)
        )


@pytest.mark.parametrize("contract_version", [2, 3])
def test_runtime_ready_requires_the_exact_requested_binding_contract(contract_version):
    from omnia_api.schemas.restoration import (
        RuntimeRestoration,
        RuntimeSourceBindingV2,
        RuntimeSourceBindingV3,
    )
    from omnia_api.services.restorations import validate_runtime_response

    identity = {
        key: str(uuid4())
        for key in ("operation_id", "workspace_id", "project_id", "owner_id")
    }
    binding = (
        RuntimeSourceBindingV3.model_validate(source_binding_v3())
        if contract_version == 3
        else RuntimeSourceBindingV2.model_validate(source_binding())
    )
    result = RuntimeRestoration(
        **identity,
        state="ready",
        phase="checked",
        revision=1,
        candidate_id=uuid4(),
        report={"revision": 1, "mode": "exact"},
        can_apply=True,
        can_cancel=True,
        binding=binding.model_dump(mode="json"),
        binding_digest=binding.digest(),
    )
    assert result.binding is not None
    assert result.binding.version == contract_version
    request = {
        **identity,
        "binding_contract_version": contract_version,
        "planned_commit_sha": "a" * 40,
        "fencing_epoch": 7,
    }
    validate_runtime_response(request, result)
    with pytest.raises(ValueError, match="binding is required"):
        validate_runtime_response(
            request,
            result.model_copy(update={"binding": None, "binding_digest": None}),
        )

    wrong_binding = (
        RuntimeSourceBindingV2.model_validate(source_binding())
        if contract_version == 3
        else RuntimeSourceBindingV3.model_validate(source_binding_v3())
    )
    with pytest.raises(ValueError, match="binding contract mismatch"):
        validate_runtime_response(
            request,
            result.model_copy(
                update={"binding": wrong_binding, "binding_digest": wrong_binding.digest()}
            ),
        )


async def restoration_fixture(db):
    from omnia_api.models.project import Project
    from omnia_api.models.project_cell import ProjectCellWorkspace
    from omnia_api.models.project_version import ProjectVersion
    from omnia_api.models.snapshot import Snapshot
    from omnia_api.models.user import User
    from omnia_api.schemas.restoration import RestoreRequest

    user = User(email=f"{uuid4()}@restoration.test")
    db.add(user)
    await db.flush()
    project = Project(
        owner_id=user.id, name="Restoration", slug=str(uuid4()), template="max_miniapp"
    )
    db.add(project)
    await db.flush()
    old_sha = repo.init_from_files(project.id, {"page.txt": "old", "empty.txt": ""}, "old")
    new_sha = repo.commit_files(project.id, {"page.txt": "new", "added.txt": "new"}, "new", old_sha)
    old = Snapshot(project_id=project.id, commit_sha=old_sha, model_id="original-model")
    db.add(old)
    await db.flush()
    current = Snapshot(project_id=project.id, commit_sha=new_sha, parent_id=old.id)
    db.add(current)
    await db.flush()
    project.current_snapshot_id = current.id
    version = ProjectVersion(
        project_id=project.id,
        number=1,
        snapshot_id=old.id,
        commit_sha=old_sha,
        prompt_text="Old version",
        status="ready",
    )
    workspace = ProjectCellWorkspace(
        project_id=project.id,
        owner_id=user.id,
        provider="docker",
        state="ready",
        fencing_epoch=7,
        version=1,
    )
    db.add_all([version, workspace])
    await db.commit()
    request = RestoreRequest(
        target_version_id=version.id,
        expected_draft_snapshot_id=current.id,
        idempotency_key="prepare-restore-1",
    )
    return user, project, old, current, version, workspace, request


async def test_db_restoration_prepares_without_head_change_then_applies_once(db_session):
    from sqlalchemy import func, select

    from omnia_api.models.project_version import ProjectVersion
    from omnia_api.models.snapshot import Snapshot
    from omnia_api.schemas.restoration import RestoreApplyRequest
    from omnia_api.services import restorations as service

    owner, project, old, current, _, workspace, request = await restoration_fixture(db_session)
    runtime = FakeRuntime()
    operation = await service.create_restoration(db_session, project.id, owner.id, request, runtime)
    assert operation.state == "ready" and operation.can_apply
    assert project.current_snapshot_id == current.id
    assert workspace.fencing_epoch == 7
    repeated = await service.create_restoration(db_session, project.id, owner.id, request, runtime)
    assert repeated.id == operation.id and runtime.prepares == 1
    applied_request = RestoreApplyRequest(
        report_revision=1, expected_draft_snapshot_id=current.id, idempotency_key="apply-restore-1"
    )
    result = await service.apply_restoration(
        db_session, project.id, owner.id, operation.id, applied_request, runtime
    )
    assert result.state == "completed" and not result.can_apply
    assert result.applied_snapshot_id not in {current.id, old.id}
    assert project.current_snapshot_id == result.applied_snapshot_id
    assert workspace.fencing_epoch == 8
    restored_files = repo.read_files(
        project.id, (await db_session.get(Snapshot, result.applied_snapshot_id)).commit_sha
    )
    assert restored_files["page.txt"] == "old"
    assert restored_files["empty.txt"] == ""
    assert "onConflictDoNothing" in restored_files["src/lib/max/session.ts"]
    assert str(project.id) in restored_files["src/lib/max/session.ts"]
    replay = await service.apply_restoration(
        db_session, project.id, owner.id, operation.id, applied_request, runtime
    )
    assert replay == result and runtime.applies == 1 and workspace.fencing_epoch == 8
    assert await db_session.scalar(select(func.count()).select_from(Snapshot)) == 3
    assert await db_session.scalar(select(func.count()).select_from(ProjectVersion)) == 2
    await db_session.refresh(current)
    assert current.commit_sha != old.commit_sha  # Existing source history is preserved.


async def test_db_legacy_manual_idempotency_replays_but_cannot_be_upgraded_to_automatic(
    db_session,
):
    from omnia_api.core.errors import ApiError
    from omnia_api.models.restoration import Restoration
    from omnia_api.services import restorations as service

    owner, project, _, _, _, _, request = await restoration_fixture(db_session)
    runtime = FakeRuntime()
    created = await service.create_restoration(
        db_session, project.id, owner.id, request, runtime
    )
    row = await db_session.get(Restoration, created.id)
    assert row.execution_policy == "manual"

    replay = await service.create_restoration(
        db_session, project.id, owner.id, request, runtime
    )
    assert replay.id == created.id and runtime.prepares == 1

    automatic = request.model_copy(
        update={"execution_policy": "automatic_when_safe"}
    )
    with pytest.raises(ApiError, match="idempotency key was reused"):
        await service.create_restoration(
            db_session, project.id, owner.id, automatic, runtime
        )


async def test_db_restoration_overlays_complete_current_max_kit_when_configured(db_session):
    from omnia_api.models.max_project_config import MaxProjectConfig
    from omnia_api.models.restoration import Restoration
    from omnia_api.services import restorations as service

    owner, project, _, _, _, _, request = await restoration_fixture(db_session)
    db_session.add(
        MaxProjectConfig(
            project_id=project.id,
            owner_id=owner.id,
            config_version=2,
            managed_kit_version=19,
            config={
                "app_name": "Restored current platform",
                "app_type": "custom",
                "summary": "Historical product with the current managed runtime",
            },
        )
    )
    await db_session.commit()

    runtime = FakeRuntime()
    operation = await service.create_restoration(db_session, project.id, owner.id, request, runtime)
    row = await db_session.get(Restoration, operation.id)
    restored_files = repo.read_files(project.id, row.planned_commit_sha)

    assert restored_files["page.txt"] == "old"
    assert "onConflictDoNothing" in restored_files["src/lib/max/session.ts"]
    assert "Restored current platform" in restored_files["src/lib/omnia/max-config.ts"]
    assert runtime.prepares == 1


async def test_db_v3_binding_is_durable_and_required_before_apply(db_session):
    from omnia_api.core.errors import ApiError
    from omnia_api.models.restoration import Restoration
    from omnia_api.schemas.restoration import RestoreApplyRequest
    from omnia_api.services import restorations as service

    owner, project, _, current, _, _, request = await restoration_fixture(db_session)
    runtime = FakeRuntime()
    operation = await service.create_restoration(db_session, project.id, owner.id, request, runtime)
    row = await db_session.get(Restoration, operation.id)
    assert row.source_binding["version"] == 3
    assert len(row.source_binding_digest) == 64
    assert row.request_payload["binding_contract_version"] == 3

    row.source_binding = None
    row.source_binding_digest = None
    await db_session.commit()
    with pytest.raises(ApiError, match="binding"):
        await service.apply_restoration(
            db_session,
            project.id,
            owner.id,
            operation.id,
            RestoreApplyRequest(
                report_revision=1,
                expected_draft_snapshot_id=current.id,
                idempotency_key="unbound-ready-apply",
            ),
            runtime,
        )
    assert runtime.applies == 0


def test_v2_runtime_observation_requires_saved_binding_digest():
    from omnia_api.schemas.restoration import RuntimeRestoration, RuntimeSourceBindingV2
    from omnia_api.services.restorations import validate_runtime_response

    identity = {
        key: str(uuid4()) for key in ("operation_id", "workspace_id", "project_id", "owner_id")
    }
    binding = RuntimeSourceBindingV2.model_validate(source_binding())
    candidate = uuid4()
    request = {
        **identity,
        "binding_contract_version": 2,
        "binding_digest": binding.digest(),
        "planned_commit_sha": "a" * 40,
        "fencing_epoch": 9,
        "candidate_id": str(candidate),
    }
    response = RuntimeRestoration(
        **identity,
        state="completed",
        phase="done",
        revision=3,
        candidate_id=candidate,
        can_apply=False,
        can_cancel=False,
        binding=binding,
        binding_digest=binding.digest(),
        observed={
            "candidate_id": candidate,
            "source_commit_sha": "a" * 40,
            "fencing_epoch": 9,
            "binding_digest": "f" * 64,
            "applied": True,
        },
    )
    with pytest.raises(ValueError, match="binding"):
        validate_runtime_response(request, response)


@pytest.mark.parametrize(
    "case", ["legacy", "empty", "present", "different_state", "invalid_extra", "invalid_flag"]
)
async def test_db_same_revision_receipt_is_noop_only_when_semantically_identical(db_session, case):
    from copy import deepcopy

    from sqlalchemy.orm.attributes import flag_modified

    from omnia_api.models.restoration import Restoration
    from omnia_api.services import restorations as service

    owner, project, _, current, _, workspace, request = await restoration_fixture(db_session)
    runtime = FakeRuntime()
    operation = await service.create_restoration(db_session, project.id, owner.id, request, runtime)
    row = await db_session.get(Restoration, operation.id)
    runtime.result = runtime.result.model_copy(
        update={"state": "checking", "phase": "checking", "can_apply": False}
    )
    receipt = runtime.result.model_dump(mode="json")
    if case == "legacy":
        receipt["report"].pop("database_state")
        row.report = {key: value for key, value in row.report.items() if key != "database_state"}
    elif case in {"empty", "present"}:
        receipt["report"]["database_state"] = case
        runtime.result.report.database_state = case
    elif case == "different_state":
        receipt["report"]["database_state"] = "present"
    elif case == "invalid_extra":
        receipt["unexpected"] = True
    else:
        receipt["can_apply"] = 1
    row.runtime_result = receipt
    row.state = "checking"
    row.phase = "checking"
    # JSON dirty detection uses Python equality (True == 1); persist the raw
    # malformed receipt so the test exercises strict validation after DB reload.
    flag_modified(row, "runtime_result")
    await db_session.commit()
    await db_session.refresh(row)
    assert row.runtime_result == receipt
    before = (row.revision, row.updated_at, deepcopy(row.runtime_result), row.reconcile_attempts)
    if case == "invalid_flag":
        assert type(row.runtime_result["can_apply"]) is int
    result = await service.advance_restoration(db_session, project.id, owner.id, row.id, runtime)
    await db_session.refresh(row)
    if case in {"legacy", "empty", "present"}:
        assert result.state == "checking" and not result.can_apply
        assert (row.revision, row.updated_at, row.runtime_result, row.reconcile_attempts) == before
        assert row.runtime_revision == 1
    else:
        assert result.state == "reconciling" and not result.can_apply
        assert row.runtime_result == receipt
    assert runtime.prepares == 1 and runtime.applies == 0
    assert project.current_snapshot_id == current.id and workspace.fencing_epoch == 7


async def test_db_lost_apply_reply_reconciles_without_second_activation(db_session):
    from omnia_api.schemas.restoration import RestoreApplyRequest
    from omnia_api.services import restorations as service

    owner, project, _, current, _, workspace, request = await restoration_fixture(db_session)
    runtime = FakeRuntime()
    operation = await service.create_restoration(db_session, project.id, owner.id, request, runtime)
    runtime.lose_apply_reply = True
    result = await service.apply_restoration(
        db_session,
        project.id,
        owner.id,
        operation.id,
        RestoreApplyRequest(
            report_revision=1,
            expected_draft_snapshot_id=current.id,
            idempotency_key="apply-restore-1",
        ),
        runtime,
    )
    assert result.state == "reconciling" and project.current_snapshot_id == current.id
    result = await service.advance_restoration(
        db_session, project.id, owner.id, operation.id, runtime
    )
    assert result.state == "completed" and runtime.applies == 1
    assert workspace.fencing_epoch == 8


@pytest.mark.parametrize("conflict", ["head", "report", "epoch"])
async def test_db_stale_apply_rejected_before_runtime(db_session, conflict):
    from omnia_api.core.errors import ApiError
    from omnia_api.schemas.restoration import RestoreApplyRequest
    from omnia_api.services import restorations as service

    owner, project, old, current, _, workspace, request = await restoration_fixture(db_session)
    runtime = FakeRuntime()
    operation = await service.create_restoration(db_session, project.id, owner.id, request, runtime)
    if conflict == "head":
        project.current_snapshot_id = old.id
    if conflict == "epoch":
        workspace.fencing_epoch += 1
    await db_session.commit()
    with pytest.raises(ApiError) as error:
        await service.apply_restoration(
            db_session,
            project.id,
            owner.id,
            operation.id,
            RestoreApplyRequest(
                report_revision=2 if conflict == "report" else 1,
                expected_draft_snapshot_id=current.id,
                idempotency_key="apply-restore-1",
            ),
            runtime,
        )
    assert error.value.status_code == 409 and runtime.applies == 0


async def test_db_admission_blocks_active_restore_and_rebound_key(db_session):
    from omnia_api.core.errors import ApiError
    from omnia_api.services import restorations as service

    owner, project, _, _, _, _, request = await restoration_fixture(db_session)
    runtime = FakeRuntime()
    operation = await service.create_restoration(db_session, project.id, owner.id, request, runtime)
    for changed in [
        request.model_copy(update={"idempotency_key": "another-key"}),
        request.model_copy(update={"target_version_id": uuid4()}),
    ]:
        with pytest.raises(ApiError) as error:
            await service.create_restoration(db_session, project.id, owner.id, changed, runtime)
        assert error.value.status_code == 409
    with pytest.raises(ApiError) as active:
        await service.assert_no_active_restoration(db_session, project.id)
    assert (active.value.code, active.value.status_code) == ("restoration_active", 409)
    assert active.value.details == {"restoration_id": str(operation.id)}
    cancelled = await service.cancel_restoration(
        db_session, project.id, owner.id, operation.id, runtime
    )
    assert cancelled.state == "cancelled" and runtime.cancel_calls == 1
    await service.assert_no_active_restoration(db_session, project.id)


async def test_db_disabled_routes_owner_scope_and_no_dispatch(client, db_session, monkeypatch):
    from omnia_api.core.security import create_access_token
    from omnia_api.routers import restorations as routes

    owner, project, _, _, _, _, request = await restoration_fixture(db_session)
    monkeypatch.setattr(routes.get_settings(), "max_code_restoration_enabled", False)
    token = create_access_token(owner.id)
    headers = {"Authorization": f"Bearer {token}"}
    path = f"/api/projects/{project.id}/restorations"
    response = await client.get(path, headers=headers)
    assert response.status_code == 200 and response.json() == {"items": [], "enabled": False}
    response = await client.post(path, json=request.model_dump(mode="json"), headers=headers)
    assert response.status_code == 409
    response = await client.get(f"/api/projects/{uuid4()}/restorations", headers=headers)
    assert response.status_code == 404


async def test_db_every_operation_rejects_foreign_actor(db_session):
    from omnia_api.core.errors import ApiError
    from omnia_api.schemas.restoration import RestoreApplyRequest
    from omnia_api.services import restorations as service

    owner, project, _, current, _, _, request = await restoration_fixture(db_session)
    runtime = FakeRuntime()
    operation = await service.create_restoration(db_session, project.id, owner.id, request, runtime)
    foreign = uuid4()
    calls = [
        service.list_operations(db_session, project.id, foreign),
        service.get_restoration(db_session, project.id, foreign, operation.id),
        service.cancel_restoration(db_session, project.id, foreign, operation.id, runtime),
        service.apply_restoration(
            db_session,
            project.id,
            foreign,
            operation.id,
            RestoreApplyRequest(
                report_revision=1,
                expected_draft_snapshot_id=current.id,
                idempotency_key="apply-restore-1",
            ),
            runtime,
        ),
        service.create_restoration(db_session, project.id, foreign, request, runtime),
    ]
    for call in calls:
        with pytest.raises(ApiError) as error:
            await call
        assert error.value.status_code == 404
    assert runtime.applies == 0 and runtime.cancel_calls == 0


async def test_runtime_wire_shape_and_deadlines(monkeypatch):
    from omnia_api.services import restoration_runtime as transport

    calls = []
    identity = {
        key: str(uuid4()) for key in ("operation_id", "workspace_id", "project_id", "owner_id")
    }

    async def fake_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {
            **identity,
            "state": "checking",
            "phase": "checking",
            "revision": 1,
            "can_apply": False,
            "can_cancel": True,
        }

    monkeypatch.setattr(transport, "_request", fake_request)
    request = {
        **identity,
        "expected_source_head": "a" * 40,
        "target_commit_sha": "b" * 40,
        "planned_commit_sha": "c" * 40,
        "fencing_epoch": 7,
        "binding_contract_version": 2,
        "files": [{"path": "test.bin", "content_base64": "AP8=", "mode": 0o100755}],
        "current_files": [{"path": "test.bin", "sha256": "a" * 64, "mode": 0o100755}],
    }
    runtime = transport.HttpRestorationRuntime()
    await runtime.prepare(request)
    assert calls[-1][2]["json"]["files"][0]["mode"] == "100755"
    assert calls[-1][2]["json"]["current_files"][0]["mode"] == "100755"
    assert calls[-1][2]["timeout"] == 930.0
    await runtime.apply(
        {
            **request,
            "fencing_epoch": 8,
            "expected_fencing_epoch": 7,
            "candidate_id": str(uuid4()),
            "report_revision": 1,
            "binding_digest": "d" * 64,
            "sentinel_prepare_only_extra": "must-not-cross-apply-boundary",
        }
    )
    assert set(calls[-1][2]["json"]) == {
        "operation_id",
        "workspace_id",
        "project_id",
        "owner_id",
        "expected_source_head",
        "target_commit_sha",
        "planned_commit_sha",
        "fencing_epoch",
        "candidate_id",
        "report_revision",
        "expected_fencing_epoch",
        "binding_digest",
    }
    await runtime.cancel(request)
    assert calls[-1][2]["json"] == identity
    await runtime.status(request)
    assert calls[-1][0] == "GET" and calls[-1][2]["json"] is None
    assert calls[-1][2]["params"] == {key: identity[key] for key in ("project_id", "owner_id")}
    assert calls[-1][2]["timeout"] == 30.0


async def test_db_lost_initial_dispatch_resumes_same_operation(db_session, monkeypatch):
    from omnia_api.services import restorations as service

    owner, project, _, current, _, _, request = await restoration_fixture(db_session)
    runtime = FakeRuntime()

    async def not_dispatched(payload):
        raise TimeoutError("connection lost before dispatch")

    real_prepare = runtime.prepare
    monkeypatch.setattr(runtime, "prepare", not_dispatched)
    operation = await service.create_restoration(db_session, project.id, owner.id, request, runtime)
    assert operation.state == "reconciling" and operation.can_cancel
    monkeypatch.setattr(runtime, "prepare", real_prepare)
    recovered = await service.advance_restoration(
        db_session, project.id, owner.id, operation.id, runtime
    )
    assert recovered.id == operation.id and recovered.state == "ready"
    assert runtime.prepares == 1 and project.current_snapshot_id == current.id


async def test_db_lost_initial_dispatch_replays_persisted_legacy_v2_payload_unchanged(
    db_session, monkeypatch
):
    from sqlalchemy.orm.attributes import flag_modified

    from omnia_api.models.restoration import Restoration
    from omnia_api.services import restorations as service

    class CapturingRuntime(FakeRuntime):
        def __init__(self):
            super().__init__()
            self.prepare_payloads = []

        async def prepare(self, payload):
            self.prepare_payloads.append(dict(payload))
            return await super().prepare(payload)

    owner, project, _, _, _, _, request = await restoration_fixture(db_session)
    runtime = CapturingRuntime()
    real_prepare = runtime.prepare

    async def not_dispatched(payload):
        raise TimeoutError("connection lost before dispatch")

    monkeypatch.setattr(runtime, "prepare", not_dispatched)
    operation = await service.create_restoration(
        db_session, project.id, owner.id, request, runtime
    )
    row = await db_session.get(Restoration, operation.id)
    row.request_payload = {**row.request_payload, "binding_contract_version": 2}
    flag_modified(row, "request_payload")
    await db_session.commit()

    monkeypatch.setattr(runtime, "prepare", real_prepare)
    recovered = await service.advance_restoration(
        db_session, project.id, owner.id, operation.id, runtime
    )

    assert recovered.state == "ready"
    assert runtime.prepare_payloads[-1]["binding_contract_version"] == 2
    row = await db_session.get(Restoration, operation.id)
    assert row.request_payload["binding_contract_version"] == 2
    assert row.source_binding["version"] == 2


async def test_db_database_commit_failure_after_activation_is_recoverable(db_session, monkeypatch):
    from sqlalchemy import func, select

    from omnia_api.models.restoration import Restoration
    from omnia_api.models.snapshot import Snapshot
    from omnia_api.schemas.restoration import RestoreApplyRequest
    from omnia_api.services import restorations as service

    owner, project, _, current, _, _, request = await restoration_fixture(db_session)
    owner_id, project_id, current_id = owner.id, project.id, current.id
    runtime = FakeRuntime()
    operation = await service.create_restoration(db_session, project_id, owner_id, request, runtime)
    real_commit = db_session.commit

    async def fail_final_commit():
        if any(
            isinstance(row, Restoration) and row.state == "completed"
            for row in db_session.identity_map.values()
        ):
            raise RuntimeError("injected SQL commit failure after runtime and Git activation")
        await real_commit()

    monkeypatch.setattr(db_session, "commit", fail_final_commit)
    with pytest.raises(RuntimeError, match="injected SQL"):
        await service.apply_restoration(
            db_session,
            project_id,
            owner_id,
            operation.id,
            RestoreApplyRequest(
                report_revision=1,
                expected_draft_snapshot_id=current_id,
                idempotency_key="apply-restore-1",
            ),
            runtime,
        )
    await db_session.rollback()
    monkeypatch.setattr(db_session, "commit", real_commit)
    recovered = await service.advance_restoration(
        db_session, project_id, owner_id, operation.id, runtime
    )
    assert recovered.state == "completed" and runtime.applies == 1
    assert await db_session.scalar(select(func.count()).select_from(Snapshot)) == 3


@pytest.mark.parametrize("field", ["owner_id", "source_commit_sha", "fencing_epoch"])
async def test_db_wrong_observed_activation_never_advances_draft(db_session, monkeypatch, field):
    from omnia_api.schemas.restoration import RestoreApplyRequest
    from omnia_api.services import restorations as service

    owner, project, _, current, _, _, request = await restoration_fixture(db_session)
    runtime = FakeRuntime()
    operation = await service.create_restoration(db_session, project.id, owner.id, request, runtime)
    real_apply = runtime.apply

    async def mismatched(payload):
        result = await real_apply(payload)
        if field == "owner_id":
            return result.model_copy(update={"owner_id": uuid4()})
        result.observed = result.observed.model_copy(
            update={
                field: "f" * 40 if field == "source_commit_sha" else payload["fencing_epoch"] + 1,
            }
        )
        return result

    monkeypatch.setattr(runtime, "apply", mismatched)
    result = await service.apply_restoration(
        db_session,
        project.id,
        owner.id,
        operation.id,
        RestoreApplyRequest(
            report_revision=1,
            expected_draft_snapshot_id=current.id,
            idempotency_key="apply-restore-1",
        ),
        runtime,
    )
    assert result.state == "reconciling" and result.applied_snapshot_id is None
    assert project.current_snapshot_id == current.id


async def test_db_generation_blocks_restore_before_external_dispatch(db_session):
    from omnia_api.core.errors import ApiError
    from omnia_api.models.generation_run import GenerationRun
    from omnia_api.models.message import Message
    from omnia_api.services import restorations as service

    owner, project, _, _, _, _, request = await restoration_fixture(db_session)
    message = Message(project_id=project.id, role="user", content="fixture")
    db_session.add(message)
    await db_session.flush()
    run = GenerationRun(
        project_id=project.id,
        user_id=owner.id,
        user_message_id=message.id,
        idempotency_key="active-fixture",
        prompt_hash="fixture",
        status="running",
    )
    db_session.add(run)
    await db_session.commit()
    runtime = FakeRuntime()
    with pytest.raises(ApiError) as error:
        await service.create_restoration(db_session, project.id, owner.id, request, runtime)
    assert error.value.status_code == 409 and runtime.prepares == 0
    # The owner is told what blocks the restoration, and which run it is.
    assert error.value.code == "generation_active"
    assert error.value.details == {"active_run_id": str(run.id)}
    assert "идёт сборка" in error.value.message


async def test_db_busy_project_lock_rejects_restoration_admission_without_writes(
    db_session, test_engine
):
    import asyncio

    from sqlalchemy import func, select, text
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from omnia_api.core.errors import ApiError
    from omnia_api.models.restoration import Restoration
    from omnia_api.services import restorations as service

    owner, project, _, _, _, _, request = await restoration_fixture(db_session)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    runtime = FakeRuntime()

    async with factory() as blocker, factory() as contender:
        await blocker.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:project_id))"),
            {"project_id": str(project.id)},
        )
        with pytest.raises(ApiError) as error:
            await asyncio.wait_for(
                service.create_restoration(
                    contender,
                    project.id,
                    owner.id,
                    request,
                    runtime,
                ),
                timeout=1.0,
            )
        await contender.rollback()

    assert error.value.status_code == 503
    assert error.value.code == "conflict"
    assert error.value.details == {"retryable": True}
    assert error.value.message == (
        "Проект сейчас занят. Повторите восстановление через несколько секунд."
    )
    assert runtime.prepares == 0
    assert await db_session.scalar(select(func.count()).select_from(Restoration)) == 0


@pytest.mark.parametrize("flag", [1, "true", "yes"])
def test_runtime_completion_rejects_coerced_applied_flags(flag):
    from pydantic import ValidationError

    from omnia_api.schemas.restoration import RuntimeObserved

    with pytest.raises(ValidationError):
        RuntimeObserved(
            candidate_id=uuid4(), source_commit_sha="a" * 40, fencing_epoch=1, applied=flag
        )


def test_restore_source_symlink_is_rejected_without_activation():
    import pygit2

    project, operation = uuid4(), uuid4()
    old = repo.init_from_files(project, {"page.txt": "old"}, "seed")
    with repo._open_workdir(project, must_exist=True) as path:
        git = pygit2.Repository(str(path))
        tree = git.TreeBuilder(git[old].tree)
        tree.insert("link", git.create_blob(b"/etc/passwd"), pygit2.GIT_FILEMODE_LINK)
        unsafe = str(
            git.create_commit(
                None, repo._signature(), repo._signature(), "link", tree.write(), [git.head.target]
            )
        )
        repo._upload(project, path)
    with pytest.raises(ValueError, match="regular files"):
        repo.prepare_restore_commit(project, unsafe, old, operation)
    with repo._open_workdir(project, must_exist=True) as path:
        git = pygit2.Repository(str(path))
        assert f"refs/omnia/restorations/{operation.hex}" not in git.references
        assert str(git.head.target) == old


@pytest.mark.parametrize("consumer", ["generation", "preview", "publication", "deletion"])
async def test_db_active_restoration_blocks_real_mutating_consumers(db_session, consumer):
    from omnia_api.core.errors import ApiError
    from omnia_api.services import restorations as service
    from omnia_api.services.cell_publication import submit_publication
    from omnia_api.services.generation_runs import reserve_generation_run
    from omnia_api.services.project_cell_deletion import teardown_project_cell
    from omnia_api.services.project_cell_runtime import _try_preview_project_lock

    owner, project, _, _, _, workspace, request = await restoration_fixture(db_session)
    workspace.provider = "docker_owner_canary"
    await db_session.commit()
    await service.create_restoration(db_session, project.id, owner.id, request, FakeRuntime())
    with pytest.raises(ApiError) as error:
        if consumer == "generation":
            await reserve_generation_run(
                db_session,
                project_id=project.id,
                user_id=owner.id,
                idempotency_key="new-generation",
                prompt="a new generation",
            )
        elif consumer == "preview":
            await _try_preview_project_lock(db_session, project.id)
        elif consumer == "publication":
            await submit_publication(
                db_session,
                project,
                workspace,
                requested_sha=None,
                idempotency_key="publish-during-restore",
            )
        else:
            await teardown_project_cell(db_session, project)
    assert error.value.status_code == 409
    # Every consumer is told the real reason, never a bare "conflict" that a
    # client could mistake for a running build — and in words the owner can act on.
    assert error.value.code == "restoration_active"
    assert "восстановление версии" in error.value.message
    assert workspace.state == "ready"


async def test_db_lost_cancel_reply_replays_as_status(db_session, monkeypatch):
    from omnia_api.services import restorations as service

    owner, project, _, _, _, _, request = await restoration_fixture(db_session)
    runtime = FakeRuntime()
    operation = await service.create_restoration(db_session, project.id, owner.id, request, runtime)
    real_cancel = runtime.cancel

    async def lost_reply(payload):
        await real_cancel(payload)
        raise TimeoutError("cancel reply lost")

    monkeypatch.setattr(runtime, "cancel", lost_reply)
    first = await service.cancel_restoration(
        db_session, project.id, owner.id, operation.id, runtime
    )
    assert first.state == "reconciling"
    repeated = await service.cancel_restoration(
        db_session, project.id, owner.id, operation.id, runtime
    )
    assert repeated.state == "reconciling" and runtime.cancel_calls == 1
    recovered = await service.advance_restoration(
        db_session, project.id, owner.id, operation.id, runtime
    )
    assert recovered.state == "cancelled" and runtime.cancel_calls == 1


async def test_db_capacity_skips_restoration_until_cancelled(db_session):
    from omnia_api.models.generation_run import GenerationRun
    from omnia_api.models.project import Project
    from omnia_api.services import restorations as service
    from omnia_api.services.project_cell_capacity import claim_idle_hibernation_victim

    owner, project, _, _, _, workspace, request = await restoration_fixture(db_session)
    runtime = FakeRuntime()
    operation = await service.create_restoration(db_session, project.id, owner.id, request, runtime)
    requester = Project(
        owner_id=owner.id, name="Requester", slug=str(uuid4()), template="max_miniapp"
    )
    db_session.add(requester)
    await db_session.flush()
    run = GenerationRun(
        project_id=requester.id,
        user_id=owner.id,
        idempotency_key="capacity-run",
        prompt_hash="fixture",
        status="queued_for_capacity",
    )
    db_session.add(run)
    await db_session.commit()
    victim = await claim_idle_hibernation_victim(
        db_session, requesting_run_id=run.id, expected_workspace_id=workspace.id
    )
    assert victim is None
    await db_session.commit()
    await service.cancel_restoration(db_session, project.id, owner.id, operation.id, runtime)
    victim = await claim_idle_hibernation_victim(
        db_session, requesting_run_id=run.id, expected_workspace_id=workspace.id
    )
    assert victim.id == workspace.id


async def test_db_router_completed_returns_actual_snapshot(client, db_session, monkeypatch):
    from omnia_api.core.security import create_access_token
    from omnia_api.routers import restorations as routes

    owner, project, old, current, _, _, request = await restoration_fixture(db_session)
    monkeypatch.setattr(routes.get_settings(), "max_code_restoration_enabled", True)
    runtime = FakeRuntime()
    monkeypatch.setattr(routes, "HttpRestorationRuntime", lambda: runtime)
    headers = {"Authorization": f"Bearer {create_access_token(owner.id)}"}
    path = f"/api/projects/{project.id}/restorations"
    prepared = await client.post(path, json=request.model_dump(mode="json"), headers=headers)
    assert prepared.status_code == 200
    operation_id = prepared.json()["id"]
    applied = await client.post(
        f"{path}/{operation_id}/apply",
        headers=headers,
        json={
            "report_revision": 1,
            "expected_draft_snapshot_id": str(current.id),
            "idempotency_key": "apply-route-1",
        },
    )
    assert applied.status_code == 200
    payload = applied.json()
    assert payload["state"] == "completed"
    assert payload["applied_snapshot"]["id"] == payload["applied_snapshot_id"]
    assert payload["applied_snapshot"]["project_id"] == str(project.id)
    assert payload["applied_snapshot"]["parent_id"] == str(current.id)
    assert payload["applied_snapshot"]["commit_sha"] != old.commit_sha


def test_verified_negative_runtime_receipt_is_failed_only():
    from pydantic import ValidationError

    from omnia_api.schemas.restoration import RuntimeRestoration
    from omnia_api.services.restorations import validate_runtime_response

    identity = {key: uuid4() for key in ("operation_id", "workspace_id", "project_id", "owner_id")}
    observed = {
        "candidate_id": uuid4(),
        "source_commit_sha": "a" * 40,
        "fencing_epoch": 8,
        "applied": False,
        "safe_to_release": True,
    }
    result = RuntimeRestoration(
        **identity,
        state="failed",
        phase="recovered",
        revision=4,
        candidate_id=observed["candidate_id"],
        can_apply=False,
        can_cancel=False,
        observed=observed,
    )
    assert result.observed.applied is False

    pre_effect = RuntimeRestoration(
        **identity,
        state="failed",
        phase="recovered",
        revision=5,
        candidate_id=observed["candidate_id"],
        can_apply=False,
        can_cancel=False,
        observed={
            **observed,
            "rejected_before_effect": True,
            "retained_source_fencing_epoch": 3,
        },
    )
    assert pre_effect.observed.rejected_before_effect is True
    assert pre_effect.observed.retained_source_fencing_epoch == 3
    request_payload = {
        **{key: str(value) for key, value in identity.items()},
        "candidate_id": str(observed["candidate_id"]),
        "planned_commit_sha": observed["source_commit_sha"],
        "fencing_epoch": 8,
        "expected_fencing_epoch": 7,
    }
    validate_runtime_response(request_payload, pre_effect)
    with pytest.raises(ValueError, match="rejection fence mismatch"):
        validate_runtime_response({**request_payload, "expected_fencing_epoch": 2}, pre_effect)
    superseded = RuntimeRestoration(
        **identity,
        state="failed",
        phase="recovered",
        revision=6,
        candidate_id=observed["candidate_id"],
        can_apply=False,
        can_cancel=False,
        observed={
            **observed,
            "superseded_before_effect": True,
        },
    )
    validate_runtime_response(request_payload, superseded)
    with pytest.raises(ValidationError, match="cannot assert"):
        RuntimeRestoration(
            **identity,
            state="failed",
            phase="recovered",
            revision=6,
            candidate_id=observed["candidate_id"],
            can_apply=False,
            can_cancel=False,
            observed={
                **observed,
                "superseded_before_effect": True,
                "retained_source_fencing_epoch": 3,
            },
        )
    with pytest.raises(ValidationError):
        RuntimeRestoration(
            **identity,
            state="completed",
            phase="complete",
            revision=4,
            candidate_id=observed["candidate_id"],
            can_apply=False,
            can_cancel=False,
            observed=observed,
        )
    with pytest.raises(ValidationError):
        RuntimeRestoration(
            **identity,
            state="failed",
            phase="failed",
            revision=4,
            can_apply=False,
            can_cancel=False,
            observed={**observed, "safe_to_release": 1},
        )
    with pytest.raises(ValidationError, match="receipt must be complete"):
        RuntimeRestoration(
            **identity,
            state="failed",
            phase="failed",
            revision=5,
            candidate_id=observed["candidate_id"],
            can_apply=False,
            can_cancel=False,
            observed={**observed, "rejected_before_effect": True},
        )


@pytest.mark.parametrize("verified", [False, True])
async def test_db_apply_failure_keeps_claim_until_verified_recovery(
    db_session, monkeypatch, verified
):
    from omnia_api.core.errors import ApiError
    from omnia_api.schemas.restoration import RestoreApplyRequest, RuntimeRestoration
    from omnia_api.services import restorations as service

    owner, project, _, current, _, _, request = await restoration_fixture(db_session)
    runtime = FakeRuntime()
    operation = await service.create_restoration(db_session, project.id, owner.id, request, runtime)

    async def failed_apply(payload):
        result = runtime.result.model_dump(mode="json")
        result.update(
            state="failed", phase="recovered", revision=2, can_apply=False, can_cancel=False
        )
        if verified:
            result["observed"] = {
                "candidate_id": payload["candidate_id"],
                "source_commit_sha": payload["planned_commit_sha"],
                "fencing_epoch": payload["fencing_epoch"],
                "binding_digest": payload["binding_digest"],
                "applied": False,
                "safe_to_release": True,
            }
        return RuntimeRestoration.model_validate(result)

    monkeypatch.setattr(runtime, "apply", failed_apply)
    result = await service.apply_restoration(
        db_session,
        project.id,
        owner.id,
        operation.id,
        RestoreApplyRequest(
            report_revision=1,
            expected_draft_snapshot_id=current.id,
            idempotency_key="verified-recovery-apply",
        ),
        runtime,
    )
    assert result.state == ("failed" if verified else "reconciling")
    assert result.applied_snapshot_id is None and project.current_snapshot_id == current.id
    if verified:
        await service.assert_no_active_restoration(db_session, project.id)
    else:
        with pytest.raises(ApiError):
            await service.assert_no_active_restoration(db_session, project.id)


@pytest.mark.parametrize("active", [False, True])
async def test_db_source_releases_only_real_terminal_lease(db_session, monkeypatch, active):
    from omnia_api.core.errors import ApiError
    from omnia_api.models.generation_run import GenerationRun
    from omnia_api.models.message import Message
    from omnia_api.services import project_cell_capacity
    from omnia_api.services import restorations as service

    owner, project, _, _, _, workspace, request = await restoration_fixture(db_session)
    message = Message(project_id=project.id, role="user", content="Existing source build")
    db_session.add(message)
    await db_session.flush()
    run = GenerationRun(
        project_id=project.id,
        user_id=owner.id,
        user_message_id=message.id,
        idempotency_key="existing-terminal",
        prompt_hash="existing",
        status="running" if active else "completed",
    )
    db_session.add(run)
    await db_session.flush()
    workspace.generation_run_id = run.id
    await db_session.commit()
    calls = []

    async def release(factory, **kwargs):
        calls.append(kwargs)
        assert not db_session.in_transaction()
        workspace.generation_run_id = None
        workspace.fencing_epoch += 1
        await db_session.commit()
        return True

    monkeypatch.setattr(project_cell_capacity, "release_one_stale_generation_lease", release)
    if active:
        with pytest.raises(ApiError):
            await service.create_restoration(
                db_session, project.id, owner.id, request, FakeRuntime()
            )
        assert calls == []
    else:
        result = await service.create_restoration(
            db_session, project.id, owner.id, request, FakeRuntime()
        )
        assert result.state == "ready" and len(calls) == 1
        assert calls[0]["requesting_run_id"] == run.id
        assert calls[0]["workspace_id"] == workspace.id
        assert calls[0]["reclaim_for_repair"] is False


async def test_db_source_lost_wake_reuses_durable_operation(db_session, monkeypatch):
    from types import SimpleNamespace

    from omnia_api.core.errors import ApiError
    from omnia_api.models.project_cell import ProjectCellOperation
    from omnia_api.services import project_cell_runtime as cell
    from omnia_api.services import restorations as service

    owner, project, _, _, _, workspace, request = await restoration_fixture(db_session)
    workspace.state = "stopped"
    await db_session.commit()
    wake_ids = []
    ready = False

    async def resources(workspace_id):
        return SimpleNamespace(state="resources_ready" if ready else "resources_paused")

    async def wake(session, source, *, operation):
        nonlocal ready
        if operation is None:
            operation = ProjectCellOperation(
                workspace_id=source.id,
                kind="wake",
                status="indeterminate",
                idempotency_key=f"owner-preview:wake:{source.id}:{source.fencing_epoch}",
                request_payload={},
                request_digest="fixture",
                fencing_epoch=source.fencing_epoch + 1,
            )
            session.add(operation)
            source.fencing_epoch += 1
            await session.commit()
            wake_ids.append(operation.id)
            raise ApiError("orchestrator_unavailable", "wake reply lost", 503)
        wake_ids.append(operation.id)
        operation.status = "completed"
        source.state = "ready"
        ready = True
        await session.commit()

    monkeypatch.setattr(cell, "_get_cell_resources", resources)
    monkeypatch.setattr(cell, "_wake_owner_workspace", wake)
    runtime = FakeRuntime()
    with pytest.raises(ApiError):
        await service.create_restoration(db_session, project.id, owner.id, request, runtime)
    assert runtime.prepares == 0
    result = await service.create_restoration(db_session, project.id, owner.id, request, runtime)
    assert result.state == "ready" and len(wake_ids) == 2 and wake_ids[0] == wake_ids[1]


async def test_db_restored_publication_loads_real_version_without_generation(db_session):
    from omnia_api.schemas.restoration import RestoreApplyRequest
    from omnia_api.services import restorations as service
    from omnia_api.services.cell_publication import load_publication_evidence

    owner, project, _, current, _, workspace, request = await restoration_fixture(db_session)
    workspace.provider = "docker_owner_canary"
    await db_session.commit()
    runtime = FakeRuntime()
    operation = await service.create_restoration(db_session, project.id, owner.id, request, runtime)
    result = await service.apply_restoration(
        db_session,
        project.id,
        owner.id,
        operation.id,
        RestoreApplyRequest(
            report_revision=1,
            expected_draft_snapshot_id=current.id,
            idempotency_key="publication-restored-apply",
        ),
        runtime,
    )
    evidence = await load_publication_evidence(db_session, project, workspace)
    assert evidence["restoration_operation_id"] == str(result.id)
    assert evidence["snapshot_id"] == str(result.applied_snapshot_id)
    assert evidence["source_revision"] == "b" * 64
    assert "proof_key" not in evidence
