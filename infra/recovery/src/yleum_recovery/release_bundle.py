"""Exact-source, offline OCI release assembly and independent verification.

No image builds or registry lookups occur here. Byte integrity is not runtime
certification; a configured independent trust adapter must authenticate that.
"""

import argparse
import hashlib
import json
import re
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from .contracts import Component, Failure, Proof, ReleaseManifest, Role
from .storage import BlobStore, DirectoryBlobStore, file_sha256

REQUIRED_IMAGES = {
    "api": Role.CORE, "web": Role.CORE, "gateway": Role.COMMERCE,
    "billing": Role.COMMERCE, "machine": Role.RUNTIME, "guard": Role.RUNTIME,
    "core": Role.RUNTIME, "helper": Role.RUNTIME, "postgres": Role.CORE,
    "redis": Role.CORE, "minio": Role.CORE, "minio-client": Role.CORE,
    "kit": Role.RUNTIME, "node": Role.RUNTIME, "template-max": Role.RUNTIME,
    "template-bare": Role.RUNTIME, "agent-runner": Role.RUNTIME,
}
REQUIRED_INPUT_ARTIFACTS = frozenset({
    "orchestrator-dependencies", "python-runtime", "uv-runtime", "host-packages",
    "build-provenance",
})
REQUIRED_SOURCE_FILES = frozenset({
    "apps/orchestrator/pyproject.toml", "apps/orchestrator/uv.lock",
    "apps/api/pyproject.toml", "apps/api/uv.lock",
    "apps/llm-gateway/pyproject.toml", "apps/llm-gateway/uv.lock",
    "apps/web/package.json", "apps/web/pnpm-lock.yaml",
    "infra/recovery/pyproject.toml", "infra/recovery/uv.lock",
    "infra/systemd/yleum-orchestrator.service",
    "apps/orchestrator/scripts/build-public-max-core.sh",
    "apps/orchestrator/scripts/public-max-core/Dockerfile",
    "apps/orchestrator/templates/max-miniapp-nextjs/pnpm-lock.yaml",
})
_OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
_OCI_CONFIG = "application/vnd.oci.image.config.v1+json"
_MAX_JSON = 1024 * 1024


@dataclass(frozen=True)
class ArtifactInput:
    component_id: str
    archive: Path
    archive_sha256: str


@dataclass(frozen=True)
class ImageInput(ArtifactInput):
    image_digest: str


@dataclass(frozen=True)
class ReleaseInputs:
    repository: Path
    output_directory: Path
    store: BlobStore
    ci_sha: str
    schema_version: str
    protocol_version: str
    images: tuple[ImageInput, ...]
    artifacts: tuple[ArtifactInput, ...]


def blob_key(manifest: ReleaseManifest, component_id: str) -> str:
    return f"{manifest.release_id}--{component_id}"


def _members(archive: tarfile.TarFile) -> dict[str, tarfile.TarInfo]:
    members: dict[str, tarfile.TarInfo] = {}
    for item in archive:
        name = item.name.rstrip("/")
        path = PurePosixPath(name)
        if (not name or path.is_absolute() or ".." in path.parts or "\\" in name
                or ":" in name or name in members or not (item.isfile() or item.isdir())):
            raise ValueError("Unsafe release archive member")
        if len(members) >= 100000:
            raise ValueError("Release archive member limit exceeded")
        members[name] = item
    return members


def _json_member(
    archive: tarfile.TarFile, members: dict[str, tarfile.TarInfo], name: str
) -> Any:
    item = members.get(name)
    if item is None or not item.isfile() or item.size > _MAX_JSON:
        raise ValueError("Required archive metadata missing or too large")
    content = archive.extractfile(item)
    if content is None:
        raise ValueError("Archive metadata unavailable")
    with content:
        return json.load(content)


def _descriptor(
    archive: tarfile.TarFile, members: dict[str, tarfile.TarInfo], descriptor: dict[str, Any]
) -> str:
    digest = descriptor.get("digest", "")
    if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("Immutable OCI digest required")
    name = "blobs/sha256/" + digest.removeprefix("sha256:")
    item = members.get(name)
    if (item is None or not item.isfile() or type(descriptor.get("size")) is not int
            or item.size != descriptor["size"]):
        raise ValueError("OCI descriptor size or blob missing")
    content = archive.extractfile(item)
    if content is None:
        raise ValueError("OCI blob unavailable")
    with content:
        hasher = hashlib.sha256()
        while chunk := content.read(1024 * 1024):
            hasher.update(chunk)
        actual = hasher.hexdigest()
    if "sha256:" + actual != digest:
        raise ValueError("OCI blob digest mismatch")
    return name


