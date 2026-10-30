"""Active publication supplement, not a collector or full disaster-recovery proof.

collect(cells, DockerRunner, state_root, state_archive, peer_dir, runtime_witness=...)
writes flat PUBLISHED.json and content-addressed blobs only after short canonical
publication captures before/after copying agree. No lock spans heavy I/O.
Existing PG dumps remain authoritative.
validate(peer_dir, state_archive, runtime_witness=...) never talks to Docker.
resolve_witness(cells, runner, state_root, state_archive, runtime_images) binds
external Kube/CRI image facts to current journals and exact historical artifacts.

The externally obtained runtime witness is mandatory for nonempty active sets:
{schema: 1, publications: [{project_id, release_id, epoch, journal_sha256,
 source_image: {artifact_ref}, images: {app/core/guard/redis: {
 reference, config_id, repo_digest, runtime_image_id,
 platform: {os, architecture, variant}}}}]}.
runtime_image_id must resolve to repo_digest, not a Docker config digest. The
caller owns obtaining a trustworthy, release-bound witness; this module cannot
prove freshness of an externally supplied observation. No credentials are read
from Docker inspect Config, printed, or persisted in the sidecar.

Integration must include these files in the SAME encrypted bundle, bind the
sidecar digest in coverage, resolve BOTH runtime DB references, and separately
accept actual app boot/CRUD and any durable PVC gaps. No pull/load/start calls.
"""
import gzip
import hashlib
import json
import os
import re
import stat
import tarfile
import time
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from uuid import UUID

CHUNK = 1024 * 1024
PG = "/var/lib/postgresql/data"
ROLES = {"app", "core", "guard", "redis"}
HEX = re.compile(r"[0-9a-f]{64}")
IMAGE = re.compile(r"sha256:[0-9a-f]{64}")
ARTIFACT = re.compile(r"[0-9a-f]{32}\.tar")


class PublishedError(RuntimeError):
    """Only fixed nonsecret codes cross this module's failure boundary."""


def need(condition, code="published_integrity_failed"):
    if not condition:
        raise PublishedError(code)


def uuid(value):
    need(isinstance(value, str) and str(UUID(value)) == value, "invalid_identity")
    return value


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def canonical_digest(reference):
    """Normalize only Docker Hub official-image aliases, never private repos."""
    need(isinstance(reference, str)
         and re.fullmatch(r"[^\s@]+@sha256:[0-9a-f]{64}", reference), "invalid_manifest_reference")
    repository, checksum = reference.split("@", 1)
    if "/" not in repository:
        repository = "docker.io/library/" + repository
    elif repository.startswith("library/") and repository.count("/") == 1:
        repository = "docker.io/" + repository
    elif repository.startswith("docker.io/") and repository.count("/") == 1:
        repository = "docker.io/library/" + repository.split("/", 1)[1]
    return repository + "@" + checksum


def identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns if os.name == "posix" else None)


@contextmanager
def regular(path, flags=os.O_RDONLY):
    """Open beneath non-symlink directories; Linux walks pinned directory FDs."""
    path = Path(path).absolute()
    opened = []
    fd = None
    try:
        if os.name == "posix":
            parent = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
            opened.append(parent)
            for part in path.parts[1:-1]:
                parent = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                opened.append(parent)
            fd = os.open(path.name, flags | os.O_NOFOLLOW, dir_fd=parent)
        else:  # Unit fixtures on Windows; real collection requires flock below.
            need(all(not part.is_symlink() for part in (path, *path.parents)), "unsafe_source_path")
            fd = os.open(path, flags)
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode), "source_not_regular")
        need(identity(path.lstat()) == identity(before), "source_changed")
        yield fd, before
        need(identity(os.fstat(fd)) == identity(before) == identity(path.lstat()), "source_changed")
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise PublishedError("source_unavailable_or_invalid") from error
    finally:
        if fd is not None:
            os.close(fd)
        for parent in reversed(opened):
            os.close(parent)


