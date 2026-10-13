"""Version-one public metadata and in-memory secret contracts.

Capture signatures and certification IDs are references, not verified evidence.
SecretMaterial has deliberately no plaintext serializer: K2 owns the encrypted
envelope boundary. Ordinary Pydantic dumps and representation stay public-safe.
"""

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
    computed_field,
    field_validator,
    model_validator,
)

Identifier = Annotated[str, StringConstraints(min_length=1, pattern=r"^[A-Za-z0-9_.:-]+$")]
NonEmpty = Annotated[str, StringConstraints(min_length=1)]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
ImageDigest = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
GitSha = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
DatabaseFormat = Literal["logical-snapshot", "physical-wal"]
Completeness = Literal["complete", "partial", "deferred", "unknown", "missing"]
ComponentKind = Literal[
    "database",
    "volume",
    "object-store",
    "filesystem",
    "configuration",
    "source",
    "image",
    "queue",
    "journal",
    "cache",
    "unknown",
]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    format_version: Literal[1] = 1

    @field_validator("format_version", mode="before")
    @classmethod
    def exact_format_version(cls, value: object) -> Literal[1]:
        if type(value) is not int or value != 1:
            raise ValueError("Only integer format version 1 is supported")
        return 1


class Role(StrEnum):
    CORE = "core"
    RUNTIME = "runtime"
    COMMERCE = "commerce"


class Component(Contract):
    component_id: Identifier
    role: Role
    kind: ComponentKind
    required: bool = Field(strict=True)
    owner_id: Identifier
    binding_id: Identifier
    capture_handler_id: Identifier


def _unique_components(components: tuple[Component, ...]) -> None:
    if len({component.component_id for component in components}) != len(components):
        raise ValueError("Component IDs must be unique")


class Inventory(Contract):
    roles: tuple[Role, ...]
    components: tuple[Component, ...]
    capture_id: Identifier
    signer_id: Identifier
    signature: NonEmpty

    @model_validator(mode="after")
    def unique_inventory(self) -> Self:
        _unique_components(self.components)
        if len(set(self.roles)) != len(self.roles):
            raise ValueError("Inventory roles must be unique")
        if any(component.role not in self.roles for component in self.components):
            raise ValueError("Component role must belong to inventory roles")
        return self


class SecretMaterial(Contract):
    export_id: Identifier
    credentials: dict[Identifier, SecretStr] = Field(exclude=True, repr=False)
    keys: dict[Identifier, SecretStr] = Field(exclude=True, repr=False)
    role_exports: dict[Role, dict[Identifier, SecretStr]] = Field(exclude=True, repr=False)

    @model_validator(mode="after")
    def all_role_exports(self) -> Self:
        if set(self.role_exports) != set(Role) or not all(self.role_exports.values()):
            raise ValueError("Secret exports for all three roles are required")
        return self


class RecoveryProfile(Contract):
    name: Identifier
    provider_adapter: Identifier
    provider_account: Identifier
    provider_group: Identifier
    role_selectors: dict[Role, NonEmpty]
    storage_adapter: Identifier
    lease_adapter: Identifier
    ingress_adapter: Identifier
    secret_material: SecretMaterial = Field(exclude=True, repr=False)

    @model_validator(mode="after")
    def all_role_selectors(self) -> Self:
        if set(self.role_selectors) != set(Role):
            raise ValueError("Selectors for all three roles are required")
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def secret_export_id(self) -> str:
        return self.secret_material.export_id

    @property
    def public_reference(self) -> str:
        return f"local-profile:{self.name}"


class ReleaseManifest(Contract):
    release_id: Identifier
    git_sha: GitSha
    schema_version: Identifier
    protocol_version: Identifier
    platform: Literal["linux"]
    architecture: Literal["x86_64"]
    components: tuple[Component, ...]
    image_digests: dict[Identifier, ImageDigest] = Field(min_length=1)
    archive_hashes: dict[Identifier, Sha256] = Field(min_length=1)
    # None denotes staging. Even an explicit ID is only a reference: K3 must
    # independently authenticate its receipt before treating it as certified.
    certification_evidence_id: Identifier | None = None
    supported_db_formats: tuple[DatabaseFormat, ...] = ("logical-snapshot", "physical-wal")

    @model_validator(mode="after")
    def unique_release_components(self) -> Self:
        _unique_components(self.components)
        if not self.supported_db_formats or len(set(self.supported_db_formats)) != len(
            self.supported_db_formats
        ):
            raise ValueError("Supported database formats must be nonempty and unique")
        return self


class BackupManifest(Contract):
    backup_id: Identifier
    release_id: Identifier
    release_sha: GitSha
    secret_export_id: Identifier
    schema_version: Identifier
    protocol_version: Identifier
    platform: Literal["linux"]
    architecture: Literal["x86_64"]
    inventory: Inventory
    captured_components: tuple[Identifier, ...]
    hashes: dict[Identifier, Sha256]
    completeness: Completeness
    component_completeness: dict[Identifier, Completeness]
    boundary: datetime
    watermarks: dict[Identifier, NonEmpty]
    object_versions: dict[Identifier, NonEmpty]
    db_format: DatabaseFormat
    requires_wal_replay: bool = Field(default=False, strict=True)
    rpo_seconds: int = Field(ge=0, strict=True)

    @field_validator("boundary")
    @classmethod
    def utc_boundary(cls, value: datetime) -> datetime:
        if value.utcoffset() != timedelta(0):
            raise ValueError("Recovery boundary must explicitly use UTC")
        return value

    @model_validator(mode="after")
    def capture_metadata(self) -> Self:
        if len(set(self.captured_components)) != len(self.captured_components):
            raise ValueError("Captured component IDs must be unique")
        if self.db_format == "logical-snapshot" and self.rpo_seconds == 0:
            raise ValueError("Logical snapshots must declare a nonzero possible loss window")
        return self


class RestorePoint(Contract):
    point_id: Identifier
    release: ReleaseManifest
    backup: BackupManifest
    db_format: DatabaseFormat


class Failure(Contract):
    code: Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]+$")]
    component_id: Identifier | None = None
    message: NonEmpty


class Proof(Contract):
    """Metadata verification outcome. Never a ready/certified claim."""

    ok: bool = Field(strict=True)
    failures: tuple[Failure, ...]

    @model_validator(mode="after")
    def coherent_result(self) -> Self:
        if self.ok != (not self.failures):
            raise ValueError("Proof result must agree with its failures")
        return self