def verify_oci_archive(path: Path, expected_digest: str) -> None:
    """Verify every OCI descriptor without extracting or executing layer bytes."""
    with tarfile.open(path, "r:*") as archive:
        members = _members(archive)
        if _json_member(archive, members, "oci-layout") != {"imageLayoutVersion": "1.0.0"}:
            raise ValueError("OCI layout version unsupported")
        index = _json_member(archive, members, "index.json")
        descriptors = index.get("manifests", [])
        if index.get("schemaVersion") != 2 or len(descriptors) != 1:
            raise ValueError("Exactly one platform-specific OCI image required")
        descriptor = descriptors[0]
        if descriptor.get("digest") != expected_digest or descriptor.get("mediaType") != (
            _OCI_MANIFEST
        ):
            raise ValueError("OCI image digest mismatch")
        manifest = _json_member(archive, members, _descriptor(archive, members, descriptor))
        if manifest.get("schemaVersion") != 2 or manifest.get("mediaType") != _OCI_MANIFEST:
            raise ValueError("OCI image manifest unsupported")
        config_descriptor = manifest["config"]
        if config_descriptor.get("mediaType") != _OCI_CONFIG:
            raise ValueError("OCI image config unsupported")
        config = _json_member(
            archive, members, _descriptor(archive, members, config_descriptor)
        )
        if config.get("os") != "linux" or config.get("architecture") != "amd64":
            raise ValueError("OCI platform must be Linux x86_64")
        platform = descriptor.get("platform")
        if platform is not None and (
            platform.get("os") != "linux" or platform.get("architecture") != "amd64"
        ):
            raise ValueError("OCI index platform mismatch")
        layers = manifest.get("layers")
        if not isinstance(layers, list) or not layers:
            raise ValueError("OCI image layers required")
        for layer in layers:
            if layer.get("mediaType") not in {
                "application/vnd.oci.image.layer.v1.tar",
                "application/vnd.oci.image.layer.v1.tar+gzip",
                "application/vnd.oci.image.layer.v1.tar+zstd",
            } or "urls" in layer:
                raise ValueError("Offline OCI layers required")
            _descriptor(archive, members, layer)


def _verify_source(path: Path, source_sha: str) -> dict[str, str]:
    with tarfile.open(path, "r:*") as archive:
        members = _members(archive)
        if archive.pax_headers.get("comment") != source_sha:
            raise ValueError("Source archive does not declare the bound exact commit")
        if not REQUIRED_SOURCE_FILES <= members.keys():
            raise ValueError("Full source and locks are required")
        for name in members:
            base = PurePosixPath(name).name.lower()
            if (base == ".env" or (base.startswith(".env.") and not base.endswith(
                (".example", ".sample", ".template")
            )) or base.endswith((".yleum-recovery", ".p12", ".pfx", ".key"))
                    or base in {"id_rsa", "id_ed25519", "credentials.json"}):
                raise ValueError("Private material prohibited in public source bundle")
        hashes = {}
        for name in REQUIRED_SOURCE_FILES:
            content = archive.extractfile(members[name])
            if content is None or not members[name].isfile():
                raise ValueError("Source lock or build file unavailable")
            with content:
                hashes[name] = hashlib.sha256(content.read()).hexdigest()
        return hashes


def _verify_provenance(
    path: Path, manifest: ReleaseManifest, source_hashes: dict[str, str]
) -> None:
    if path.stat().st_size > _MAX_JSON:
        raise ValueError("Build provenance metadata too large")
    document = json.loads(path.read_bytes())
    if (set(document) != {"format_version", "source_sha", "ci_sha", "image_digests",
                          "source_hashes", "origins"}
            or type(document["format_version"]) is not int or document["format_version"] != 1
            or document["source_sha"] != manifest.git_sha or document["ci_sha"] != manifest.git_sha
            or document["image_digests"] != manifest.image_digests
            or document["source_hashes"] != source_hashes
            or set(document["origins"]) != {"kit", "node"}):
        raise ValueError("Build inputs do not match exact source, locks and images")
    for name in ["kit", "node"]:
        origin = document["origins"][name]
        if not isinstance(origin, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9./:_-]*@sha256:[0-9a-f]{64}", origin
        ) or not origin.endswith("@" + manifest.image_digests[name]):
            raise ValueError("Immutable kit and Node origins required")


def _failure(code: str, message: str, component: str | None = None) -> Failure:
    return Failure(code=code, component_id=component, message=message)


