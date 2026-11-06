import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from yleum_recovery.cli import main
from yleum_recovery.contracts import (
    BackupManifest,
    Component,
    Inventory,
    RecoveryProfile,
    ReleaseManifest,
    RestorePoint,
    Role,
    SecretMaterial,
)
from yleum_recovery.manifest import select_restore_point, verify_manifests

SHA = "a" * 40
HASH = "b" * 64


@pytest.fixture
def manifests() -> tuple[ReleaseManifest, BackupManifest]:
    components = tuple(
        Component(
            component_id=f"{role.value}-db",
            role=role,
            kind="database",
            required=True,
            owner_id="platform",
            binding_id=f"{role.value}-database",
            capture_handler_id="postgres-logical",
        )
        for role in Role
    )
    release = ReleaseManifest(
        release_id="release-one",
        git_sha=SHA,
        schema_version="1",
        protocol_version="1",
        platform="linux",
        architecture="x86_64",
        components=components,
        image_digests={c.component_id: f"sha256:{HASH}" for c in components},
        archive_hashes={c.component_id: HASH for c in components},
        certification_evidence_id="evidence-reference-only",
    )
    backup = BackupManifest(
        backup_id="backup-one",
        release_id="release-one",
        release_sha=SHA,
        secret_export_id="export-one",
        schema_version="1",
        protocol_version="1",
        platform="linux",
        architecture="x86_64",
        inventory=Inventory(
            roles=tuple(Role),
            components=components,
            capture_id="capture-one",
            signer_id="capture-signer",
            signature="signature-reference-only",
        ),
        captured_components=tuple(c.component_id for c in components),
        hashes={c.component_id: HASH for c in components},
        completeness="complete",
        component_completeness={c.component_id: "complete" for c in components},
        boundary=datetime(2026, 10, 13, tzinfo=UTC),
        watermarks={c.component_id: "snapshot-one" for c in components},
        object_versions={},
        db_format="logical-snapshot",
        rpo_seconds=3600,
    )
    return release, backup


def changed(model, **updates):
    """Revalidate changes through the public serialization boundary."""
    return type(model).model_validate(model.model_dump() | updates)


def point(release, backup, **updates):
    return RestorePoint(
        point_id=backup.backup_id,
        release=release,
        backup=backup,
        db_format=backup.db_format,
        **updates,
    )


def test_complete_three_roles(manifests):
    release, backup = manifests
    proof = verify_manifests(release, backup)
    assert proof.ok is True
    assert proof.failures == ()
    assert "ready" not in proof.model_dump()
    assert "certified" not in proof.model_dump()


def test_missing_component(manifests):
    release, backup = manifests
    backup = changed(backup, captured_components=["core-db", "runtime-db"])
    proof = verify_manifests(release, backup)
    assert proof.ok is False
    assert any(
        f.code == "MISSING_COMPONENT" and f.component_id == "commerce-db" for f in proof.failures
    )


def test_new_main_not_selected(manifests):
    release, backup = manifests
    new_main = changed(release, release_id="new-main", git_sha="c" * 40)
    chosen = select_restore_point((point(new_main, backup), point(release, backup)))
    assert chosen.release.git_sha == SHA
    assert chosen.backup.release_id == chosen.release.release_id


def test_reused_release_id_cannot_replace_bound_sha(manifests):
    release, backup = manifests
    replacement = changed(release, git_sha="c" * 40)
    proof = verify_manifests(replacement, backup)
    assert proof.ok is False
    assert "FORMAT_MISMATCH" in {failure.code for failure in proof.failures}
    chosen = select_restore_point((point(replacement, backup), point(release, backup)))
    assert chosen.release.git_sha == SHA


def test_logical_dump_cannot_replay_wal(manifests):
    release, backup = manifests
    backup = changed(backup, requires_wal_replay=True)
    proof = verify_manifests(release, backup)
    assert proof.ok is False
    assert "FORMAT_MISMATCH" in {f.code for f in proof.failures}


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", "2"),
        ("protocol_version", "2"),
        ("release_id", "other"),
    ],
)
def test_incompatible_metadata(manifests, field, value):
    release, backup = manifests
    proof = verify_manifests(release, changed(backup, **{field: value}))
    assert proof.ok is False
    assert "FORMAT_MISMATCH" in {f.code for f in proof.failures}


@pytest.mark.parametrize("state", ["partial", "deferred", "unknown", "missing"])
def test_incomplete_capture(manifests, state):
    release, backup = manifests
    proof = verify_manifests(release, changed(backup, completeness=state))
    assert proof.ok is False


def test_inventory_cannot_hide_required_component(manifests):
    release, backup = manifests
    inventory = changed(backup.inventory, components=list(backup.inventory.components[:2]))
    assert verify_manifests(release, changed(backup, inventory=inventory)).ok is False


