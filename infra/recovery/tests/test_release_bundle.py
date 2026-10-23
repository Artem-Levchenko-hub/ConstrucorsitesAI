"""Synthetic OCI bytes prove refusal/integrity contracts, never runtime acceptance."""

import hashlib
import io
import json
import subprocess
import tarfile
from dataclasses import replace

import httpx
import pytest


def implementation():
    # Missing implementation fails an assertion before the first production edit.
    import importlib.util

    assert importlib.util.find_spec("yleum_recovery.release_bundle") is not None
    from yleum_recovery import release_bundle, storage

    return release_bundle, storage


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def tar_bytes(files):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        for name, raw in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(raw)
            archive.addfile(member, io.BytesIO(raw))
    return output.getvalue()


def oci(path, *, arch="amd64", corrupt_layer=False, unsafe=False):
    layer = b"synthetic opaque layer"
    config = json.dumps({"os": "linux", "architecture": arch}).encode()

    def descriptor(raw, kind):
        return {"mediaType": kind, "digest": "sha256:" + digest(raw), "size": len(raw)}

    manifest = json.dumps({
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "config": descriptor(config, "application/vnd.oci.image.config.v1+json"),
        "layers": [descriptor(layer, "application/vnd.oci.image.layer.v1.tar")],
    }).encode()
    index = json.dumps({"schemaVersion": 2, "manifests": [
        descriptor(manifest, "application/vnd.oci.image.manifest.v1+json")
    ]}).encode()
    files = {
        "oci-layout": b'{"imageLayoutVersion":"1.0.0"}', "index.json": index,
        "blobs/sha256/" + digest(manifest): manifest,
        "blobs/sha256/" + digest(config): config,
        "blobs/sha256/" + digest(layer): b"wrong" if corrupt_layer else layer,
    }
    if unsafe:
        files["../escape"] = b"evil"
    path.write_bytes(tar_bytes(files))
    return "sha256:" + digest(manifest)


@pytest.fixture
def bundle(tmp_path):
    rb, storage = implementation()
    repository = tmp_path / "repo"
    repository.mkdir()
    for name in rb.REQUIRED_SOURCE_FILES:
        file = repository / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("synthetic public source\n")
    for args in [
        ["init"], ["add", "."],
        ["-c", "user.name=Recovery QA", "-c", "user.email=qa@example.test",
         "commit", "-m", "synthetic source"],
    ]:
        subprocess.run(["git", "-C", str(repository), *args], check=True, capture_output=True)
    sha = subprocess.check_output(["git", "-C", str(repository), "rev-parse", "HEAD"],
                                  text=True).strip()
    images = []
    for name in rb.REQUIRED_IMAGES:
        archive = tmp_path / (name + ".tar")
        image_digest = oci(archive)
        images.append(rb.ImageInput(name, archive, digest(archive.read_bytes()), image_digest))
    artifacts = []
    for name in rb.REQUIRED_INPUT_ARTIFACTS:
        file = tmp_path / name
        if name == "build-provenance":
            file.write_text(json.dumps({
                "format_version": 1, "source_sha": sha, "ci_sha": sha,
                "image_digests": {image.component_id: image.image_digest for image in images},
                "source_hashes": {name: digest((repository / name).read_bytes())
                                  for name in rb.REQUIRED_SOURCE_FILES},
                "origins": {name: "registry.example.test/public/" + name + "@" +
                            next(image.image_digest for image in images
                                 if image.component_id == name) for name in ["kit", "node"]},
            }))
        else:
            file.write_bytes(b"synthetic public offline input")
        artifacts.append(rb.ArtifactInput(name, file, digest(file.read_bytes())))
    store = storage.DirectoryBlobStore(tmp_path / "external")
    inputs = rb.ReleaseInputs(
        repository=repository, output_directory=tmp_path / "output", store=store,
        ci_sha=sha, schema_version="1", protocol_version="1", images=tuple(images),
        artifacts=tuple(artifacts),
    )
    return rb, storage, sha, inputs


