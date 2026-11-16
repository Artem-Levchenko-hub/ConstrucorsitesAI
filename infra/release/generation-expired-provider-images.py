#!/usr/bin/env python3
"""Prepare exact images and run the selected expired-reservation maintenance."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from uuid import UUID

CHECKOUT = Path("/opt/omnia")
COMPOSE = Path("apps/llm-gateway/deploy/full")
HEAD = "0107_expired_provider_reservation"
PROJECT = "full"
ROLES = {"api": "yleum-prod-api", "gateway": "yleum-prod-gw"}
MAX_SCOPE = 8192
IMAGE_REVISION = "org.opencontainers.image.revision"
IMAGE_ROLE = "ru.yleum.release.role"
AUTHORIZATION_DIR = Path("/var/lib/yleum-expired-provider")


class ReleaseDenied(ValueError):
    pass


def require(condition: object) -> None:
    if not condition:
        raise ReleaseDenied("expired reservation launch is unconfirmed")


def unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for name, value in pairs:
        require(name not in result)
        result[name] = value
    return result


def decoded(raw: bytes, *, maximum: int = MAX_SCOPE) -> dict:
    require(0 < len(raw) <= maximum)
    value = json.loads(raw, object_pairs_hook=unique_object)
    require(type(value) is dict)
    return value


def require_revision(value: object) -> None:
    require(type(value) is str and re.fullmatch(r"[0-9a-f]{40}", value) and int(value, 16))


def require_uuid(value: object) -> None:
    require(type(value) is str and str(UUID(value)) == value and UUID(value).int)


def scope_pair(scope: dict, release: str, incumbent: str) -> tuple[dict, dict]:
    require_revision(release)
    require_revision(incumbent)
    require(release != incumbent)
    require(set(scope) == {*ROLES, "gateway_runtime"})
    runtime = scope["gateway_runtime"]
    require(
        type(runtime) is dict and set(runtime) == {"container_id", "image_id"}
        and type(runtime["container_id"]) is str and re.fullmatch(r"[0-9a-f]{64}", runtime["container_id"])
        and type(runtime["image_id"]) is str and re.fullmatch(r"sha256:[0-9a-f]{64}", runtime["image_id"])
    )
    api, gateway = scope["api"], scope["gateway"]
    common = {"version", "release_sha", "reader_release_sha", "state_revision", "source_sha256"}
    require(
        type(api) is dict
        and set(api) == common | {"run_id", "attempt_id", "context_sha256", "clock_sha256"}
        and type(gateway) is dict
        and set(gateway) == common | {"action", "identity"}
        and type(api["version"]) is int and type(gateway["version"]) is int
        and api["version"] == gateway["version"] == 1
        and api["release_sha"] == gateway["release_sha"] == release
        and api["reader_release_sha"] == gateway["reader_release_sha"] == incumbent
        and type(api["state_revision"]) is int and type(gateway["state_revision"]) is int
        and api["state_revision"] == gateway["state_revision"] and api["state_revision"] > 0
        and api["source_sha256"] == gateway["source_sha256"]
        and gateway["action"] == "close_expired_before_dispatch"
    )
    for name in ("source_sha256", "context_sha256", "clock_sha256"):
        require(type(api[name]) is str and re.fullmatch(r"[0-9a-f]{64}", api[name]))
    identity = gateway["identity"]
    require(
        type(identity) is dict
        and set(identity) == {"version", "run_id", "attempt_id", "user_id", "project_id",
                              "prompt_hash", "request_sha256"}
        and type(identity["version"]) is int and identity["version"] == 1
        and identity["run_id"] == api["run_id"]
        and identity["attempt_id"] == api["attempt_id"]
    )
    for name in ("run_id", "attempt_id", "user_id", "project_id"):
        require_uuid(identity[name])
    for name in ("prompt_hash", "request_sha256"):
        require(type(identity[name]) is str and re.fullmatch(r"[0-9a-f]{64}", identity[name]))
    return api, gateway


def command(arguments: list[str], *, stdin: bytes | None = None, env: dict | None = None) -> bytes:
    result = subprocess.run(arguments, input=stdin, capture_output=True, env=env, check=False)
    require(result.returncode == 0)
    return result.stdout


def live_roles(incumbent: str, gateway_runtime: dict) -> tuple[str, str]:
    rows = json.loads(command(["docker", "inspect", *ROLES.values()]))
    require(type(rows) is list and len(rows) == len(ROLES))
    projects, databases = set(), set()
    for (role, name), row in zip(ROLES.items(), rows, strict=True):
        config = row["Config"]
        labels = config["Labels"]
        require(
            row["Name"] == "/" + name and row["State"]["Running"] is True
            and row["State"].get("Health", {}).get("Status") == "healthy"
            and labels["com.docker.compose.service"] == role
        )
        projects.add(labels["com.docker.compose.project"])
        env = dict(item.split("=", 1) for item in config["Env"])
        if role == "api":
            require(env.get("OMNIA_RELEASE_SHA") == incumbent)
        else:
            require_gateway_writer(row, gateway_runtime)
        databases.add(env.get("DATABASE_URL"))
    require(len(projects) == len(databases) == 1)
    project, database = projects.pop(), databases.pop()
    require(project == PROJECT)
    require(type(database) is str and database.startswith(("postgresql://", "postgresql+asyncpg://")))
    return project, database


def require_gateway_writer(row: dict, gateway_runtime: dict) -> None:
    container, image = gateway_runtime["container_id"], gateway_runtime["image_id"]
    require(
        row["Id"] == container and row["Image"] == image
        and row["Config"]["Image"] == "omnia-gateway:prod"
        and command(["docker", "image", "inspect", "omnia-gateway:prod", "--format", "{{.Id}}"])
        .decode().strip() == image
    )
    active = command(["docker", "ps", "--no-trunc", "--format", "{{.ID}}"]).decode().splitlines()
    require(container in active)
    others = [candidate for candidate in active if candidate != container]
    metadata = '{{json .Name}}\t{{json .Image}}\t{{json .Config.Image}}\t' + (
        '{{json (index .Config.Labels "com.docker.compose.service")}}'
    )
    if others:
        output = command(["docker", "inspect", "--format", metadata, *others]).decode().splitlines()
        require(len(output) == len(others))
        for each in output:
            name, image_id, image_ref, service = (json.loads(field) for field in each.split("\t"))
            require(
                name not in {"/yleum-prod-gw", "/omnia-prod-gw"} and image_id != image
                and not image_ref.startswith("omnia-gateway:") and service != "gateway"
            )


def compose_command(root: Path, project: str, release: str) -> tuple[list[str], dict, dict]:
    options = image_names(release)
    override = root / "release-images.json"
    override.write_text(json.dumps({"services": {
        role: {"image": image, "build": {"labels": {IMAGE_REVISION: release, IMAGE_ROLE: role}}}
        for role, image in options.items()
    }}))
    compose_dir = root / COMPOSE
    args = [
        "docker", "compose", "--project-name", project,
        "--env-file", str(CHECKOUT / COMPOSE / ".env"),
        "-f", str(compose_dir / "docker-compose.yml"),
        "-f", str(compose_dir / "docker-compose.hostdb.yml"),
        "-f", str(override),
    ]
    env = {**os.environ, "API_IMAGE": options["api"], "OMNIA_RELEASE_SHA": release}
    return args, env, options


def image_names(release: str) -> dict[str, str]:
    return {"api": "omnia-api:" + release, "gateway": "omnia-gateway:" + release}


def images_ready(images: dict, release: str) -> bool:
    result = subprocess.run(
        ["docker", "image", "inspect", *images.values()], capture_output=True, check=False,
    )
    if result.returncode != 0:
        return False
    rows = json.loads(result.stdout)
    if type(rows) is not list or len(rows) != len(images):
        return False
    for (role, name), row in zip(images.items(), rows, strict=True):
        labels = row["Config"]["Labels"] or {}
        if name not in (row.get("RepoTags") or ()) or (
            labels.get(IMAGE_REVISION), labels.get(IMAGE_ROLE),
        ) != (release, role):
            return False
    return True


def require_database(args: list[str], env: dict, images: dict, database: str) -> None:
    config = decoded(command([*args, "config", "--format", "json"], env=env), maximum=2 << 20)
    for role, image in images.items():
        entry = config["services"][role]
        require(entry["image"] == image and entry["environment"]["DATABASE_URL"] == database)


def invoke(
    args: list[str], env: dict, *, role: str, module: str, scope: dict, name: str | None = None,
) -> dict:
    run = [*args, "run", "--build=false", "--no-deps", "--pull", "never", "-T", "--rm"]
    if name is not None:
        run += ["--name", name]
    response = command(
        [*run, "-e", "OMNIA_RELEASE_SHA", "--entrypoint", "/app/.venv/bin/python", role, "-m", module],
        stdin=json.dumps(scope, sort_keys=True, separators=(",", ":")).encode(),
        env=env,
    )
    return decoded(response)


def run_migration(
    root: Path, args: list[str], env: dict, images: dict, database: str,
    api: dict, release: str, incumbent: str,
) -> dict:
    label = "yleum-expired-provider-migration:" + release
    image_id = command(["docker", "image", "inspect", images["api"], "--format", "{{.Id}}"]).decode().strip()
    require(re.fullmatch(r"sha256:[0-9a-f]{64}", image_id))
    command(["docker", "tag", image_id, label])
    require(
        command(["docker", "image", "inspect", label, "--format", "{{.Id}}"]).decode().strip() == image_id
    )
    # The one-off only runs the migration command. Its dedicated alias keeps
    # this bounded maintenance process outside the census of serving readers.
    override = root / "release-migration.json"
    override.write_text(json.dumps({"services": {"api": {"image": label}}}))
    migration_args = [*args, "-f", str(override)]
    require_database(migration_args, env, {**images, "api": label}, database)
    migration = invoke(
        migration_args, env, role="api", module="yleum_api.ops.expired_provider_migration",
        scope=api, name="yleum-release-expired-provider-" + release[:12],
    )
    require(
        set(migration) == {"ok", "head", "action", "settled", "release_sha", "reader_release_sha"}
        and migration["ok"] is True and migration["head"] == HEAD
        and migration["action"] in {"upgraded", "already_current"}
        and type(migration["settled"]) is bool
        and migration["release_sha"] == release and migration["reader_release_sha"] == incumbent
    )
    return {"ok": True, "mode": "migrate", "settled": migration["settled"]}


def seal(args: list[str], env: dict, gateway: dict) -> dict:
    result = invoke(args, env, role="gateway", module="yleum_gateway.ops.expired_provider_reservation",
                    scope=gateway)
    require(
        set(result) == {"result", "receipt_sha256"} and result["result"] == "settled"
        and type(result["receipt_sha256"]) is str and re.fullmatch(r"[0-9a-f]{64}", result["receipt_sha256"])
    )
    return {"ok": True, "mode": "seal", **result}


def scope_description(scope: dict, release: str) -> tuple[str, str, str]:
    api_scope = scope.get("api")
    require(type(api_scope) is dict)
    incumbent = api_scope.get("reader_release_sha")
    api, _ = scope_pair(scope, release, incumbent)
    canonical = json.dumps(scope, sort_keys=True, separators=(",", ":")).encode()
    return api["run_id"], incumbent, hashlib.sha256(canonical).hexdigest()


def authorize_reattach(api: dict, release: str, *, check: bool = False) -> None:
    name = "yleum-prod-generation-worker"
    rows = json.loads(command(["docker", "inspect", name]))
    require(type(rows) is list and len(rows) == 1)
    worker = rows[0]
    config = worker["Config"]
    image = image_names(release)["api"]
    require(
        worker["Name"] == "/" + name and worker["State"]["Running"] is True
        and config["Image"] == image
        and config["Labels"].get("com.docker.compose.project") == PROJECT
        and config["Labels"].get("com.docker.compose.service") == "generation-worker"
        and "OMNIA_RELEASE_SHA=" + release in config["Env"]
        and config["User"] in {"", "0", "0:0", "root", "root:root"}
        and worker["Image"]
        == command(["docker", "image", "inspect", image, "--format", "{{.Id}}"]).decode().strip()
    )
    require(any(
        mount["Type"] == "bind" and mount["Source"] == str(AUTHORIZATION_DIR)
        and mount["Destination"] == str(AUTHORIZATION_DIR) and mount["RW"] is False
        for mount in worker["Mounts"]
    ))
    directory = AUTHORIZATION_DIR.lstat()
    require(
        os.geteuid() == 0 and stat.S_ISDIR(directory.st_mode)
        and directory.st_uid == 0 and stat.S_IMODE(directory.st_mode) & 0o022 == 0
    )
    scope = {key: value for key, value in api.items() if key not in {
        "release_sha", "reader_release_sha",
    }}
    authorization = json.dumps({
        "release_sha": release, "scope": json.dumps(scope, separators=(",", ":"), sort_keys=True),
    }, separators=(",", ":")).encode()
    destination = AUTHORIZATION_DIR / "scope.json"
    if check:
        info = destination.lstat()
        require(
            stat.S_ISREG(info.st_mode) and info.st_uid == 0
            and stat.S_IMODE(info.st_mode) == 0o600 and info.st_size == len(authorization)
            and destination.read_bytes() == authorization
        )
        return
    descriptor, temporary = tempfile.mkstemp(dir=AUTHORIZATION_DIR, prefix=".scope-")
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(authorization)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        directory_fd = os.open(AUTHORIZATION_DIR, os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        Path(temporary).unlink(missing_ok=True)


def main() -> dict:
    require(len(sys.argv) == 3)
    mode, release = sys.argv[1:]
    require(mode in {
        "scope", "stage", "migrate", "seal", "verify-images", "authorize-reattach", "check-reattach",
    })
    require_revision(release)
    scope = decoded(sys.stdin.buffer.read(MAX_SCOPE + 1))
    run_id, incumbent, digest = scope_description(scope, release)
    if mode == "scope":
        return {"run_id": run_id, "reader_release_sha": incumbent, "scope_sha256": digest}
    lock = CHECKOUT / ".deploy.lock"
    require(lock.is_file() and not lock.is_symlink() and lock.read_text().split()[-1] == release)
    if mode == "verify-images":
        require(images_ready(image_names(release), release))
        return {"ok": True, "mode": mode}
    api, gateway = scope_pair(scope, release, incumbent)
    if mode in {"authorize-reattach", "check-reattach"}:
        authorize_reattach(api, release, check=mode == "check-reattach")
        return {"ok": True, "mode": mode}
    project, database = live_roles(incumbent, scope["gateway_runtime"])
    command(["git", "-C", str(CHECKOUT), "merge-base", "--is-ancestor", release, "origin/main"])
    with tempfile.TemporaryDirectory(prefix="yleum-expired-provider-") as directory:
        root = Path(directory)
        archive = root / "release.tar"
        command(["git", "-C", str(CHECKOUT), "archive", "--format=tar", "-o", str(archive), release])
        command(["tar", "-xf", str(archive), "-C", str(root)])
        args, env, images = compose_command(root, project, release)
        require_database(args, env, images, database)
        if mode == "stage":
            if not images_ready(images, release):
                with tempfile.NamedTemporaryFile(prefix="yleum-expired-provider-build-", suffix=".log",
                                                 delete=False) as output:
                    result = subprocess.run(
                        [*args, "build", *ROLES], env=env, stdout=output, stderr=output, check=False,
                    )
                    if result.returncode != 0:
                        print(f"exact image build failed; core log: {output.name}", file=sys.stderr)
                    require(result.returncode == 0)
                Path(output.name).unlink()
            require(images_ready(images, release))
            return {"ok": True, "mode": "stage"}
        require(images_ready(images, release))
        if mode == "migrate":
            return run_migration(root, args, env, images, database, api, release, incumbent)
        return seal(args, env, gateway)


if __name__ == "__main__":
    try:
        print(json.dumps(main(), sort_keys=True))
    except Exception as error:
        print(json.dumps({"ok": False, "error_class": type(error).__name__}))
        raise SystemExit(75) from None