def _coverage(manifest: ReleaseManifest) -> list[Failure]:
    failures = []
    expected = set(REQUIRED_IMAGES) | REQUIRED_INPUT_ARTIFACTS | {"source"}
    if (not expected <= manifest.archive_hashes.keys()
            or set(REQUIRED_IMAGES) != set(manifest.image_digests)
            or not manifest.image_digests.keys() <= manifest.archive_hashes.keys()):
        failures.append(_failure("MISSING_ARTIFACT", "Mandatory offline artifacts are missing"))
    components = {component.component_id: component for component in manifest.components}
    for image, role in REQUIRED_IMAGES.items():
        component = components.get(image)
        if (component is None or not component.required or component.kind != "image"
                or component.role != role):
            failures.append(_failure("COMPONENT_MISMATCH", "Required image binding missing", image))
    if manifest.release_id != "recovery-" + manifest.git_sha:
        failures.append(_failure("SOURCE_MISMATCH", "Release identity must pin exact source SHA"))
    return failures


def build_release_bundle(source_sha: str, inputs: ReleaseInputs) -> ReleaseManifest:
    """Assemble only after successful CI of the exact SHA, then verify readback.

    ci_sha is caller-supplied metadata; matching it does not authenticate CI.
    Callers must authenticate successful CI and input provenance before calling.
    No CI authentication workflow is implemented by this module.
    """
    if not re.fullmatch(r"[0-9a-f]{40}", source_sha) or inputs.ci_sha != source_sha:
        raise ValueError("Successful CI for exact full source SHA required")
    names = [item.component_id for item in (*inputs.images, *inputs.artifacts)]
    if len(names) != len(set(names)) or set(names) != (
        set(REQUIRED_IMAGES) | REQUIRED_INPUT_ARTIFACTS
    ) or {item.component_id for item in inputs.images} != set(REQUIRED_IMAGES):
        raise ValueError("Complete immutable release input set required")
    inputs.output_directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=inputs.output_directory) as temporary:
        source = Path(temporary) / "source.tar"
        result = subprocess.run(
            ["git", "-C", str(inputs.repository), "archive", "--format=tar",
             "--output", str(source.resolve()), source_sha],
            capture_output=True, check=False,
        )
        if result.returncode != 0:
            raise ValueError("Exact source archive unavailable")
        source_hashes = _verify_source(source, source_sha)
        hashes = {"source": file_sha256(source)}
        for item in (*inputs.images, *inputs.artifacts):
            if file_sha256(item.archive) != item.archive_sha256:
                raise ValueError("Release input integrity check failed")
            hashes[item.component_id] = item.archive_sha256
        for image in inputs.images:
            verify_oci_archive(image.archive, image.image_digest)
        manifest = ReleaseManifest(
            release_id="recovery-" + source_sha, git_sha=source_sha,
            schema_version=inputs.schema_version, protocol_version=inputs.protocol_version,
            platform="linux", architecture="x86_64",
            components=tuple(Component(
                component_id=name, role=role, kind="image", required=True,
                owner_id="platform", binding_id=name, capture_handler_id="oci-archive",
            ) for name, role in sorted(REQUIRED_IMAGES.items())),
            image_digests={image.component_id: image.image_digest for image in inputs.images},
            archive_hashes=hashes,
        )
        provenance = next(item.archive for item in inputs.artifacts
                          if item.component_id == "build-provenance")
        _verify_provenance(provenance, manifest, source_hashes)
        for name, path in [("source", source), *[
            (item.component_id, item.archive) for item in (*inputs.images, *inputs.artifacts)
        ]]:
            inputs.store.put_verified(blob_key(manifest, name), path, hashes[name])
        proof = verify_release_bundle(manifest, inputs.store)
        if not proof.ok:
            raise ValueError("Independent release readback failed")
        # Manifest is the atomic completion marker; older bytes are never replaced.
        path = Path(temporary) / "manifest.json"
        path.write_bytes(canonical_manifest(manifest))
        inputs.store.put_verified(blob_key(manifest, "manifest.json"), path, file_sha256(path))
        return manifest


def verify_release_bundle(manifest: ReleaseManifest, store: BlobStore) -> Proof:
    try:
        ReleaseManifest.model_validate(manifest.model_dump())
    except ValueError:
        return Proof(ok=False, failures=(
            _failure("INVALID_MANIFEST", "Release metadata invalid"),
        ))
    failures = _coverage(manifest)
    source_hashes = None
    provenance = None
    with tempfile.TemporaryDirectory() as temporary:
        for number, (name, expected) in enumerate(sorted(manifest.archive_hashes.items())):
            destination = Path(temporary) / str(number)
            try:
                store.fetch_verified(blob_key(manifest, name), destination, expected)
                # Never trust an adapter's successful return alone.
                if file_sha256(destination) != expected:
                    raise ValueError("Archive hash mismatch")
                if name in manifest.image_digests:
                    verify_oci_archive(destination, manifest.image_digests[name])
                elif name == "source":
                    source_hashes = _verify_source(destination, manifest.git_sha)
                elif name == "build-provenance":
                    provenance = destination
            except Exception:
                # External adapters may raise credential-bearing transport errors.
                # Refuse safely without echoing access URLs or their exceptions.
                failures.append(_failure(
                    "INVALID_ARTIFACT", "Release artifact unavailable or invalid", name
                ))
        if source_hashes is not None and provenance is not None:
            try:
                _verify_provenance(provenance, manifest, source_hashes)
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                failures.append(_failure(
                    "INVALID_PROVENANCE", "Immutable build input provenance rejected"
                ))
    return Proof(ok=not failures, failures=tuple(failures))