def test_all_images_required(bundle):
    rb, _, sha, inputs = bundle
    manifest = rb.build_release_bundle(sha, inputs)
    assert {"api", "web", "gateway", "billing", "machine", "guard", "core", "helper",
            "postgres", "redis", "kit", "node", "minio", "minio-client",
            "template-max", "template-bare", "agent-runner"} <= set(manifest.image_digests)
    assert rb.verify_release_bundle(manifest, inputs.store).ok


@pytest.mark.parametrize("missing", ["kit", "node", "billing", "helper", "minio"])
def test_missing_required_image_refused_before_upload(bundle, missing):
    rb, _, sha, inputs = bundle
    with pytest.raises(ValueError):
        rb.build_release_bundle(sha, replace(inputs, images=tuple(
            image for image in inputs.images if image.component_id != missing)))
    assert not list(inputs.store.root.glob("*"))


def test_old_registry_offline(bundle):
    rb, storage, sha, inputs = bundle
    manifest = rb.build_release_bundle(sha, inputs)
    # Independent verification has only external bytes, no source tree or registry client.
    offline = storage.DirectoryBlobStore(inputs.store.root)
    assert rb.verify_release_bundle(manifest, offline).ok


@pytest.mark.parametrize("fault", ["arch", "digest", "layer", "traversal"])
def test_digest_arch_mismatch(bundle, fault):
    rb, _, sha, inputs = bundle
    first = inputs.images[0]
    pinned = oci(first.archive, arch="arm64" if fault == "arch" else "amd64",
                 corrupt_layer=fault == "layer", unsafe=fault == "traversal")
    first = replace(first, archive_sha256=digest(first.archive.read_bytes()),
                    image_digest="sha256:" + "f" * 64 if fault == "digest" else pinned)
    with pytest.raises(ValueError):
        rb.build_release_bundle(sha, replace(inputs, images=(first, *inputs.images[1:])))
    assert not list(inputs.store.root.glob("*"))


def test_ci_without_runtime_certification(bundle):
    rb, _, sha, inputs = bundle
    manifest = rb.build_release_bundle(sha, inputs)
    assert manifest.certification_evidence_id is None
    result = rb.check_certification(manifest, inputs.store)
    assert result.certified is False
    assert "CERTIFICATION_REQUIRED" in {failure.code for failure in result.proof.failures}


def test_wrong_ci_sha_and_short_source_refused(bundle):
    rb, _, sha, inputs = bundle
    for source, ci in [(sha[:7], sha), (sha, "b" * 40)]:
        with pytest.raises(ValueError):
            rb.build_release_bundle(source, replace(inputs, ci_sha=ci))
    assert not list(inputs.store.root.glob("*"))


def test_missing_offline_input_refused(bundle):
    rb, _, sha, inputs = bundle
    with pytest.raises(ValueError):
        rb.build_release_bundle(sha, replace(inputs, artifacts=inputs.artifacts[:-1]))


def test_archive_corruption_and_absence_fail_independently(bundle):
    rb, _, sha, inputs = bundle
    manifest = rb.build_release_bundle(sha, inputs)
    artifact = inputs.store.root / rb.blob_key(manifest, "node")
    artifact.write_bytes(b"tampered external bytes")
    assert not rb.verify_release_bundle(manifest, inputs.store).ok
    artifact.unlink()
    assert not rb.verify_release_bundle(manifest, inputs.store).ok


def test_manifest_cannot_omit_required_image(bundle):
    rb, _, sha, inputs = bundle
    manifest = rb.build_release_bundle(sha, inputs)
    altered = manifest.model_copy(update={"image_digests": {
        key: value for key, value in manifest.image_digests.items() if key != "kit"}})
    assert not rb.verify_release_bundle(altered, inputs.store).ok


def test_tracked_secret_file_refused(bundle):
    rb, _, _, inputs = bundle
    (inputs.repository / ".env").write_text("SYNTHETIC_PRIVATE_VALUE=never-publish")
    subprocess.run(["git", "-C", str(inputs.repository), "add", ".env"], check=True)
    subprocess.run(["git", "-C", str(inputs.repository), "-c", "user.name=QA",
                    "-c", "user.email=qa@example.test", "commit", "-m", "private fixture"],
                   check=True, capture_output=True)
    sha = subprocess.check_output(["git", "-C", str(inputs.repository), "rev-parse", "HEAD"],
                                  text=True).strip()
    with pytest.raises(ValueError):
        rb.build_release_bundle(sha, replace(inputs, ci_sha=sha))
    assert not list(inputs.store.root.glob("*"))