def test_custom_required_volume_needs_capture(manifests):
    release, backup = manifests
    custom = Component(
        component_id="custom-draft",
        role="runtime",
        kind="volume",
        required=True,
        owner_id="project-1",
        binding_id="pvc-with-unusual-name",
        capture_handler_id="volume",
    )
    inventory = changed(backup.inventory, components=[*backup.inventory.components, custom])
    proof = verify_manifests(release, changed(backup, inventory=inventory))
    assert proof.ok is False
    assert any(f.component_id == "custom-draft" for f in proof.failures)


@pytest.mark.parametrize("field", ["hashes", "watermarks", "component_completeness"])
def test_required_capture_evidence_cannot_be_missing(manifests, field):
    release, backup = manifests
    assert verify_manifests(release, changed(backup, **{field: {}})).ok is False


def test_role_cannot_be_missing(manifests):
    release, backup = manifests
    inventory = changed(
        backup.inventory,
        roles=["core", "runtime"],
        components=list(backup.inventory.components[:2]),
    )
    assert verify_manifests(release, changed(backup, inventory=inventory)).ok is False


def test_selection_uses_latest_compatible_boundary(manifests):
    release, backup = manifests
    newer = changed(backup, backup_id="newer", boundary=datetime(2026, 10, 14, tzinfo=UTC))
    incompatible = changed(newer, backup_id="bad", schema_version="99")
    chosen = select_restore_point(
        (point(release, backup), point(release, incompatible), point(release, newer))
    )
    assert chosen.backup.backup_id == "newer"


def test_selection_rejects_empty_or_wrong_restore_format(manifests):
    release, backup = manifests
    with pytest.raises(ValueError, match="compatible"):
        select_restore_point(())
    wrong = RestorePoint(point_id="wrong", release=release, backup=backup, db_format="physical-wal")
    with pytest.raises(ValueError, match="compatible"):
        select_restore_point((wrong,))


@pytest.mark.parametrize(
    "updates",
    [
        {"format_version": 2},
        {"unexpected": "value"},
        {"git_sha": "main"},
        {"image_digests": {"core-db": "prod"}},
        {"platform": "windows"},
    ],
)
def test_release_rejects_unsupported_or_unpinned_metadata(manifests, updates):
    release, _ = manifests
    with pytest.raises(ValidationError):
        changed(release, **updates)


def test_duplicate_inventory_ids_rejected(manifests):
    _, backup = manifests
    with pytest.raises(ValidationError):
        changed(
            backup.inventory,
            components=[*backup.inventory.components, backup.inventory.components[0]],
        )


def test_boundary_must_be_utc_and_logical_rpo_positive(manifests):
    _, backup = manifests
    for updates in (
        {"boundary": "2026-10-13T01:00:00"},
        {"boundary": "2026-10-13T01:00:00+03:00"},
        {"rpo_seconds": 0},
    ):
        with pytest.raises(ValidationError):
            changed(backup, **updates)


@pytest.fixture
def recovery_profile() -> RecoveryProfile:
    sentinel = "SYNTHETIC-PRIVATE-NEVER-PRINT"
    material = SecretMaterial(
        export_id="export-one",
        credentials={"provider": sentinel},
        keys={"JWT_SECRET": sentinel, "SECRETS_ENCRYPTION_KEY": sentinel},
        role_exports={role: {"ENV": sentinel} for role in Role},
    )
    return RecoveryProfile(
        name="cortel",
        provider_adapter="provider",
        provider_account="account",
        provider_group="group",
        role_selectors={role: f"yleum-role={role.value}" for role in Role},
        storage_adapter="storage",
        lease_adapter="lease",
        ingress_adapter="ingress",
        secret_material=material,
    )


def test_profile_and_secret_material_do_not_expose_secrets(recovery_profile):
    sentinel = "SYNTHETIC-PRIVATE-NEVER-PRINT"
    profile = recovery_profile
    material = profile.secret_material
    for model in (material, profile):
        assert sentinel not in repr(model)
        assert sentinel not in str(model)
        assert sentinel not in str(model.model_dump())
        assert sentinel not in model.model_dump_json()
    assert profile.model_dump()["secret_export_id"] == "export-one"
    assert profile.public_reference == "local-profile:cortel"
    assert material.credentials["provider"].get_secret_value() == sentinel


def test_profile_rejects_physical_addresses(recovery_profile):
    # Public dumps intentionally exclude the private material, so preserve it
    # explicitly in the otherwise valid input for this validation boundary.
    payload = recovery_profile.model_dump(exclude={"secret_export_id"}) | {
        "secret_material": recovery_profile.secret_material,
    }
    RecoveryProfile.model_validate(payload)
    with pytest.raises(ValidationError) as error:
        RecoveryProfile.model_validate(payload | {"physical_ip": "192.0.2.1"})
    assert [(failure["loc"], failure["type"]) for failure in error.value.errors()] == [
        (("physical_ip",), "extra_forbidden"),
    ]