def read_bytes(path):
    with regular(path) as (fd, info):
        need(info.st_size <= 16 * CHUNK, "metadata_too_large")
        with os.fdopen(os.dup(fd), "rb") as source:
            return source.read()


def file_digest(path):
    with regular(path) as (fd, info):
        checksum = hashlib.sha256()
        with os.fdopen(os.dup(fd), "rb") as source:
            while chunk := source.read(CHUNK):
                checksum.update(chunk)
        return {"sha256": checksum.hexdigest(), "size": info.st_size}


def verify_sealed(path, *, expected_size=None):
    """Never trust the seal's fast path: hash actual bytes every time."""
    path = Path(path)
    marker = path.with_name(path.name + ".ok")
    try:
        seal_bytes = read_bytes(marker)
        seal = json.loads(seal_bytes)
        before = path.lstat()
        result = file_digest(path)
        need(result["size"] > 0 and HEX.fullmatch(seal.get("sha256", "")), "invalid_seal")
        need(result == {"sha256": seal["sha256"], "size": seal["size"]}, "artifact_checksum_mismatch")
        need(seal["inode"] == before.st_ino and seal["mtime_ns"] == before.st_mtime_ns,
             "stale_seal")
        need(expected_size is None or result["size"] == expected_size, "artifact_size_mismatch")
        need(identity(before) == identity(path.lstat()) and read_bytes(marker) == seal_bytes,
             "source_changed")
        return result
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise PublishedError("invalid_seal") from error


def archive_bytes(archive, name):
    found = [entry for entry in archive.getmembers() if entry.name == name]
    need(len(found) == 1 and found[0].isreg() and found[0].size <= 16 * CHUNK,
         "archive_metadata_missing_or_invalid")
    return archive.extractfile(found[0]).read()


def selected(records):
    active = {}
    for project, payload in records:
        uuid(project)
        need(payload.get("project_id") == project)
        release = payload.get("active_release")
        if not release or payload.get("disabled"):
            continue
        need(not payload.get("activation_pending") and not payload.get("recovery_required"),
             "publication_unstable")
        need(not any(item.get("response", {}).get("phase") in {"queued", "building", "swapping"}
                     for item in payload.get("history", [])), "publication_unstable")
        placement = release.get("placement", {})
        need(placement.get("backend") == "kubernetes" and placement.get("namespace") == "app-" + project,
             "unsupported_active_placement")
        need(project not in active)
        uuid(release.get("release_id"))
        uuid(payload.get("owner_id"))
        uuid(payload.get("production_workspace_id"))
        active[project] = payload
    return active


def archived_records(archive):
    records = []
    for member in archive.getmembers():
        parts = PurePosixPath(member.name).parts
        if len(parts) == 4 and parts[:2] == ("state", "cell-publications") and parts[3] == "publication.json":
            records.append((parts[2], json.loads(archive_bytes(archive, member.name))))
    return selected(records)


def _flock(fd):
    import fcntl
    deadline = time.monotonic() + 60
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            need(time.monotonic() < deadline, "publication_lock_timeout")
            time.sleep(0.1)


def _capture_publications(cells, root):
    """Hold one existing lock only while reading its small journal/request pair."""
    records = selected(cells._publication_records(root))
    snapshot = []
    for project, payload in sorted(records.items()):
        lock = root / "cell-publications" / "locks" / f"workspace-{project}.lock"
        with regular(lock, os.O_RDWR) as (fd, info):
            need(os.name != "posix" or stat.S_IMODE(info.st_mode) == 0o600, "unsafe_lock_mode")
            _flock(fd)
            base = root / "cell-publications" / project
            journal = read_bytes(base / "publication.json")
            need(json.loads(journal) == payload and (base / "publication.json").stat().st_uid == info.st_uid,
                 "publication_changed")
            release = payload["active_release"]["release_id"]
            request = read_bytes(base / "requests" / (release + ".json"))
            snapshot.append((payload, journal, request, identity(info)))
    need(selected(cells._publication_records(root)) == records, "publication_changed")
    return snapshot


