#!/usr/bin/env python3
"""Rotate Omnia-owned production secrets with automatic rollback.

External provider credentials (LLM, GitHub OAuth, SMTP, YooKassa, etc.) are
intentionally out of scope: only their provider can revoke and replace them.
This script rotates secrets controlled entirely by the Omnia production host.
"""

from __future__ import annotations

import argparse
import base64
import fcntl
import json
import os
import secrets
import shutil
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

RUNTIME_ROOT = Path("/opt/omnia-runtime")
RUNTIME_ENV = RUNTIME_ROOT / ".env"
# Must match EnvironmentFile in infra/systemd/omnia-orchestrator.service.
# Rotating a detached mirror leaves the running daemon on the old internal
# token and makes every new provision/deploy request fail with HTTP 401.
ORCHESTRATOR_ENV = Path("/opt/omnia/apps/orchestrator/.env")
FULLSTACK_ROOT = Path("/opt/omnia/apps/llm-gateway/deploy/full")
FULLSTACK_ENV = FULLSTACK_ROOT / ".env"
# Phase 2 (core): the platform DB may live on the host PostgreSQL instead of the
# omnia-prod-postgres container. Then the compose .env carries
# PLATFORM_DATABASE_URL, the role is altered on the host and the backup
# credentials file below must follow the password (infra/backup reads it).
PLATFORM_CREDS = Path("/etc/max-studio/platform-postgres.env")
PROJECT_POSTGRES_COMPOSE = RUNTIME_ROOT / "postgres-compose.yml"
API_CONTAINER = "omnia-prod-api"
ROTATE_SCRIPT = "/app/scripts/rotate_encryption_keys.py"


def run(
    args: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    stdin: str | None = None,
    timeout: int = 600,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        env=env,
        input=stdin,
        text=True,
        check=True,
        timeout=timeout,
        capture_output=capture_output,
    )


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def update_env(path: Path, replacements: dict[str, str]) -> None:
    source = path.read_text(encoding="utf-8").splitlines()
    seen: set[str] = set()
    result: list[str] = []
    for raw in source:
        if "=" in raw and not raw.lstrip().startswith("#"):
            key = raw.split("=", 1)[0].strip()
            if key in replacements:
                result.append(f"{key}={replacements[key]}")
                seen.add(key)
                continue
        result.append(raw)
    missing = set(replacements) - seen
    if missing:
        raise RuntimeError(f"missing expected keys in {path}: {sorted(missing)}")

    handle, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.rotate-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as output:
            output.write("\n".join(result) + "\n")
            output.flush()
            os.fsync(output.fileno())
        temporary.chmod(0o600)
        temporary.replace(path)
        path.chmod(0o600)
    finally:
        if temporary.exists():
            temporary.unlink()


def new_fernet_key() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")


def rotate_stored_tokens(
    old: dict[str, str],
    new: dict[str, str],
    *,
    database_url: str | None = None,
) -> None:
    child_env = os.environ.copy()
    child_env.update(
        {
            "OLD_JWT_SECRET": old["JWT_SECRET"],
            "NEW_JWT_SECRET": new["JWT_SECRET"],
            "OLD_SECRETS_ENCRYPTION_KEY": old["SECRETS_ENCRYPTION_KEY"],
            "NEW_SECRETS_ENCRYPTION_KEY": new["SECRETS_ENCRYPTION_KEY"],
        }
    )
    docker_args = [
        "docker",
        "exec",
        "-e",
        "OLD_JWT_SECRET",
        "-e",
        "NEW_JWT_SECRET",
        "-e",
        "OLD_SECRETS_ENCRYPTION_KEY",
        "-e",
        "NEW_SECRETS_ENCRYPTION_KEY",
    ]
    if database_url is not None:
        child_env["DATABASE_URL"] = database_url
        docker_args.extend(["-e", "DATABASE_URL"])
    docker_args.extend(
        [API_CONTAINER, "/app/.venv/bin/python", ROTATE_SCRIPT]
    )
    run(docker_args, env=child_env)


def alter_role(container: str, user: str, password: str) -> None:
    escaped = password.replace("'", "''")
    sql = f'ALTER ROLE "{user}" WITH PASSWORD \'{escaped}\';\n'
    run(
        [
            "docker",
            "exec",
            "-i",
            container,
            "psql",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            user,
            "-d",
            "postgres",
            "-q",
        ],
        stdin=sql,
    )


def platform_on_host(full: dict[str, str]) -> bool:
    return bool(full.get("PLATFORM_DATABASE_URL"))