def test_create_only_storage_preserves_existing_bytes(tmp_path):
    _, storage = implementation()
    store = storage.DirectoryBlobStore(tmp_path / "external")
    source = tmp_path / "input"
    source.write_bytes(b"accepted")
    store.put_verified("one", source, digest(b"accepted"))
    source.write_bytes(b"changed")
    with pytest.raises(ValueError):
        store.put_verified("one", source, digest(b"changed"))
    assert (store.root / "one").read_bytes() == b"accepted"
    destination = tmp_path / "download"
    destination.write_bytes(b"previous good download")
    with pytest.raises(ValueError):
        store.fetch_verified("one", destination, digest(b"changed"))
    assert destination.read_bytes() == b"previous good download"


@pytest.mark.parametrize("key", ["../escape", "/absolute", "a/b", "a\\b"])
def test_storage_rejects_path_keys(tmp_path, key):
    _, storage = implementation()
    source = tmp_path / "input"
    source.write_bytes(b"accepted")
    store = storage.DirectoryBlobStore(tmp_path / "external")
    with pytest.raises(ValueError):
        store.put_verified(key, source, digest(b"accepted"))


def test_release_asset_store_fetches_by_asset_id_and_checks_bytes(tmp_path):
    _, storage = implementation()
    raw = b"external artifact"

    def handler(request):
        assert request.url.host == "api.github.com"
        if request.url.path.endswith("/assets"):
            return httpx.Response(200, json=[{"id": 9, "name": "one"}])
        if request.url.path.endswith("/assets/9"):
            return httpx.Response(200, content=raw)
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        store = storage.GitHubReleaseAssetStore("owner/repo", 7, client=client)
        output = tmp_path / "download"
        assert store.fetch_verified("one", output, digest(raw)).read_bytes() == raw
        with pytest.raises(ValueError):
            store.fetch_verified("one", output, digest(b"bad"))
        assert output.read_bytes() == raw


def test_arbitrary_evidence_id_cannot_certify(bundle):
    rb, _, sha, inputs = bundle
    manifest = rb.build_release_bundle(sha, inputs).model_copy(
        update={"certification_evidence_id": "arbitrary-reference"})
    assert rb.check_certification(manifest, inputs.store).certified is False


def test_no_trust_means_no_certified_index(bundle):
    rb, _, sha, inputs = bundle
    manifest = rb.build_release_bundle(sha, inputs)
    with pytest.raises(ValueError):
        rb.publish_certified_index(manifest, inputs.store)
    assert not any("certified" in path.name for path in inputs.store.root.iterdir())


@pytest.mark.parametrize("fault", ["source", "ci", "lock", "kit", "node", "origin"])
def test_wrong_build_provenance_refused_before_upload(bundle, fault):
    rb, _, sha, inputs = bundle
    item = next(item for item in inputs.artifacts if item.component_id == "build-provenance")
    document = json.loads(item.archive.read_text())
    if fault in {"source", "ci"}:
        document[fault + "_sha"] = "b" * 40
    elif fault == "lock":
        document["source_hashes"]["apps/orchestrator/uv.lock"] = "b" * 64
    elif fault in {"kit", "node"}:
        document["image_digests"][fault] = "sha256:" + "b" * 64
    else:
        document["origins"]["kit"] = "registry.example.test/kit:latest"
    item.archive.write_text(json.dumps(document))
    changed = replace(item, archive_sha256=digest(item.archive.read_bytes()))
    with pytest.raises(ValueError):
        rb.build_release_bundle(sha, replace(inputs, artifacts=tuple(
            changed if entry.component_id == "build-provenance" else entry
            for entry in inputs.artifacts)))
    assert not list(inputs.store.root.glob("*"))