@contextmanager
def active_snapshot(cells, state_root, state_archive):
    """Optimistic before/after capture; no publication lock across the yield."""
    root = Path(state_root)
    before = _capture_publications(cells, root)
    # Decompression and archive scans also happen with every flock released.
    with tarfile.open(state_archive, "r:*") as archive:
        need(archived_records(archive) == {p["project_id"]: p for p, _, _, _ in before},
             "archive_publication_mismatch")
        for payload, journal, request, _ in before:
            base = f"state/cell-publications/{payload['project_id']}/"
            need(journal == archive_bytes(archive, base + "publication.json"), "archive_publication_mismatch")
            release = payload["active_release"]["release_id"]
            need(request == archive_bytes(archive, base + f"requests/{release}.json"), "archive_request_mismatch")
    yield [(payload, journal, json.loads(request)) for payload, journal, request, _ in before]
    need(_capture_publications(cells, root) == before, "publication_or_lock_changed")


def verify_image(path, config_id, *, provenance=None, platform=None):
    """Verify docker-save config and all rootfs layers without extracting or load."""
    need(isinstance(config_id, str) and IMAGE.fullmatch(config_id), "invalid_image_identity")
    try:
        with regular(path) as (fd, _), os.fdopen(os.dup(fd), "rb") as source, tarfile.open(fileobj=source) as archive:
            members = {}
            for member in archive.getmembers():
                parts = PurePosixPath(member.name).parts
                need(member.name not in members and not member.name.startswith("/")
                     and ".." not in parts and "\\" not in member.name
                     and (member.isreg() or member.isdir()), "unsafe_image_archive")
                members[member.name] = member
            manifests = json.loads(archive_bytes(archive, "manifest.json"))
            need(isinstance(manifests, list) and len(manifests) == 1, "ambiguous_image_archive")
            manifest = manifests[0]
            config_bytes = archive_bytes(archive, manifest["Config"])
            need("sha256:" + digest(config_bytes) == config_id, "image_config_mismatch")
            config = json.loads(config_bytes)
            if platform:
                need({k: config.get(k) or "" for k in ("os", "architecture", "variant")} == platform,
                     "image_platform_mismatch")
            if provenance:
                baked = config.get("config") or {}
                need(not any(baked.get(key) for key in ("Env", "Entrypoint", "Cmd")), "unsafe_app_config")
                need(all((baked.get("Labels") or {}).get(key) == value for key, value in provenance.items()),
                     "app_provenance_mismatch")
            layers = manifest["Layers"]
            expected = config["rootfs"]["diff_ids"]
            need(len(layers) == len(expected) and len(set(layers)) == len(layers), "image_layers_mismatch")
            for name, checksum in zip(layers, expected):
                member = members.get(name)
                need(member is not None and member.isreg(), "image_layer_missing")
                handle = archive.extractfile(member)
                magic = handle.read(2)
                handle.seek(0)
                stream = gzip.GzipFile(fileobj=handle) if magic == b"\x1f\x8b" else handle
                actual = hashlib.sha256()
                while chunk := stream.read(CHUNK):
                    actual.update(chunk)
                need("sha256:" + actual.hexdigest() == checksum, "image_layer_mismatch")
            return config_id
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError) as error:
        raise PublishedError("image_archive_invalid") from error


def witness_map(runtime_witness, projects):
    if not projects and runtime_witness is None:
        return {}
    need(isinstance(runtime_witness, dict) and runtime_witness.get("schema") == 1,
         "runtime_witness_required")
    records = runtime_witness.get("publications")
    need(isinstance(records, list), "runtime_witness_invalid")
    result = {record["project_id"]: record for record in records}
    need(len(result) == len(records) and set(result) == set(projects), "runtime_witness_scope_mismatch")
    return result


def source_workspace(payload, request):
    project, release = payload["project_id"], payload["active_release"]
    workspace = uuid(request.get("workspace_id"))
    need(all(request.get(key) == expected for key, expected in {
        "project_id": project, "owner_id": payload["owner_id"],
        "snapshot_id": release.get("snapshot_id"), "source_revision": release.get("source_revision")}.items()),
        "request_release_mismatch")
    return workspace