def url_credentials(url: str) -> tuple[str, str]:
    parts = urllib.parse.urlsplit(url)
    if not parts.username or parts.password is None:
        raise RuntimeError("PLATFORM_DATABASE_URL does not carry a user and a password")
    return urllib.parse.unquote(parts.username), urllib.parse.unquote(parts.password)


def replace_url_password(url: str, old: str, new: str) -> str:
    marker = f":{urllib.parse.quote(old, safe='')}@"
    if marker not in url:
        marker = f":{old}@"
    if marker not in url:
        raise RuntimeError("PLATFORM_DATABASE_URL does not contain the current password")
    return url.replace(marker, f":{urllib.parse.quote(new, safe='')}@", 1)


def alter_host_platform_role(user: str, password: str) -> None:
    escaped = password.replace("'", "''")
    sql = f'ALTER ROLE "{user}" WITH PASSWORD \'{escaped}\';\n'
    run(
        ["sudo", "-u", "postgres", "psql", "-X", "-v", "ON_ERROR_STOP=1", "-d", "postgres", "-q"],
        cwd=Path("/tmp"),
        stdin=sql,
    )


def read_root_file(path: Path) -> str:
    return run(["sudo", "cat", str(path)], capture_output=True).stdout


def write_root_file(path: Path, content: str) -> None:
    run(["sudo", "tee", str(path)], stdin=content, capture_output=True)
    run(["sudo", "chmod", "600", str(path)])


def platform_creds_with_password(content: str, old: str, new: str) -> str:
    lines: list[str] = []
    for raw in content.splitlines():
        if raw.startswith("PLATFORM_PG_PASSWORD="):
            lines.append(f"PLATFORM_PG_PASSWORD={new}")
        elif raw.startswith("PLATFORM_PG_DSN="):
            lines.append("PLATFORM_PG_DSN=" + replace_url_password(raw.split("=", 1)[1], old, new))
        else:
            lines.append(raw)
    return "\n".join(lines) + "\n"


def recreate_services(*, host_db: bool = False) -> None:
    run(
        [
            "docker",
            "compose",
            "-f",
            str(PROJECT_POSTGRES_COMPOSE),
            "up",
            "-d",
            "--force-recreate",
        ],
        cwd=RUNTIME_ROOT,
    )
    run(["sudo", "systemctl", "restart", "omnia-orchestrator.service"])
    # In host-db mode the compose `postgres` service sits in an inactive profile
    # (docker-compose.hostdb.yml) and must not be named, or compose refuses.
    services = ["minio", "minio-init", "gateway", "api", "worker", "web"]
    if not host_db:
        services.insert(0, "postgres")
    run(
        ["docker", "compose", "up", "-d", "--force-recreate", *services],
        cwd=FULLSTACK_ROOT,
        timeout=900,
    )