def test_source_archive_commit_must_match_manifest(bundle):
    rb, _, sha, inputs = bundle
    manifest = rb.build_release_bundle(sha, inputs)
    changed = manifest.model_copy(update={
        "git_sha": "b" * 40, "release_id": "recovery-" + "b" * 40,
    })
    for name in manifest.archive_hashes:
        (inputs.store.root / rb.blob_key(changed, name)).write_bytes(
            (inputs.store.root / rb.blob_key(manifest, name)).read_bytes())
    assert not rb.verify_release_bundle(changed, inputs.store).ok


def test_trust_adapter_exception_cannot_certify_or_leak(bundle):
    rb, _, sha, inputs = bundle
    manifest = rb.build_release_bundle(sha, inputs).model_copy(
        update={"certification_evidence_id": "runtime-one"})

    class UnavailableTrust:
        def verify(self, **kwargs):
            raise RuntimeError("private-access-token")

    result = rb.check_certification(manifest, inputs.store, UnavailableTrust())
    assert result.certified is False
    assert "private-access-token" not in repr(result)


def test_signed_receipt_rejection_propagates(bundle):
    rb, _, sha, inputs = bundle
    from yleum_recovery.contracts import Failure, Proof

    manifest = rb.build_release_bundle(sha, inputs).model_copy(
        update={"certification_evidence_id": "runtime-one"})

    class RejectingTrust:
        def verify(self, *, evidence_id, manifest_sha256, source_sha):
            assert evidence_id == "runtime-one" and source_sha == sha
            assert len(manifest_sha256) == 64
            return Proof(ok=False, failures=(Failure(
                code="INVALID_SIGNATURE", message="Runtime receipt rejected"),))

    assert rb.check_certification(manifest, inputs.store, RejectingTrust()).certified is False
    with pytest.raises(ValueError):
        rb.publish_certified_index(manifest, inputs.store, RejectingTrust())


def test_github_redirect_cannot_send_credentials_to_untrusted_host(tmp_path):
    _, storage = implementation()
    from pydantic import SecretStr

    seen = []

    def handler(request):
        seen.append(request.url.host)
        if request.url.path.endswith("/assets"):
            return httpx.Response(200, json=[{"id": 9, "name": "one"}])
        return httpx.Response(302, headers={"location": "https://evil.example.test/blob"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        store = storage.GitHubReleaseAssetStore("owner/repo", 7, client=client,
                                               token=SecretStr("synthetic-token"))
        with pytest.raises(ValueError):
            store.fetch_verified("one", tmp_path / "download", digest(b"artifact"))
    assert "evil.example.test" not in seen


def test_public_environment_examples_in_full_source_are_allowed(bundle):
    rb, _, _, inputs = bundle
    public = inputs.repository / "apps/web/.env.local.example"
    public.parent.mkdir(parents=True, exist_ok=True)
    public.write_text("# public configuration example\n")
    subprocess.run(["git", "-C", str(inputs.repository), "add", "."], check=True)
    subprocess.run(["git", "-C", str(inputs.repository), "-c", "user.name=QA",
                    "-c", "user.email=qa@example.test", "commit", "-m", "public example"],
                   check=True, capture_output=True)
    sha = subprocess.check_output(["git", "-C", str(inputs.repository), "rev-parse", "HEAD"],
                                  text=True).strip()
    item = next(item for item in inputs.artifacts if item.component_id == "build-provenance")
    document = json.loads(item.archive.read_text())
    document.update(source_sha=sha, ci_sha=sha)
    item.archive.write_text(json.dumps(document))
    updated = replace(item, archive_sha256=digest(item.archive.read_bytes()))
    inputs = replace(inputs, ci_sha=sha, artifacts=tuple(
        updated if entry.component_id == "build-provenance" else entry
        for entry in inputs.artifacts))
    manifest = rb.build_release_bundle(sha, inputs)
    assert rb.verify_release_bundle(manifest, inputs.store).ok


def test_independent_store_exception_yields_safe_failure(bundle):
    rb, _, sha, inputs = bundle
    manifest = rb.build_release_bundle(sha, inputs)

    class UnavailableStore:
        def fetch_verified(self, key, destination, sha256):
            raise httpx.ConnectError("https://storage.example.test?secret=private")

    result = rb.verify_release_bundle(manifest, UnavailableStore())
    assert not result.ok
    assert "private" not in repr(result)