def image_ids(image):
    """Bind the trusted config and manifest identities without conflating them."""
    config_id = image.get("config_id")
    need(isinstance(config_id, str) and IMAGE.fullmatch(config_id), "runtime_witness_image_invalid")
    reference = canonical_digest(image["repo_digest"])
    need(canonical_digest(image["runtime_image_id"].removeprefix("docker-pullable://")) == reference,
         "runtime_manifest_identity_mismatch")
    return {config_id, reference.split("@", 1)[1]}


def plan(payload, journal, request, witness):
    project, release = payload["project_id"], payload["active_release"]
    workspace = source_workspace(payload, request)
    need(all(witness.get(key) == expected for key, expected in {
        "project_id": project, "release_id": release["release_id"], "epoch": release["epoch"],
        "journal_sha256": digest(journal)}.items()), "runtime_witness_release_mismatch")
    image_ref = witness.get("source_image", {}).get("artifact_ref", "")
    need(ARTIFACT.fullmatch(image_ref), "source_image_reference_invalid")
    images = witness.get("images", {})
    need(set(images) == ROLES, "runtime_witness_images_missing")
    for role, value in images.items():
        need(set(value) == {"reference", "config_id", "repo_digest", "runtime_image_id", "platform"},
             "runtime_witness_image_invalid")
        image_ids(value)
        need(value["reference"] and not any(c.isspace() for c in value["reference"]), "invalid_image_reference")
        need(set(value["platform"]) == {"os", "architecture", "variant"}
             and value["platform"]["os"] == "linux" and value["platform"]["architecture"], "invalid_platform")
        if role != "redis":
            need(value["reference"] == release["placement"].get(role + "_image"), "placement_image_mismatch")
    need(release["image_id"] in image_ids(images["app"]), "active_image_mismatch")
    seeds, durable, mounts = [], set(), set()
    for seed in release["placement"].get("seeds", []):
        mount = seed["mount_path"]
        need(isinstance(mount, str) and mount.startswith("/") and ".." not in PurePosixPath(mount).parts
             and mount not in mounts and type(seed.get("durable")) is bool, "invalid_seed_mount")
        mounts.add(mount)
        if mount == PG:
            need(seed["durable"], "postgres_seed_not_durable")
            continue
        if seed["durable"]:
            durable.add(mount)
            continue
        source = str(seed.get("artifact", "")).replace("\\", "/")
        ref = PurePosixPath(source).name
        need(ARTIFACT.fullmatch(ref) and source.endswith(f"/project-machines/artifacts/{workspace}/{ref}")
             and ".." not in PurePosixPath(source).parts and seed["size"] > 0, "invalid_seed_reference")
        seeds.append({"mount_path": mount, "artifact_ref": ref, "size": seed["size"]})
    for service in release.get("manifest", {}).get("services", []):
        for mount in service.get("mounts", []):
            if mount["target"] != PG:
                durable.add(mount["target"])
    return {"project_id": project, "release_id": release["release_id"], "epoch": release["epoch"],
            "journal_sha256": digest(journal), "request_sha256": digest(encoded(request)),
            "source_workspace_id": workspace, "source_image": image_ref, "images": images,
            "seeds": sorted(seeds, key=lambda seed: seed["mount_path"]),
            "databases": [{"project_id": project, "workspace_id": payload["production_workspace_id"], "kind": kind,
                           "source": "existing-runtime-backup"} for kind in ("project", "core")],
            "durable_pvc_gaps": sorted(durable)}


def provenance(payload, workspace):
    return {"omnia.managed": "true", "omnia.project_machine": "true", "omnia.workspace_id": workspace,
            "omnia.project_id": payload["project_id"], "omnia.owner_id": payload["owner_id"],
            "omnia.resource_kind": "environment", "omnia.namespace": "prod"}