def canonical_manifest(manifest: ReleaseManifest) -> bytes:
    """Evidence reference excluded so receipt binding cannot become circular."""
    return json.dumps(
        manifest.model_dump(mode="json", exclude={"certification_evidence_id"}),
        sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("ascii")


class CertificationVerifier(Protocol):
    def verify(self, *, evidence_id: str, manifest_sha256: str, source_sha: str) -> Proof:
        """Authenticate independent isolated/offline runtime receipt and coverage.

        Trust roots, receipt signatures and actual tested roles/images belong to
        this external adapter. A string reference or successful CI is insufficient.
        """
        ...


@dataclass(frozen=True)
class CertificationStatus:
    certified: bool
    proof: Proof


def check_certification(
    manifest: ReleaseManifest, store: BlobStore, verifier: CertificationVerifier | None = None,
) -> CertificationStatus:
    failures = list(verify_release_bundle(manifest, store).failures)
    if verifier is None or manifest.certification_evidence_id is None:
        failures.append(_failure(
            "CERTIFICATION_REQUIRED", "Authenticated runtime receipt required"
        ))
    else:
        try:
            proof = verifier.verify(
                evidence_id=manifest.certification_evidence_id,
                manifest_sha256=hashlib.sha256(canonical_manifest(manifest)).hexdigest(),
                source_sha=manifest.git_sha,
            )
            failures.extend(proof.failures)
        except Exception:
            # Trust adapter exceptions must not expose signed URLs or credentials.
            failures.append(_failure(
                "CERTIFICATION_REJECTED", "Runtime receipt verification failed"
            ))
    proof = Proof(ok=not failures, failures=tuple(failures))
    return CertificationStatus(certified=proof.ok, proof=proof)


def publish_certified_index(
    manifest: ReleaseManifest, store: BlobStore, verifier: CertificationVerifier | None = None,
) -> str:
    if not check_certification(manifest, store, verifier).certified:
        raise ValueError("Authenticated offline runtime certification required")
    with tempfile.TemporaryDirectory() as temporary:
        marker = Path(temporary) / "certified.json"
        marker.write_text(json.dumps({
            "format_version": 1, "release_id": manifest.release_id,
            "git_sha": manifest.git_sha,
            "manifest_sha256": hashlib.sha256(canonical_manifest(manifest)).hexdigest(),
            "certification_evidence_id": manifest.certification_evidence_id,
        }, sort_keys=True), encoding="utf-8")
        return store.put_verified(blob_key(manifest, "certified.json"), marker, file_sha256(marker))


def main() -> int:
    parser = argparse.ArgumentParser(description="Assemble a verified staging recovery bundle")
    parser.add_argument("source_sha")
    parser.add_argument("inputs", type=Path)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        document = json.loads(args.inputs.read_text(encoding="utf-8"))
        if set(document) != {"ci_sha", "schema_version", "protocol_version", "images", "artifacts"}:
            raise ValueError("Release input document fields rejected")

        def load(item: dict[str, str], image: bool) -> ArtifactInput:
            expected = {"component_id", "archive", "archive_sha256"} | (
                {"image_digest"} if image else set()
            )
            if set(item) != expected or not re.fullmatch(r"[A-Za-z0-9_.-]+", item["archive"]):
                raise ValueError("Local immutable artifact filename required")
            archive = args.inputs.parent / item["archive"]
            if image:
                return ImageInput(
                    item["component_id"], archive, item["archive_sha256"], item["image_digest"]
                )
            return ArtifactInput(item["component_id"], archive, item["archive_sha256"])

        images = tuple(load(item, True) for item in document["images"])
        artifacts = tuple(load(item, False) for item in document["artifacts"])
        build_release_bundle(args.source_sha, ReleaseInputs(
            repository=args.repository, output_directory=args.output / ".staging",
            store=DirectoryBlobStore(args.output), ci_sha=document["ci_sha"],
            schema_version=document["schema_version"],
            protocol_version=document["protocol_version"],
            images=images, artifacts=artifacts,  # type: ignore[arg-type]
        ))
    except (OSError, ValueError, KeyError, TypeError, AttributeError, tarfile.TarError):
        print("Staging release refused: exact source and complete verified inputs required")
        return 1
    print("Staging release bytes verified; independent runtime certification still required")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
