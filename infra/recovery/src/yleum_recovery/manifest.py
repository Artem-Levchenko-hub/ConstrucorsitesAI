"""Pure metadata verification: no secrets, remote access or runtime readiness."""

from .contracts import BackupManifest, Failure, Proof, ReleaseManifest, RestorePoint, Role


def verify_manifests(release: ReleaseManifest, backup: BackupManifest) -> Proof:
    """Check declared bindings and completeness, not the artifacts or signatures.

    A successful result only says the metadata agrees. Verifying archive bytes,
    signed capture identity, secret export contents and certification is separate.
    """
    failures: list[Failure] = []

    def fail(code: str, message: str, component_id: str | None = None) -> None:
        failures.append(Failure(code=code, component_id=component_id, message=message))

    if backup.release_sha != release.git_sha or any(
        getattr(release, field) != getattr(backup, field)
        for field in (
            "release_id",
            "schema_version",
            "protocol_version",
            "platform",
            "architecture",
        )
    ):
        fail("FORMAT_MISMATCH", "Release and backup metadata are incompatible")
    if backup.db_format not in release.supported_db_formats or (
        backup.db_format == "logical-snapshot" and backup.requires_wal_replay
    ):
        fail("FORMAT_MISMATCH", "Database format cannot support the requested restore mode")
    if backup.completeness != "complete":
        fail("INCOMPLETE_BACKUP", "Backup does not declare a complete capture")
    if set(backup.inventory.roles) != set(Role):
        fail("MISSING_ROLE", "Capture inventory must include all three roles")

    inventory = {c.component_id: c for c in backup.inventory.components}
    expected = {c.component_id: c for c in release.components if c.required}
    required = expected | {c.component_id: c for c in inventory.values() if c.required}
    # Even an empty database inventory is meaningful only with a separate signed
    # inventory proof; a bare empty manifest must not pass as a full three-role kit.
    if not required:
        fail("EMPTY_INVENTORY", "No mandatory components are declared")
    for component_id, component in expected.items():
        captured_definition = inventory.get(component_id)
        if captured_definition is None:
            fail("MISSING_COMPONENT", "Required component is missing from inventory", component_id)
        elif captured_definition != component:
            fail(
                "COMPONENT_MISMATCH",
                "Required component binding differs from release",
                component_id,
            )

    if {component.role for component in required.values()} != set(Role):
        fail("MISSING_ROLE", "Required components must cover all three roles")
    for component_id, component in required.items():
        if component.kind == "unknown":
            fail(
                "UNKNOWN_COMPONENT", "Required component has no known classification", component_id
            )
        if component_id not in backup.captured_components:
            fail("MISSING_COMPONENT", "Required component was not captured", component_id)
        if backup.component_completeness.get(component_id) != "complete":
            fail("INCOMPLETE_COMPONENT", "Required component capture is incomplete", component_id)
        if component_id not in backup.hashes:
            fail("MISSING_HASH", "Required component has no archive hash", component_id)
        if component_id not in backup.watermarks and component_id not in backup.object_versions:
            fail(
                "MISSING_WATERMARK",
                "Required component has no recovery boundary marker",
                component_id,
            )

    required_images = {
        component.component_id for component in required.values() if component.kind == "image"
    }
    for component_id in sorted(release.image_digests.keys() | required_images):
        if component_id not in release.archive_hashes:
            fail("MISSING_ARTIFACT", "Pinned image has no trusted archive hash", component_id)
    for component_id in sorted(required_images):
        if component_id not in release.image_digests:
            fail("MISSING_ARTIFACT", "Required image has no pinned digest", component_id)

    unknown_captures = set(backup.captured_components) - inventory.keys()
    if unknown_captures:
        fail("UNKNOWN_COMPONENT", "Captured component is not described in inventory")
    return Proof(ok=not failures, failures=tuple(failures))


def select_restore_point(points: tuple[RestorePoint, ...]) -> RestorePoint:
    """Select the newest compatible declared point and its bound exact release.

    The caller must independently authenticate certification/capture evidence and
    verify the selected artifacts before using this candidate for recovery.
    """
    compatible = [
        point
        for point in points
        if point.db_format == point.backup.db_format
        and verify_manifests(point.release, point.backup).ok
    ]
    if not compatible:
        raise ValueError("No metadata-compatible restore point is available")
    return max(compatible, key=lambda point: (point.backup.boundary, point.point_id))