def inspect_local(runner, image):
    identities = image_ids(image)
    reference = canonical_digest(image["repo_digest"])
    result = runner.run(["image", "inspect", reference], timeout=60)
    need(result.ok, "local_image_missing")
    try:
        records = json.loads(result.text())
        need(len(records) == 1, "local_image_ambiguous")
        record = records[0]
        need(record["Id"] in identities and reference
             in {canonical_digest(ref) for ref in record.get("RepoDigests", [])},
             "local_image_identity_mismatch")
        need({"os": record.get("Os") or "", "architecture": record.get("Architecture") or "",
              "variant": record.get("Variant") or ""} == image["platform"], "local_image_platform_mismatch")
    except (ValueError, KeyError, TypeError) as error:
        raise PublishedError("local_image_invalid") from error


def _archive_config(path):
    """Cheap candidate discovery only; full image/seal verification follows."""
    with regular(path) as (fd, _), os.fdopen(os.dup(fd), "rb") as source:
        try:
            with tarfile.open(fileobj=source) as archive:
                entries = [m for m in archive.getmembers() if m.name == "manifest.json"]
                if not entries:
                    return None  # Captured volume, not a Docker-save image.
                manifests = json.loads(archive_bytes(archive, "manifest.json"))
                if not isinstance(manifests, list) or len(manifests) != 1:
                    return None
                return "sha256:" + digest(archive_bytes(archive, manifests[0]["Config"]))
        except (ValueError, KeyError, TypeError, tarfile.TarError, PublishedError):
            # Non-image volume archives may have unrelated manifest.json files.
            # This cannot select an image: no exact candidate means failure.
            return None


def _resolve_source_image(root, payload, request, image):
    workspace = source_workspace(payload, request)
    store = root / "project-machines" / "artifacts" / workspace
    need(store.is_dir() and not store.is_symlink(), "source_image_store_missing")
    config_id = image["config_id"]
    matches = {}
    # Search only the exact request workspace. Latest docker.json may describe
    # a different revision; no editor environment or retained history is trusted.
    for path in sorted(store.glob("*.tar")):
        need(ARTIFACT.fullmatch(path.name), "invalid_source_artifact_name")
        if _archive_config(path) != config_id:
            continue
        checksum = verify_sealed(path)
        verify_image(path, config_id, provenance=provenance(payload, workspace), platform=image["platform"])
        matches.setdefault(checksum["sha256"], path.name)
    need(matches, "source_image_missing")
    need(len(matches) == 1, "source_image_ambiguous")
    return next(iter(matches.values()))


def _runtime_facts(runtime_images, projects):
    """Require four primary images; the stateless relay must reuse the guard."""
    need(isinstance(runtime_images, list), "runtime_witness_required")
    records = {project: {} for project in projects}
    auxiliaries = []
    for fact in runtime_images:
        project = fact["project_id"]
        if project not in records:
            continue  # Each peer receives the complete census, owns its subset.
        role = "guard" if fact["component"] == "boundary" else fact["component"]
        need(role in ROLES | {"analytics-relay"} and fact.get("ready", True) is True,
             "runtime_component_invalid")
        resolved = canonical_digest(fact["runtime_image_id"].removeprefix("docker-pullable://"))
        need(isinstance(fact["repo_digests"], list)
             and resolved in {canonical_digest(ref) for ref in fact["repo_digests"]},
             "cri_manifest_identity_mismatch")
        value = {"reference": fact["reference"], "config_id": fact["config_id"],
                 "repo_digest": resolved, "runtime_image_id": fact["runtime_image_id"],
                 "platform": {key: fact["platform"].get(key) or "" for key in ("os", "architecture", "variant")}}
        if role == "analytics-relay":
            auxiliaries.append((project, value))
        else:
            need(role not in records[project] or records[project][role] == value,
                 "runtime_replicas_disagree")
            records[project][role] = value
    need(all(set(images) == ROLES for images in records.values()), "runtime_witness_images_missing")
    need(all(value == records[project]["guard"] for project, value in auxiliaries),
         "runtime_auxiliary_image_mismatch")
    return records