def wait_for_validation(
    orchestrator_token: str,
    *,
    public_origin: str = "https://yleum.ru",
) -> dict[str, object]:
    last_error = ""
    for _ in range(36):
        try:
            with urllib.request.urlopen(
                f"{public_origin.rstrip('/')}/api/health", timeout=10
            ) as response:
                payload = json.loads(response.read())
            checks = payload.get("checks", {})
            if payload.get("status") == "ok" and all(
                checks.get(name) == "ok"
                for name in (
                    "database",
                    "redis",
                    "worker",
                    "deploy_control_plane",
                    "preview_storage",
                )
            ):
                break
            last_error = json.dumps(payload, sort_keys=True)
        except Exception as exc:
            last_error = type(exc).__name__
        time.sleep(5)
    else:
        raise RuntimeError(f"production readiness failed after rotation: {last_error}")

    # `/api/health` only checks the orchestrator's public health route. Exercise
    # an authenticated route too, otherwise a stale systemd EnvironmentFile can
    # look healthy while every provision/deploy call fails with HTTP 401.
    probe_id = "00000000-0000-4000-8000-000000000099"
    orchestrator_error = ""
    for _ in range(12):
        try:
            request = urllib.request.Request(
                f"http://127.0.0.1:8003/internal/projects/{probe_id}/status",
                headers={"X-Internal-Token": orchestrator_token},
            )
            with urllib.request.urlopen(request, timeout=10) as response:
                payload = json.loads(response.read())
            if payload.get("project_id") == probe_id:
                break
            orchestrator_error = "unexpected authenticated probe response"
        except Exception as exc:
            orchestrator_error = type(exc).__name__
        time.sleep(2)
    else:
        raise RuntimeError(
            "orchestrator authenticated readiness failed after rotation: "
            f"{orchestrator_error}"
        )

    verified = run(
        [
            "docker",
            "exec",
            API_CONTAINER,
            "/app/.venv/bin/python",
            ROTATE_SCRIPT,
            "--verify-only",
        ],
        capture_output=True,
    )
    return {
        "readiness": "ok",
        "orchestrator_authenticated": "ok",
        "stored_tokens": json.loads(verified.stdout or "{}"),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply",
        action="store_true",
        help="required acknowledgement; without it the script performs no mutation",
    )
    args = parser.parse_args()
    if not args.apply:
        raise SystemExit("refusing to rotate without --apply")

    lock_path = RUNTIME_ROOT / "secret-rotation.lock"
    lock_path.touch(mode=0o600, exist_ok=True)
    lock_path.chmod(0o600)
    with lock_path.open("r+", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        backup_dir = RUNTIME_ROOT / f"secret-rotation-rollback-{timestamp}"
        backup_dir.mkdir(mode=0o700)
        paths = (RUNTIME_ENV, ORCHESTRATOR_ENV, FULLSTACK_ENV)
        backups: dict[Path, Path] = {}
        for path in paths:
            destination = backup_dir / path.name
            if destination.exists():
                destination = backup_dir / f"{path.parent.name}-{path.name}"
            shutil.copy2(path, destination)
            destination.chmod(0o600)
            backups[path] = destination

        full = read_env(FULLSTACK_ENV)
        runtime = read_env(RUNTIME_ENV)
        orchestrator = read_env(ORCHESTRATOR_ENV)
        required_full = (
            "JWT_SECRET",
            "NEXTAUTH_SECRET",
            "SECRETS_ENCRYPTION_KEY",
            "ORCHESTRATOR_INTERNAL_TOKEN",
            "POSTGRES_PASSWORD",
            "POSTGRES_USER",
            "POSTGRES_DB",
            "MINIO_ROOT_PASSWORD",
        )
        for key in required_full:
            if not full.get(key):
                raise RuntimeError(f"required production key is missing: {key}")
        if full["ORCHESTRATOR_INTERNAL_TOKEN"] != orchestrator.get("INTERNAL_TOKEN"):
            raise RuntimeError("orchestrator tokens do not match before rotation")
        if not runtime.get("POSTGRES_USERS_PASSWORD"):
            raise RuntimeError("POSTGRES_USERS_PASSWORD is missing")

        host_db = platform_on_host(full)
        if host_db:
            platform_role, platform_password = url_credentials(full["PLATFORM_DATABASE_URL"])
            platform_creds_before = read_root_file(PLATFORM_CREDS)
        else:
            platform_role, platform_password = full["POSTGRES_USER"], full["POSTGRES_PASSWORD"]
            platform_creds_before = ""
        old = {
            "JWT_SECRET": full["JWT_SECRET"],
            "NEXTAUTH_SECRET": full["NEXTAUTH_SECRET"],
            "SECRETS_ENCRYPTION_KEY": full["SECRETS_ENCRYPTION_KEY"],
            "ORCHESTRATOR_INTERNAL_TOKEN": full["ORCHESTRATOR_INTERNAL_TOKEN"],
            "POSTGRES_PASSWORD": platform_password,
            "POSTGRES_USERS_PASSWORD": runtime["POSTGRES_USERS_PASSWORD"],
            "MINIO_ROOT_PASSWORD": full["MINIO_ROOT_PASSWORD"],
        }
        new = {
            "JWT_SECRET": secrets.token_urlsafe(48),
            "NEXTAUTH_SECRET": secrets.token_urlsafe(48),
            "SECRETS_ENCRYPTION_KEY": new_fernet_key(),
            "ORCHESTRATOR_INTERNAL_TOKEN": secrets.token_hex(32),
            "POSTGRES_PASSWORD": secrets.token_urlsafe(36),
            "POSTGRES_USERS_PASSWORD": secrets.token_urlsafe(36),
            "MINIO_ROOT_PASSWORD": secrets.token_urlsafe(36),
        }

        tokens_rotated = False
        platform_role_rotated = False
        users_role_rotated = False
        env_updates_started = False
        try:
            rotate_stored_tokens(old, new)
            tokens_rotated = True

            if host_db:
                alter_host_platform_role(platform_role, new["POSTGRES_PASSWORD"])
            else:
                alter_role("omnia-prod-postgres", platform_role, new["POSTGRES_PASSWORD"])
            platform_role_rotated = True
            alter_role(
                "omnia-postgres-users",
                "omnia_root",
                new["POSTGRES_USERS_PASSWORD"],
            )
            users_role_rotated = True

            old_orchestrator_url = orchestrator["DATABASE_URL"]
            marker = f":{old['POSTGRES_USERS_PASSWORD']}@"
            if marker not in old_orchestrator_url:
                raise RuntimeError("orchestrator DATABASE_URL does not contain current password")
            new_orchestrator_url = old_orchestrator_url.replace(
                marker,
                f":{new['POSTGRES_USERS_PASSWORD']}@",
                1,
            )
            env_updates_started = True
            fullstack_updates = {
                "JWT_SECRET": new["JWT_SECRET"],
                "NEXTAUTH_SECRET": new["NEXTAUTH_SECRET"],
                "SECRETS_ENCRYPTION_KEY": new["SECRETS_ENCRYPTION_KEY"],
                "ORCHESTRATOR_INTERNAL_TOKEN": new["ORCHESTRATOR_INTERNAL_TOKEN"],
                "MINIO_ROOT_PASSWORD": new["MINIO_ROOT_PASSWORD"],
            }
            if host_db:
                # The container role keeps its password (POSTGRES_PASSWORD stays
                # valid for a rollback to the container); the live role is the
                # host one, referenced by the URL and the backup credentials.
                fullstack_updates["PLATFORM_DATABASE_URL"] = replace_url_password(
                    full["PLATFORM_DATABASE_URL"], platform_password, new["POSTGRES_PASSWORD"]
                )
                write_root_file(
                    PLATFORM_CREDS,
                    platform_creds_with_password(
                        platform_creds_before, platform_password, new["POSTGRES_PASSWORD"]
                    ),
                )
            else:
                fullstack_updates["POSTGRES_PASSWORD"] = new["POSTGRES_PASSWORD"]
            update_env(FULLSTACK_ENV, fullstack_updates)
            update_env(
                RUNTIME_ENV,
                {"POSTGRES_USERS_PASSWORD": new["POSTGRES_USERS_PASSWORD"]},
            )
            update_env(
                ORCHESTRATOR_ENV,
                {
                    "INTERNAL_TOKEN": new["ORCHESTRATOR_INTERNAL_TOKEN"],
                    "DATABASE_URL": new_orchestrator_url,
                },
            )
            recreate_services(host_db=host_db)
            evidence = wait_for_validation(
                new["ORCHESTRATOR_INTERNAL_TOKEN"],
                public_origin=full.get("PUBLIC_ORIGIN") or "https://yleum.ru",
            )
        except Exception:
            print("rotation failed; starting automatic rollback", flush=True)
            if tokens_rotated:
                if not platform_role_rotated:
                    new_database_url = None
                elif host_db:
                    new_database_url = replace_url_password(
                        full["PLATFORM_DATABASE_URL"], platform_password, new["POSTGRES_PASSWORD"]
                    )
                else:
                    new_database_url = (
                        f"postgresql+asyncpg://{full['POSTGRES_USER']}:"
                        f"{new['POSTGRES_PASSWORD']}@postgres:5432/{full['POSTGRES_DB']}"
                    )
                rotate_stored_tokens(new, old, database_url=new_database_url)
            if platform_role_rotated:
                if host_db:
                    alter_host_platform_role(platform_role, old["POSTGRES_PASSWORD"])
                    if env_updates_started:
                        write_root_file(PLATFORM_CREDS, platform_creds_before)
                else:
                    alter_role(
                        "omnia-prod-postgres",
                        platform_role,
                        old["POSTGRES_PASSWORD"],
                    )
            if users_role_rotated:
                alter_role(
                    "omnia-postgres-users",
                    "omnia_root",
                    old["POSTGRES_USERS_PASSWORD"],
                )
            if env_updates_started:
                for path in paths:
                    shutil.copy2(backups[path], path)
                    path.chmod(0o600)
            if (
                tokens_rotated
                or platform_role_rotated
                or users_role_rotated
                or env_updates_started
            ):
                recreate_services(host_db=host_db)
            raise
        else:
            shutil.rmtree(backup_dir)
            audit = {
                "status": "ok",
                "rotated_at": datetime.now(UTC).isoformat(),
                "keys": sorted(new),
                "validation": evidence,
            }
            print(json.dumps(audit, sort_keys=True))


if __name__ == "__main__":
    main()