@pytest.mark.parametrize("missing", ["image_digests", "archive_hashes", "both", None])
def test_inventory_added_required_image_needs_release_artifacts(manifests, missing):
    release, backup = manifests
    custom = Component(
        component_id="custom-runtime-image",
        role="runtime",
        kind="image",
        required=True,
        owner_id="project-1",
        binding_id="custom-runtime-image",
        capture_handler_id="oci-archive",
    )
    inventory = changed(backup.inventory, components=[*backup.inventory.components, custom])
    backup = changed(
        backup,
        inventory=inventory,
        captured_components=[*backup.captured_components, "custom-runtime-image"],
        hashes=backup.hashes | {"custom-runtime-image": HASH},
        watermarks=backup.watermarks | {"custom-runtime-image": "snapshot-one"},
        component_completeness=backup.component_completeness | {"custom-runtime-image": "complete"},
    )
    images = release.image_digests
    archives = release.archive_hashes
    if missing not in ("image_digests", "both"):
        images = images | {"custom-runtime-image": f"sha256:{HASH}"}
    if missing not in ("archive_hashes", "both"):
        archives = archives | {"custom-runtime-image": HASH}
    release = changed(release, image_digests=images, archive_hashes=archives)
    proof = verify_manifests(release, backup)
    if missing is None:
        assert proof.ok is True
        assert select_restore_point((point(release, backup),)).backup.backup_id == "backup-one"
    else:
        assert proof.ok is False
        assert any(
            failure.code == "MISSING_ARTIFACT" and failure.component_id == "custom-runtime-image"
            for failure in proof.failures
        )
        with pytest.raises(ValueError, match="compatible"):
            select_restore_point((point(release, backup),))


def test_cli_reports_metadata_compatibility_only(manifests, tmp_path: Path, capsys):
    release, backup = manifests
    release_path, backup_path = tmp_path / "release.json", tmp_path / "backup.json"
    release_path.write_text(release.model_dump_json(), encoding="utf-8")
    backup_path.write_text(backup.model_dump_json(), encoding="utf-8")
    assert main(["verify", "--release", str(release_path), "--backup", str(backup_path)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "metadata-compatible"
    assert output["proof"]["ok"] is True
    assert "ready" not in output


def test_cli_invalid_input_is_redacted(tmp_path: Path, capsys):
    path = tmp_path / "invalid.json"
    sentinel = "SYNTHETIC-PRIVATE-NEVER-PRINT"
    path.write_text(json.dumps({"git_sha": sentinel}), encoding="utf-8")
    assert main(["verify", "--release", str(path), "--backup", str(path)]) == 2
    output = capsys.readouterr()
    assert sentinel not in output.out + output.err
    assert "INVALID_MANIFEST" in output.out


@pytest.mark.parametrize("version", [True, 1.0, "1"])
def test_version_cannot_be_coerced(manifests, version):
    release, _ = manifests
    with pytest.raises(ValidationError):
        changed(release, format_version=version)


def test_required_flag_cannot_be_coerced(manifests):
    release, _ = manifests
    with pytest.raises(ValidationError):
        changed(release.components[0], required="false")


@pytest.mark.parametrize("state", ["partial", "deferred", "unknown", "missing"])
def test_required_component_incomplete(manifests, state):
    release, backup = manifests
    completeness = backup.component_completeness | {"commerce-db": state}
    proof = verify_manifests(release, changed(backup, component_completeness=completeness))
    assert proof.ok is False
    assert any(
        f.code == "INCOMPLETE_COMPONENT" and f.component_id == "commerce-db" for f in proof.failures
    )


def test_required_unknown_volume_rejected(manifests):
    release, backup = manifests
    custom = Component(
        component_id="custom-volume",
        role="runtime",
        kind="unknown",
        required=True,
        owner_id="project-1",
        binding_id="unclassified-volume",
        capture_handler_id="unknown",
    )
    inventory = changed(backup.inventory, components=[*backup.inventory.components, custom])
    backup = changed(
        backup,
        inventory=inventory,
        captured_components=[*backup.captured_components, "custom-volume"],
        hashes=backup.hashes | {"custom-volume": HASH},
        watermarks=backup.watermarks | {"custom-volume": "snapshot-one"},
        component_completeness=backup.component_completeness | {"custom-volume": "complete"},
    )
    proof = verify_manifests(release, backup)
    assert proof.ok is False
    assert any(
        f.code == "UNKNOWN_COMPONENT" and f.component_id == "custom-volume" for f in proof.failures
    )


def test_physical_wal_allows_zero_rpo(manifests):
    release, backup = manifests
    backup = changed(backup, db_format="physical-wal", requires_wal_replay=True, rpo_seconds=0)
    assert verify_manifests(release, backup).ok is True


def test_cli_incompatible_metadata_returns_failure(manifests, tmp_path: Path, capsys):
    release, backup = manifests
    backup = changed(backup, schema_version="2")
    release_path, backup_path = tmp_path / "release.json", tmp_path / "backup.json"
    release_path.write_text(release.model_dump_json(), encoding="utf-8")
    backup_path.write_text(backup.model_dump_json(), encoding="utf-8")
    assert main(["verify", "--release", str(release_path), "--backup", str(backup_path)]) == 1
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "metadata-incompatible"
    assert output["proof"]["ok"] is False