def resolve_witness(cells, runner, state_root, state_archive, runtime_images):
    """Resolve nightly active closures without hardcoded project/archive mappings.

    runtime_images is a list of externally observed facts: project_id,
    component (app/core/guard/redis; boundary aliases guard; analytics-relay
    reuses the exact guard image), reference,
    runtime_image_id, config_id, repo_digests, platform. Primary filters PG and
    initcontainers and provides actual CRI evidence. No Config.Env is read.
    This helper emits no files and uses only local Docker image inspect.
    """
    root = Path(state_root)
    try:
        with active_snapshot(cells, root, state_archive) as snapshot:
            facts = _runtime_facts(runtime_images, [p["project_id"] for p, _, _ in snapshot])
            publications = []
            for payload, journal, request in snapshot:
                project, release = payload["project_id"], payload["active_release"]
                app = facts[project]["app"]
                need(release["image_id"] in image_ids(app), "active_image_mismatch")
                ref = _resolve_source_image(root, payload, request, app)
                witness = {"project_id": project, "release_id": release["release_id"], "epoch": release["epoch"],
                           "journal_sha256": digest(journal), "source_image": {"artifact_ref": ref},
                           "images": facts[project]}
                plan(payload, journal, request, witness)
                for image in witness["images"].values():
                    inspect_local(runner, image)
                publications.append(witness)
        return {"schema": 1, "publications": publications}
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError) as error:
        raise PublishedError("published_witness_resolution_failed") from error


def copy_blob(source, out, expected):
    name = "published-" + expected["sha256"] + ".tar"
    destination = out / name
    if destination.exists():
        need(file_digest(destination) == expected, "existing_blob_mismatch")
        return name
    with regular(source) as (fd, _), os.fdopen(os.dup(fd), "rb") as reader:
        sink_fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(sink_fd, "wb") as sink:
            while chunk := reader.read(CHUNK):
                sink.write(chunk)
            sink.flush()
            os.fsync(sink.fileno())
    need(file_digest(destination) == expected, "copied_blob_mismatch")
    return name


def collect(cells, runner, state_root, state_archive, out_dir, *, runtime_witness=None):
    """Only local inspect/save commands; any failure leaves no success sidecar."""
    out, root = Path(out_dir), Path(state_root)
    need(out.is_dir() and not out.is_symlink() and not (out / "PUBLISHED.json").exists(), "unsafe_output")
    need(not any(out.glob("published-*")), "published_output_exists")
    blobs, records, sources, exported = {}, [], {}, {}
    try:
        with active_snapshot(cells, root, state_archive) as snapshot:
            witnesses = witness_map(runtime_witness, [p["project_id"] for p, _, _ in snapshot])
            for payload, journal, request in snapshot:
                record = plan(payload, journal, request, witnesses[payload["project_id"]])
                store = root / "project-machines" / "artifacts" / record["source_workspace_id"]
                for seed in payload["active_release"]["placement"].get("seeds", []):
                    if not seed["durable"]:
                        source_path = Path(seed["artifact"])
                        need(source_path == store / source_path.name, "seed_source_root_mismatch")
                record["artifacts"] = {}
                app = record["images"]["app"]
                paths = [(record["source_image"], None)] + [(s["artifact_ref"], s["size"]) for s in record["seeds"]]
                for ref, size in paths:
                    path = store / ref
                    checksum = verify_sealed(path, expected_size=size)
                    sources[path] = checksum
                    if ref == record["source_image"]:
                        verify_image(path, app["config_id"], provenance=provenance(payload, record["source_workspace_id"]),
                                     platform=app["platform"])
                    name = copy_blob(path, out, checksum)
                    blobs[name] = checksum
                    record["artifacts"][ref] = name
                exported[app["config_id"]] = record["artifacts"][record["source_image"]]
                record["image_blobs"] = {}
                for role, image in sorted(record["images"].items()):
                    inspect_local(runner, image)
                    config_id = image["config_id"]
                    if config_id not in exported:
                        temporary = out / ("published-export-" + config_id[7:] + ".tar")
                        result = runner.run_to_file(["image", "save", canonical_digest(image["repo_digest"])],
                                                    path=temporary, timeout=1800)
                        need(result.ok, "local_image_export_failed")
                        os.chmod(temporary, 0o600)
                        verify_image(temporary, config_id, platform=image["platform"])
                        checksum = file_digest(temporary)
                        name = "published-" + checksum["sha256"] + ".tar"
                        need(not (out / name).exists(), "image_blob_collision")
                        temporary.rename(out / name)
                        blobs[name], exported[config_id] = checksum, name
                    record["image_blobs"][role] = exported[config_id]
                records.append(record)
            for source, expected in sources.items():
                need(verify_sealed(source) == expected, "source_changed")
            for record in records:
                for image in record["images"].values():
                    inspect_local(runner, image)
        result = {"schema": 1, "scope": "active-published-artifacts-and-local-images", "full_dr_verified": False,
                  "runtime_witness_sha256": digest(encoded(runtime_witness)) if runtime_witness else None,
                  "publications": records, "blobs": blobs}
        fd = os.open(out / "PUBLISHED.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded(result))
            handle.flush()
            os.fsync(handle.fileno())
        return result
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError) as error:
        raise PublishedError("published_collection_failed") from error


def validate(directory, state_archive, *, runtime_witness=None):
    """Closed active set, journal/request binding, every artifact and image byte."""
    directory = Path(directory)
    try:
        result = json.loads(read_bytes(directory / "PUBLISHED.json"))
        need(result["schema"] == 1 and result["full_dr_verified"] is False
             and result["scope"] == "active-published-artifacts-and-local-images")
        need(result["runtime_witness_sha256"] == (digest(encoded(runtime_witness)) if runtime_witness else None),
             "runtime_witness_mismatch")
        blobs = result["blobs"]
        need(set(blobs) == {p.name for p in directory.glob("published-*")}, "blob_inventory_mismatch")
        for name, expected in blobs.items():
            need(name == "published-" + expected["sha256"] + ".tar" and HEX.fullmatch(expected["sha256"]),
                 "invalid_blob_name")
            need(file_digest(directory / name) == expected, "blob_checksum_mismatch")
        with tarfile.open(state_archive, "r:*") as archive:
            active = archived_records(archive)
            witnesses = witness_map(runtime_witness, active)
            records = {p["project_id"]: p for p in result["publications"]}
            need(len(records) == len(result["publications"]) and set(records) == set(active), "active_closure_mismatch")
            used, verified_images = set(), set()
            for project, payload in active.items():
                base = f"state/cell-publications/{project}/"
                journal = archive_bytes(archive, base + "publication.json")
                release = payload["active_release"]["release_id"]
                request = json.loads(archive_bytes(archive, base + f"requests/{release}.json"))
                expected = plan(payload, journal, request, witnesses[project])
                record = records[project]
                need({k: v for k, v in record.items() if k not in {"artifacts", "image_blobs"}} == expected,
                     "publication_plan_mismatch")
                need(set(record["artifacts"]) == {record["source_image"], *(s["artifact_ref"] for s in record["seeds"])},
                     "artifact_scope_mismatch")
                need(set(record["image_blobs"]) == ROLES, "image_scope_mismatch")
                for ref, name in record["artifacts"].items():
                    need(name in blobs, "artifact_blob_missing")
                    used.add(name)
                    for seed in record["seeds"]:
                        if seed["artifact_ref"] == ref:
                            need(blobs[name]["size"] == seed["size"], "seed_size_mismatch")
                need(record["image_blobs"]["app"] == record["artifacts"][record["source_image"]], "app_blob_mismatch")
                for role, image in record["images"].items():
                    name = record["image_blobs"][role]
                    need(name in blobs, "image_blob_missing")
                    used.add(name)
                    expected_provenance = provenance(payload, record["source_workspace_id"]) if role == "app" else None
                    key = (name, image["config_id"], encoded(image["platform"]), encoded(expected_provenance))
                    if key not in verified_images:
                        verify_image(directory / name, image["config_id"], platform=image["platform"],
                                     provenance=expected_provenance)
                        verified_images.add(key)
            need(used == set(blobs), "unreferenced_blob")
        return result
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError) as error:
        raise PublishedError("published_validation_failed") from error
