"""Internal API for project runtime lifecycle.

All endpoints below are gated by `X-Internal-Token` header verified against
`Settings.internal_token`. They are meant for apps/api (the public-facing
FastAPI service) to call; web clients never touch this surface.

These handlers are fully implemented and live in production: provision/wake/
stop/status, hot-reload, compile + runtime status, and a real prod deploy
(build image → run durable container → health-poll → nginx vhost + TLS). The
contracts (request/response schemas) are stable and consumed by apps/api today.
"""

from __future__ import annotations

import asyncio
import errno
import json
import os
import posixpath
import re
from base64 import urlsafe_b64encode
from datetime import timedelta
from hashlib import sha256
from hmac import new as hmac_new
from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Header

from yleum_orchestrator.core import postgres_admin
from yleum_orchestrator.core.config import get_settings
from yleum_orchestrator.core.docker_client import (
    container_image_template,
    destroy_container,
    destroy_project_network,
    exec_cmd,
    find_project_container,
    write_files,
)
from yleum_orchestrator.core.docker_client import (
    container_status as docker_container_status,
)
from yleum_orchestrator.core.env import rebrand_env
from yleum_orchestrator.core.errors import OrchestratorError
from yleum_orchestrator.core.internal_auth import (
    verify_internal_token as _verify_token,
)
from yleum_orchestrator.schemas.runtime import (
    DeployResponse,
    HotReloadRequest,
    KeepAliveRequest,
    KeepAliveResponse,
    StatusResponse,
)
from yleum_orchestrator.services import (
    demo_seed_writer,
    nginx_writer,
)
from yleum_orchestrator.services.hibernate import (
    is_keep_alive_enabled,
    record_activity,
    set_keep_alive,
)
from yleum_orchestrator.services.port_allocator import (
    get_port_allocator,
    get_prod_port_allocator,
)

router = APIRouter(prefix="/internal/projects", tags=["runtime"])

# Fixed template files (globals.css, the component kit, layout) are baked into
# the container image and never committed to the project git repo. The direct
# style-patch endpoint needs the current globals.css to append its managed
# override block, so we expose a narrow, read-only door — strictly whitelisted.
_READABLE_FILES = frozenset({"src/app/globals.css"})

# Agentic builder (Phase 0) caps — bound each observation so one fat result
# can't blow the agent's context window.
_AGENT_MAX_READ = 1_000_000
_AGENT_MAX_GREP = 16_000
_AGENT_MAX_BUILD = 24_000
_SANDBOX_SYNC_MAX_FILES = 5_000
_SANDBOX_SYNC_MAX_FILE_BYTES = 2 * 1024 * 1024
_SANDBOX_SYNC_MAX_TOTAL_BYTES = 64 * 1024 * 1024
_SANDBOX_STALE_AFTER_SECONDS = 60 * 60
_SANDBOX_SKIP_NAMES = frozenset(
    {"node_modules", ".next", ".git", "__pycache__", "dist", "build", ".venv", "vendor"}
)
_PROJECT_WORKSPACE_LOCKS: dict[str, asyncio.Lock] = {}
_DIRECTORY_FSYNC_PLATFORM = os.name

_MAX_PREVIEW_TEMPLATE = "max-miniapp-nextjs"
_MAX_PREVIEW_BOOTSTRAP_TTL = timedelta(seconds=120)
_MAX_PREVIEW_BOOTSTRAP_PATH = "/api/omnia/preview-session"
_MAX_LEDGER_PREFIX = "__OMNIA_MIGRATION_LEDGER__"
_MAX_LEDGER_QUERY = r"""
import pg from "pg";
const names = JSON.parse(process.argv[1]);
const pool = new pg.Pool({
  connectionString: process.env.DATABASE_URL,
  max: 1,
  connectionTimeoutMillis: 15000,
});
try {
  const client = await pool.connect();
  try {
    const lock = await client.query(
      "SELECT pg_try_advisory_lock(hashtext('omnia:max:migrations'), " +
      "hashtext(current_schema())) AS acquired",
    );
    if (!lock.rows[0]?.acquired) {
      console.log("__OMNIA_MIGRATION_LEDGER__" + JSON.stringify({
        lock_acquired: false,
        applied: [],
      }));
    } else {
      try {
        const present = await client.query(
          "SELECT to_regclass('__omnia_migrations') AS ledger",
        );
        let applied = [];
        if (present.rows[0]?.ledger) {
          const result = await client.query(
            "SELECT name FROM __omnia_migrations " +
            "WHERE name = ANY($1::text[]) ORDER BY name",
            [names],
          );
          applied = result.rows.map((row) => row.name);
        }
        console.log("__OMNIA_MIGRATION_LEDGER__" + JSON.stringify({
          lock_acquired: true,
          applied,
        }));
      } finally {
        await client.query(
          "SELECT pg_advisory_unlock(hashtext('omnia:max:migrations'), " +
          "hashtext(current_schema()))",
        );
      }
    }
  } finally {
    client.release();
  }
} finally {
  await pool.end();
}
""".strip()


def _max_preview_bootstrap_message(project_id: str, expires: int) -> bytes:
    """Canonical, domain-separated signing input shared with the template."""
    return f"omnia:max-preview-session:v1\n{project_id}\n{expires}".encode("ascii")


def _max_preview_bootstrap_signature(secret: str, project_id: str, expires: int) -> str:
    digest = hmac_new(
        secret.encode("utf-8"),
        _max_preview_bootstrap_message(project_id, expires),
        sha256,
    ).digest()
    return urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _safe_app_path(path: str) -> str:
    """Validate an agent-supplied path stays inside /app and return it relative.

    Rejects absolute paths, ``~``, NUL, and any ``..`` segment (traversal). The
    container already runs non-root + cap-dropped; this is defense-in-depth so a
    tool call can never escape the project tree.
    """
    p = (path or "").strip().replace("\\", "/")
    if not p or p.startswith("/") or p.startswith("~") or "\x00" in p or ".." in p.split("/"):
        raise OrchestratorError(
            code="validation_failed",
            message=f"unsafe path: {path!r}",
            status_code=403,
        )
    normalized = posixpath.normpath(p)
    if normalized in {"", "."} or normalized.startswith("../"):
        raise OrchestratorError(
            code="validation_failed",
            message=f"unsafe path: {path!r}",
            status_code=403,
        )
    return normalized


def _canonical_hot_reload_payload(payload: HotReloadRequest) -> HotReloadRequest:
    files: dict[str, str] = {}
    raw_aliases: dict[str, str] = {}
    for raw_path, content in payload.files.items():
        path = _safe_app_path(raw_path)
        previous = raw_aliases.get(path)
        if previous is not None and previous != raw_path:
            raise OrchestratorError(
                code="validation_failed",
                message=f"conflicting path aliases: {previous!r} and {raw_path!r}",
                status_code=409,
            )
        raw_aliases[path] = raw_path
        files[path] = content

    empty_files: list[str] = []
    seen_empty: set[str] = set()
    for raw_path in payload.empty_files:
        path = _safe_app_path(raw_path)
        if path in seen_empty:
            raise OrchestratorError(
                code="validation_failed",
                message=f"conflicting empty_files path alias: {raw_path!r}",
                status_code=409,
            )
        seen_empty.add(path)
        if files.get(path) != "":
            raise OrchestratorError(
                code="validation_failed",
                message="empty_files paths must normalize to an empty file payload",
                status_code=409,
            )
        empty_files.append(path)
    return payload.model_copy(update={"files": files, "empty_files": empty_files})


def _is_canonical_max_migration(path: str) -> bool:
    return path.startswith("drizzle/") and path.count("/") == 1 and path.endswith(".sql")


def _is_alternative_max_migration(path: str) -> bool:
    return (
        path.startswith("migrations/")
        or path.startswith("src/lib/db/migrations/")
        or (
            path.startswith("drizzle/")
            and path.endswith(".sql")
            and not _is_canonical_max_migration(path)
        )
        or (
            path.startswith("scripts/")
            and path != "scripts/apply-migrations.mjs"
            and "migrat" in path.rsplit("/", 1)[-1].lower()
        )
    )


def _is_postgres_identifier_continuation(character: str) -> bool:
    return bool(character) and (
        ord(character) >= 0x80
        or (
            character.isascii()
            and (character.isalnum() or character in {"_", "$"})
        )
    )


def _sql_tokens_outside_literals(sql: str) -> list[str]:
    tokens: list[str] = []
    word: list[str] = []
    index = 0
    block_depth = 0
    quote: str | None = None
    backslash_quote = False
    dollar: str | None = None

    def flush() -> None:
        if word:
            tokens.append("".join(word).upper())
            word.clear()

    while index < len(sql):
        if block_depth:
            if sql.startswith("/*", index):
                block_depth += 1
                index += 2
            elif sql.startswith("*/", index):
                block_depth -= 1
                index += 2
            else:
                index += 1
            continue
        if dollar is not None:
            end = sql.find(dollar, index)
            if end < 0:
                return tokens
            index = end + len(dollar)
            dollar = None
            continue
        if quote is not None:
            if backslash_quote and sql[index] == "\\":
                index = min(index + 2, len(sql))
                continue
            if sql[index] == quote:
                if index + 1 < len(sql) and sql[index + 1] == quote:
                    index += 2
                    continue
                quote = None
                backslash_quote = False
            index += 1
            continue
        if sql.startswith("--", index):
            flush()
            end = sql.find("\n", index + 2)
            index = len(sql) if end < 0 else end + 1
            continue
        if sql.startswith("/*", index):
            flush()
            block_depth = 1
            index += 2
            continue
        if sql[index] in {"'", '"'}:
            backslash_quote = sql[index] == "'" and "".join(word).upper() == "E"
            flush()
            quote = sql[index]
            index += 1
            continue
        if sql[index] == "$":
            previous = sql[index - 1] if index else ""
            if not _is_postgres_identifier_continuation(previous):
                match = re.match(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$", sql[index:])
                if match is not None:
                    flush()
                    dollar = match.group(0)
                    index += len(dollar)
                    continue
        if sql[index].isalpha() or sql[index] == "_":
            word.append(sql[index])
        else:
            flush()
            if sql[index] == ";":
                tokens.append(";")
        index += 1
    flush()
    return tokens


def _transaction_control_statement(sql: str) -> str | None:
    tokens = _sql_tokens_outside_literals(sql)
    singles = {"ABORT", "BEGIN", "COMMIT", "END", "RELEASE", "ROLLBACK", "SAVEPOINT"}
    statements: list[list[str]] = [[]]
    for token in tokens:
        if token == ";":
            statements.append([])
        else:
            statements[-1].append(token)
    for statement in statements:
        if not statement:
            continue
        token = statement[0]
        following = statement[1] if len(statement) > 1 else None
        if token in singles:
            return token
        if token == "START" and following == "TRANSACTION":
            return "START TRANSACTION"
        if token == "PREPARE" and following == "TRANSACTION":
            return "PREPARE TRANSACTION"
        if token == "SET" and following == "TRANSACTION":
            return "SET TRANSACTION"
    return None


def _migration_digest(content: str) -> str:
    return sha256(content.encode("utf-8")).hexdigest()


def _max_migration_receipt_path(workspace_root: Path) -> Path:
    return workspace_root.parent / f".{workspace_root.name}.max-migration-receipt.json"


def _load_max_migration_receipts(workspace_root: Path) -> dict[str, dict[str, str]]:
    receipt_path = _max_migration_receipt_path(workspace_root)
    if not receipt_path.is_file():
        return {}
    try:
        document = json.loads(receipt_path.read_text(encoding="utf-8"))
        entries = document["entries"]
        if document.get("version") != 1 or not isinstance(entries, dict):
            raise ValueError("unsupported receipt format")
        normalized: dict[str, dict[str, str]] = {}
        for path, raw in entries.items():
            if (
                not isinstance(path, str)
                or not _is_canonical_max_migration(path)
                or not isinstance(raw, dict)
                or raw.get("state") not in {"intent", "staged_unapplied", "unknown"}
                or not isinstance(raw.get("digest"), str)
            ):
                raise ValueError("invalid receipt entry")
            entry = {
                "state": raw["state"],
                "digest": raw["digest"],
            }
            for key in ("execution_id", "operation", "previous_digest", "previous_state"):
                value = raw.get(key)
                if value is not None:
                    if not isinstance(value, str):
                        raise ValueError("invalid receipt metadata")
                    entry[key] = value
            normalized[path] = entry
        return normalized
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise OrchestratorError(
            code="migration_reconciliation_required",
            message="MAX migration receipt is unreadable; reconcile before changing migrations",
            status_code=409,
        ) from exc


def _save_max_migration_receipts(
    workspace_root: Path,
    entries: dict[str, dict[str, str]],
) -> None:
    receipt_path = _max_migration_receipt_path(workspace_root)
    if not entries:
        receipt_path.unlink(missing_ok=True)
        return
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = receipt_path.with_suffix(receipt_path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump({"version": 1, "entries": entries}, handle, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, receipt_path)
    try:
        directory_fd = os.open(receipt_path.parent, os.O_RDONLY)
    except OSError as exc:
        if _directory_fsync_unsupported(exc, operation="open"):
            return
        raise
    try:
        os.fsync(directory_fd)
    except OSError as exc:
        if not _directory_fsync_unsupported(exc, operation="fsync"):
            raise
    finally:
        os.close(directory_fd)


def _directory_fsync_unsupported(error: OSError, *, operation: str) -> bool:
    # CPython on Windows rejects opening a directory with O_RDONLY/EACCES;
    # verified on the supported Windows development host. Other I/O failures
    # leave durability unknown and must fail before source mutation.
    return (
        _DIRECTORY_FSYNC_PLATFORM == "nt"
        and operation == "open"
        and error.errno == errno.EACCES
    )


async def _read_max_migration_ledger(
    container_name: str,
    paths: set[str],
) -> set[str]:
    if not paths:
        return set()
    names = sorted(Path(path).name for path in paths)
    try:
        result = await exec_cmd(
            container_name,
            cmd=[
                "node",
                "--input-type=module",
                "-e",
                _MAX_LEDGER_QUERY,
                json.dumps(names),
            ],
            workdir="/app",
            timeout_sec=30,
            max_output=16_000,
        )
    except Exception as exc:
        raise OrchestratorError(
            code="migration_reconciliation_required",
            message="MAX migration ledger is unavailable; reconcile before changing migrations",
            status_code=409,
        ) from exc
    if str(result.get("exit_code")) != "0":
        raise OrchestratorError(
            code="migration_reconciliation_required",
            message="MAX migration ledger query failed; reconcile before changing migrations",
            status_code=409,
        )
    receipt_line = next(
        (
            line[len(_MAX_LEDGER_PREFIX) :]
            for line in reversed(result.get("stdout", "").splitlines())
            if line.startswith(_MAX_LEDGER_PREFIX)
        ),
        None,
    )
    try:
        receipt = json.loads(receipt_line) if receipt_line is not None else None
    except json.JSONDecodeError as exc:
        raise OrchestratorError(
            code="migration_reconciliation_required",
            message="MAX migration ledger returned an invalid receipt",
            status_code=409,
        ) from exc
    applied_names: object
    if isinstance(receipt, list):
        lock_acquired = True
        applied_names = receipt
    elif isinstance(receipt, dict):
        lock_acquired = receipt.get("lock_acquired") is True
        applied_names = receipt.get("applied")
    else:
        lock_acquired = False
        applied_names = None
    if not lock_acquired:
        raise OrchestratorError(
            code="migration_reconciliation_required",
            message="a previous MAX migration runner is still active; retry after reconciliation",
            status_code=409,
        )
    if not isinstance(applied_names, list) or not all(
        isinstance(name, str) and name in names for name in applied_names
    ):
        raise OrchestratorError(
            code="migration_reconciliation_required",
            message="MAX migration ledger returned an invalid receipt",
            status_code=409,
        )
    return {
        path
        for path in paths
        if Path(path).name in set(applied_names)
    }


async def _reconcile_max_migration_receipts(
    workspace_root: Path,
    container_name: str,
) -> dict[str, dict[str, str]]:
    receipts = _load_max_migration_receipts(workspace_root)
    if not receipts:
        return receipts
    applied = await _read_max_migration_ledger(container_name, set(receipts))
    changed = False
    for path, entry in list(receipts.items()):
        target = workspace_root / path
        current_digest = (
            _migration_digest(target.read_text(encoding="utf-8"))
            if target.is_file() and not target.is_symlink()
            else None
        )
        if path in applied:
            if current_digest != entry["digest"]:
                entry["state"] = "unknown"
                changed = True
                _save_max_migration_receipts(workspace_root, receipts)
                raise OrchestratorError(
                    code="migration_reconciliation_required",
                    message=f"{path} is ledger-applied but source digest does not match its intent",
                    status_code=409,
                )
            receipts.pop(path, None)
            changed = True
            continue
        if entry["state"] == "intent":
            operation = entry.get("operation", "write")
            source_reached_intent = (
                current_digest is None
                if operation == "delete"
                else current_digest == entry["digest"]
            )
            if source_reached_intent:
                if operation == "delete":
                    # Only a proved-unapplied staged migration can reach a
                    # deletion intent. Once its source is absent there is
                    # nothing left for the runner or ledger to reconcile.
                    receipts.pop(path, None)
                else:
                    entry["state"] = "staged_unapplied"
                changed = True
                continue
            previous_state = entry.get("previous_state", "missing")
            previous_digest = entry.get("previous_digest")
            source_is_previous = (
                current_digest is None
                if previous_state == "missing"
                else current_digest == previous_digest
            )
            if source_is_previous:
                if previous_state == "staged_unapplied":
                    entry["state"] = "staged_unapplied"
                    entry["digest"] = previous_digest or ""
                else:
                    receipts.pop(path, None)
                changed = True
                continue
        elif current_digest == entry["digest"]:
            if entry["state"] == "unknown":
                entry["state"] = "staged_unapplied"
                changed = True
            continue
        entry["state"] = "unknown"
        changed = True
        _save_max_migration_receipts(workspace_root, receipts)
        raise OrchestratorError(
            code="migration_reconciliation_required",
            message=f"{path} receipt does not match workspace source; reconcile manually",
            status_code=409,
        )
    if changed:
        _save_max_migration_receipts(workspace_root, receipts)
    return receipts


def _prepare_max_migration_intents(
    workspace_root: Path,
    payload: HotReloadRequest,
    paths: set[str],
) -> str | None:
    if not paths:
        return None
    receipts = _load_max_migration_receipts(workspace_root)
    execution_id = str(uuid4())
    explicit_empty = set(payload.empty_files)
    for path in paths:
        target = workspace_root / path
        previous = receipts.get(path)
        previous_digest = (
            _migration_digest(target.read_text(encoding="utf-8"))
            if target.is_file() and not target.is_symlink()
            else ""
        )
        if path in payload.files:
            content = payload.files[path]
            deletion = content == "" and path not in explicit_empty
        else:
            # A runner applies every pending canonical file, not only the file
            # named in this request. Give all of them the same durable execution
            # intent before launching the runner.
            content = target.read_text(encoding="utf-8")
            deletion = False
        receipts[path] = {
            "state": "intent",
            "digest": _migration_digest(content),
            "execution_id": execution_id,
            "operation": "delete" if deletion else "write",
            "previous_digest": previous_digest,
            "previous_state": (
                previous["state"]
                if previous is not None
                else "accepted"
                if previous_digest
                else "missing"
            ),
        }
    _save_max_migration_receipts(workspace_root, receipts)
    return execution_id


async def _validate_max_hot_reload_contract(
    payload: HotReloadRequest,
    workspace_root: Path,
    container_name: str,
) -> set[str]:
    runner_path = workspace_root / "scripts" / "apply-migrations.mjs"
    if not runner_path.is_file() or runner_path.is_symlink():
        raise OrchestratorError(
            code="validation_failed",
            message="MAX platform-owned migration runner is missing from the trusted workspace",
            status_code=409,
        )

    explicit_empty = set(payload.empty_files)
    existing_canonical = sorted(
        path.relative_to(workspace_root).as_posix()
        for path in (workspace_root / "drizzle").glob("*.sql")
        if path.is_file() and not path.is_symlink()
    ) if (workspace_root / "drizzle").is_dir() else []
    new_canonical: list[str] = []
    corrected_staged: set[str] = set()
    receipts = await _reconcile_max_migration_receipts(workspace_root, container_name)
    for path, content in payload.files.items():
        target = workspace_root / path
        deletion = content == "" and path not in explicit_empty
        if path == "scripts/apply-migrations.mjs":
            if deletion or content != runner_path.read_text(encoding="utf-8"):
                raise OrchestratorError(
                    code="validation_failed",
                    message="scripts/apply-migrations.mjs is platform-owned and cannot be replaced",
                    status_code=409,
                )
            continue
        if _is_canonical_max_migration(path):
            if not deletion:
                forbidden = _transaction_control_statement(content)
                if forbidden is not None:
                    raise OrchestratorError(
                        code="validation_failed",
                        message=(
                            f"{path} contains forbidden transaction control: {forbidden}"
                        ),
                        status_code=409,
                    )
            if target.is_file():
                if target.is_symlink():
                    raise OrchestratorError(
                        code="validation_failed",
                        message=f"workspace target is a symlink: {path}",
                        status_code=403,
                    )
                current_content = target.read_text(encoding="utf-8")
                if deletion or content != current_content:
                    receipt = receipts.get(path)
                    if receipt is None:
                        raise OrchestratorError(
                            code="validation_failed",
                            message=(
                                f"{path} is an accepted canonical migration "
                                "and must remain immutable"
                            ),
                            status_code=409,
                        )
                    if (
                        receipt["state"] == "unknown"
                        or receipt["digest"] != _migration_digest(current_content)
                    ):
                        raise OrchestratorError(
                            code="migration_reconciliation_required",
                            message=(
                                f"{path} has an unknown runner outcome; "
                                "reconcile before overwrite"
                            ),
                            status_code=409,
                        )
                    applied = await _read_max_migration_ledger(container_name, {path})
                    if path in applied:
                        receipts.pop(path, None)
                        _save_max_migration_receipts(workspace_root, receipts)
                        raise OrchestratorError(
                            code="validation_failed",
                            message=f"{path} is applied and must remain immutable",
                            status_code=409,
                        )
                    corrected_staged.add(path)
            elif not deletion:
                if not content.strip():
                    raise OrchestratorError(
                        code="validation_failed",
                        message=f"{path} must contain a non-empty canonical migration",
                        status_code=409,
                    )
                new_canonical.append(path)
            continue
        if _is_alternative_max_migration(path):
            if deletion and target.is_file() and not target.is_symlink():
                continue
            if target.is_file() and not target.is_symlink() and content == target.read_text(
                encoding="utf-8"
            ):
                continue
            raise OrchestratorError(
                code="validation_failed",
                message=(
                    f"{path} cannot be introduced or modified for MAX; "
                    "remove it or use drizzle/*.sql"
                ),
                status_code=409,
            )

    if existing_canonical:
        last_existing = existing_canonical[-1]
        for path in sorted(new_canonical):
            if path <= last_existing:
                raise OrchestratorError(
                    code="validation_failed",
                    message=f"{path} must append after {last_existing}",
                    status_code=409,
                )
    if new_canonical:
        unresolved = sorted(path for path in receipts if path not in corrected_staged)
        if unresolved:
            raise OrchestratorError(
                code="validation_failed",
                message=(
                    "resolve pending MAX migration before appending a newer file: "
                    + ", ".join(unresolved)
                ),
                status_code=409,
            )
        already_applied = await _read_max_migration_ledger(
            container_name,
            set(new_canonical),
        )
        if already_applied:
            raise OrchestratorError(
                code="migration_reconciliation_required",
                message=(
                    "migration ledger already contains source-missing files: "
                    + ", ".join(sorted(already_applied))
                ),
                status_code=409,
            )
    return corrected_staged


def _project_workspace_dir(project_id: str) -> Path:
    return Path(get_settings().projects_root) / project_id


def _project_workspace_lock(project_id: str) -> asyncio.Lock:
    return _PROJECT_WORKSPACE_LOCKS.setdefault(project_id, asyncio.Lock())


def _workspace_revision(files: dict[str, str]) -> str:
    digest = sha256()
    for path, content in sorted(files.items()):
        # Next and TypeScript regenerate these during verification. Preserve
        # next-env.d.ts in snapshots for cold restores, but do not let compiler
        # bookkeeping create a new source identity on every check/build.
        if Path(path).name == "next-env.d.ts" or path.endswith(".tsbuildinfo"):
            continue
        path_bytes = path.encode("utf-8")
        content_bytes = content.encode("utf-8")
        digest.update(len(path_bytes).to_bytes(8, "big"))
        digest.update(path_bytes)
        digest.update(len(content_bytes).to_bytes(8, "big"))
        digest.update(content_bytes)
    return digest.hexdigest()


def _sandbox_name_is_secret(name: str) -> bool:
    lowered = name.lower()
    return (
        lowered == ".env"
        or lowered.startswith(".env.")
        or lowered in {"secrets.json", "secrets.yaml", "secrets.yml"}
    )


def _apply_workspace_files(
    project_id: str,
    files: dict[str, str],
    *,
    empty_files: tuple[str, ...] = (),
) -> None:
    root = _project_workspace_dir(project_id)
    root.mkdir(parents=True, exist_ok=True)
    canonical_root = root.resolve()
    explicit_empty_paths = {_safe_app_path(path) for path in empty_files if path}
    for raw_path, content in files.items():
        rel = _safe_app_path(raw_path)
        if _sandbox_name_is_secret(Path(rel).name):
            raise OrchestratorError(
                code="validation_failed",
                message=f"secret file is not allowed in the agent workspace: {rel}",
                status_code=403,
            )
        target = root / rel
        cursor = root
        for part in Path(rel).parts[:-1]:
            cursor /= part
            if cursor.is_symlink():
                raise OrchestratorError(
                    code="validation_failed",
                    message=f"workspace path crosses a symlink: {rel}",
                    status_code=403,
                )
        try:
            target.parent.resolve().relative_to(canonical_root)
        except (OSError, ValueError) as exc:
            raise OrchestratorError(
                code="validation_failed",
                message=f"workspace path escapes project root: {rel}",
                status_code=403,
            ) from exc
        if target.is_symlink():
            raise OrchestratorError(
                code="validation_failed",
                message=f"workspace target is a symlink: {rel}",
                status_code=403,
            )
        if content == "" and rel not in explicit_empty_paths:
            try:
                target.unlink()
            except FileNotFoundError:
                pass
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def _collect_workspace_text_files(root: Path) -> tuple[dict[str, str], set[str]]:
    files: dict[str, str] = {}
    dropped: set[str] = set()
    total_bytes = 0
    if not root.exists():
        return files, dropped
    for path in root.rglob("*"):
        if path.is_symlink():
            try:
                dropped.add(path.relative_to(root).as_posix())
            except ValueError:
                pass
            continue
        if not path.is_file():
            continue
        if any(part in _SANDBOX_SKIP_NAMES for part in path.parts):
            continue
        rel = path.relative_to(root).as_posix()
        if _sandbox_name_is_secret(path.name):
            dropped.add(rel)
            continue
        try:
            size = path.stat().st_size
        except OSError:
            dropped.add(rel)
            continue
        if size > _SANDBOX_SYNC_MAX_FILE_BYTES:
            dropped.add(rel)
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            dropped.add(rel)
            continue
        files[rel] = content
        total_bytes += len(content.encode("utf-8"))
        if len(files) > _SANDBOX_SYNC_MAX_FILES:
            raise OrchestratorError(
                code="validation_failed",
                message=(
                    f"sandbox workspace exceeds sync file budget: {len(files)} > "
                    f"{_SANDBOX_SYNC_MAX_FILES}"
                ),
                status_code=413,
            )
        if total_bytes > _SANDBOX_SYNC_MAX_TOTAL_BYTES:
            raise OrchestratorError(
                code="validation_failed",
                message=(
                    "sandbox workspace exceeds sync payload budget: "
                    f"{total_bytes} > {_SANDBOX_SYNC_MAX_TOTAL_BYTES}"
                ),
                status_code=413,
            )
    return files, dropped


@router.post("/{project_id}/heartbeat")
async def heartbeat(
    project_id: str,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    """Reset the hibernate idle timer for a project — HTTP fallback for the
    Redis `activity:<project_id>` pub-sub channel.

    Steady-state production publishes activity from the ingress proxy to
    Redis (one less round-trip). This endpoint exists for environments
    without Redis (tests, bare-metal docker-compose) and for apps/api to
    use directly if its proxy already touches the orchestrator anyway.
    """
    _verify_token(x_internal_token)
    await record_activity(project_id)
    return {"state": "recorded"}


@router.post("/keep-alive", response_model=KeepAliveResponse)
async def keep_alive(
    payload: KeepAliveRequest,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> KeepAliveResponse:
    """Enable or disable the durable no-hibernation mode for a project."""
    _verify_token(x_internal_token)
    await set_keep_alive(str(payload.project_id), payload.enabled)
    return KeepAliveResponse(project_id=payload.project_id, enabled=payload.enabled)


@router.post("/hot-reload")
async def hot_reload(
    payload: HotReloadRequest,
    slug: str,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    """Copy AI-generated files into the running dev container; Next.js HMR
    picks up changes without restart.

    Lookup is by `omnia-dev-<slug>` for the same reason `status` / `destroy`
    do it (PoC: no project-name registry yet — apps/api always knows the slug).
    `slug` is a query param to keep the JSON body matching `HotReloadRequest`
    exactly (which only carries project_id + files; slug-resolution is the
    orchestrator's internal concern).

    Side-effects beyond the file write:
      - A MAX workspace accepts only direct `drizzle/*.sql` migrations and
        applies them with its platform-owned `scripts/apply-migrations.mjs`.
      - Other templates retain their legacy `drizzle-kit push` behavior when
        `src/lib/db/schema.ts` or `src/lib/db/migrations/` changes.
      - MAX migration failures raise a typed conflict after source persistence;
        callers cannot attest or publish an unconfirmed database state.
    """
    _verify_token(x_internal_token)
    # A build writing files is activity too — keep hibernate off its back.
    await record_activity(str(payload.project_id))
    project_id = str(payload.project_id)
    async with _project_workspace_lock(project_id):
        if payload.base_workspace_revision:
            current_files, _dropped = await asyncio.to_thread(
                _collect_workspace_text_files,
                _project_workspace_dir(project_id),
            )
            if _workspace_revision(current_files) != payload.base_workspace_revision:
                raise OrchestratorError(
                    code="conflict",
                    message="project workspace changed after the sandbox command; rerun it",
                    status_code=409,
                )
        return await _hot_reload_locked(payload, slug)


async def _hot_reload_locked(payload: HotReloadRequest, slug: str) -> dict[str, str]:
    container_name = f"omnia-dev-{slug}"
    workspace_root = _project_workspace_dir(str(payload.project_id))
    payload = _canonical_hot_reload_payload(payload)
    max_migration_contract = (
        await container_image_template(container_name) == _MAX_PREVIEW_TEMPLATE
    )
    corrected_staged: set[str] = set()
    if max_migration_contract:
        corrected_staged = await _validate_max_hot_reload_contract(
            payload,
            workspace_root,
            container_name,
        )
    explicit_empty = set(payload.empty_files)
    canonical_max_migration_touched = max_migration_contract and any(
        _is_canonical_max_migration(path)
        and not (content == "" and path not in explicit_empty)
        for path, content in payload.files.items()
    )
    canonical_intent_paths = {
        path
        for path, content in payload.files.items()
        if max_migration_contract
        and _is_canonical_max_migration(path)
        and (
            not (content == "" and path not in explicit_empty)
            or path in corrected_staged
        )
    }
    if canonical_max_migration_touched:
        canonical_intent_paths.update(
            path
            for path in _load_max_migration_receipts(workspace_root)
            if (workspace_root / path).is_file()
            and not (workspace_root / path).is_symlink()
        )
    _prepare_max_migration_intents(
        workspace_root,
        payload,
        canonical_intent_paths,
    )

    write_result = await write_files(
        container_name,
        payload.files,
        empty_files=payload.empty_files,
    )
    await asyncio.to_thread(
        _apply_workspace_files,
        str(payload.project_id),
        payload.files,
        empty_files=tuple(payload.empty_files),
    )
    if corrected_staged:
        removed_staged = {
            path
            for path in corrected_staged
            if payload.files.get(path) == "" and path not in explicit_empty
        }
        if removed_staged:
            receipts = _load_max_migration_receipts(workspace_root)
            for path in removed_staged:
                receipts.pop(path, None)
            _save_max_migration_receipts(workspace_root, receipts)

    # Seed PUBLIC entity catalogs with demo rows so the first browse screen
    # isn't an empty-state (NORTH STAR pillars 1 & 4). Idempotent (only fills
    # empty catalogs) and fail-soft (never raises) — see demo_seed_writer.
    seeded = await demo_seed_writer.seed_demo_data(payload.project_id, payload.files, niche=slug)

    # Dependency selection belongs to the project now. Lifecycle scripts already
    # ran (if needed) in the secretless disposable sandbox; the live preview only
    # materialises the resolved tree with scripts disabled, so a package cannot
    # read runtime DB/auth/bot credentials during install.
    package_touched = any(
        path in {"package.json", "pnpm-lock.yaml"} for path in payload.files
    )
    package_result: dict[str, str] | None = None
    if package_touched:
        try:
            package_result = await exec_cmd(
                container_name,
                cmd=["pnpm", "install", "--no-frozen-lockfile", "--ignore-scripts"],
                workdir="/app",
                timeout_sec=240,
                max_output=_AGENT_MAX_BUILD,
            )
        except OrchestratorError as exc:
            package_result = {
                "exit_code": "-1",
                "stdout": "",
                "stderr": f"orchestrator: {exc.message}",
            }
    pnpm_lockfile: str | None = None
    if package_result is not None and package_result["exit_code"] == "0":
        try:
            lock_result = await exec_cmd(
                container_name,
                cmd=["cat", "--", "pnpm-lock.yaml"],
                workdir="/app",
                max_output=_SANDBOX_SYNC_MAX_FILE_BYTES + 1,
            )
            lock_content = lock_result["stdout"]
            if (
                lock_result["exit_code"] != "0"
                or len(lock_content.encode("utf-8")) > _SANDBOX_SYNC_MAX_FILE_BYTES
            ):
                package_result = {
                    "exit_code": "1",
                    "stdout": "",
                    "stderr": "generated pnpm-lock.yaml is missing or exceeds the file quota",
                }
            else:
                pnpm_lockfile = lock_content
                await asyncio.to_thread(
                    _apply_workspace_files,
                    str(payload.project_id),
                    {"pnpm-lock.yaml": lock_content},
                )
        except OrchestratorError as exc:
            package_result = {
                "exit_code": "-1",
                "stdout": "",
                "stderr": f"orchestrator: {exc.message}",
            }

    # MAX uses the same platform-owned migration runner in development and in
    # accepted runtime artifacts. Other templates retain their existing schema
    # push behavior until they adopt an explicit migration contract.
    schema_touched = any(
        p == "src/lib/db/schema.ts" or p.startswith("src/lib/db/migrations/") for p in payload.files
    )
    canonical_run_paths = {
        path
        for path, content in payload.files.items()
        if max_migration_contract
        and _is_canonical_max_migration(path)
        and not (content == "" and path not in set(payload.empty_files))
    }
    if max_migration_contract:
        canonical_run_paths.update(
            path
            for path in _load_max_migration_receipts(workspace_root)
            if (workspace_root / path).is_file()
        )
    drizzle_result: dict[str, str] | None = None
    runner_outcome_unknown = False
    if canonical_max_migration_touched or (schema_touched and not max_migration_contract):
        try:
            drizzle_result = await exec_cmd(
                container_name,
                cmd=(
                    ["node", "scripts/apply-migrations.mjs"]
                    if canonical_max_migration_touched
                    else [
                        "npx",
                        "--yes",
                        "drizzle-kit",
                        "push",
                        "--config=drizzle.config.ts",
                    ]
                ),
                workdir="/app",
                timeout_sec=90,
            )
        except Exception as exc:
            runner_outcome_unknown = True
            error_message = exc.message if isinstance(exc, OrchestratorError) else str(exc)
            drizzle_result = {
                "exit_code": "-1",
                "stdout": "",
                "stderr": f"orchestrator: {error_message}",
            }

    if max_migration_contract and drizzle_result is not None:
        receipts = _load_max_migration_receipts(workspace_root)
        if runner_outcome_unknown:
            for path in canonical_run_paths:
                source = (workspace_root / path).read_text(encoding="utf-8")
                previous = receipts.get(path, {})
                receipts[path] = {
                    "state": "unknown",
                    "digest": _migration_digest(source),
                    "execution_id": previous.get("execution_id", str(uuid4())),
                }
            _save_max_migration_receipts(workspace_root, receipts)
            raise OrchestratorError(
                code="migration_reconciliation_required",
                message=(
                    "MAX migration runner outcome is unknown after source files were written; "
                    "reconcile the ledger before retrying"
                ),
                status_code=409,
                details={
                    "source_files_written": True,
                    "database_changes_confirmed": False,
                },
            )
        try:
            applied_paths = await _read_max_migration_ledger(
                container_name,
                canonical_run_paths,
            )
        except OrchestratorError as exc:
            for path in canonical_run_paths:
                source = (workspace_root / path).read_text(encoding="utf-8")
                previous = receipts.get(path, {})
                receipts[path] = {
                    "state": "unknown",
                    "digest": _migration_digest(source),
                    "execution_id": previous.get("execution_id", str(uuid4())),
                }
            _save_max_migration_receipts(workspace_root, receipts)
            raise OrchestratorError(
                code="migration_reconciliation_required",
                message=(
                    "MAX migration ledger could not confirm the runner result after "
                    "source files were written; reconcile before retrying"
                ),
                status_code=409,
                details={
                    "source_files_written": True,
                    "database_changes_confirmed": False,
                    "ledger_error": exc.message,
                },
            ) from exc
        for path in canonical_run_paths:
            if path in applied_paths:
                receipts.pop(path, None)
                continue
            source = (workspace_root / path).read_text(encoding="utf-8")
            previous = receipts.get(path, {})
            receipts[path] = {
                "state": (
                    "unknown"
                    if str(drizzle_result["exit_code"]) == "0"
                    else "staged_unapplied"
                ),
                "digest": _migration_digest(source),
                "execution_id": previous.get("execution_id", str(uuid4())),
            }
        _save_max_migration_receipts(workspace_root, receipts)
        if str(drizzle_result["exit_code"]) == "0" and applied_paths != canonical_run_paths:
            raise OrchestratorError(
                code="migration_reconciliation_required",
                message="MAX runner exited successfully without a complete ledger receipt",
                status_code=409,
                details={
                    "source_files_written": True,
                    "database_changes_confirmed": False,
                },
            )
        if str(drizzle_result["exit_code"]) != "0":
            all_confirmed_applied = applied_paths == canonical_run_paths
            raise OrchestratorError(
                code="migration_apply_failed",
                message=(
                    "MAX migration runner failed after source files were written; "
                    + (
                        "ledger confirms the migration files were applied"
                        if all_confirmed_applied
                        else "ledger proves the failed files were not applied"
                    )
                ),
                status_code=409,
                details={
                    "source_files_written": True,
                    "database_changes_confirmed": all_confirmed_applied,
                    "exit_code": str(drizzle_result["exit_code"]),
                    "stderr_tail": drizzle_result["stderr"][-500:],
                },
            )

    response: dict[str, str] = {
        "state": "hot_reloaded",
        "written": write_result.get("written", "0"),
        "total_bytes": write_result.get("total_bytes", "0"),
        "dropped": write_result.get("dropped", ""),
        "seeded": str(sum(seeded.values())),
    }
    if drizzle_result is not None:
        response["drizzle_exit_code"] = drizzle_result["exit_code"]
        response["drizzle_stderr_tail"] = drizzle_result["stderr"][-500:]
    if package_result is not None:
        response["package_exit_code"] = package_result["exit_code"]
        response["package_stderr_tail"] = package_result["stderr"][-500:]
    if pnpm_lockfile is not None:
        response["pnpm_lockfile"] = pnpm_lockfile
    return response


# ── Agentic builder tools (Phase 0) ─────────────────────────────────────────
# Internal-token-gated capability surface the api-side agent loop calls to act
# on the live dev container: read any /app file, list, grep, and run a real
# typecheck/build. Separate from the whitelisted ``read-file`` above (used by
# style edits) so that path is untouched. exec_cmd already runs non-root
# (1000:1000) inside the cap-dropped container; `_safe_app_path` blocks escape.


@router.get("/{project_id}/agent/grep")
async def agent_grep(
    project_id: str,
    slug: str,
    pattern: str,
    path: str = "src",
    x_internal_token: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    """Recursive text search under /app (agent loop). grep exit 1 = no match."""
    _verify_token(x_internal_token)
    await record_activity(project_id)
    rel = _safe_app_path(path)
    if not pattern:
        raise OrchestratorError(
            code="validation_failed",
            message="empty pattern",
            status_code=400,
        )
    container_name = f"omnia-dev-{slug}"
    try:
        # argv (no shell) → no injection; `--` ends options so a pattern that
        # starts with `-` can't become a flag.
        result = await exec_cmd(
            container_name,
            cmd=["grep", "-rnI", "--", pattern, rel],
            workdir="/app",
            max_output=_AGENT_MAX_GREP,
        )
    except OrchestratorError as exc:
        if exc.code == "container_not_running":
            raise
        return {"ok": False, "detail": "container not running"}
    out = result["stdout"]
    return {"ok": True, "detail": out if out else "(no matches)"}


# Phase 1: a bounded shell tool for the agent. Runs an arbitrary command inside
# the project's dev container via `sh -lc`. Safe-by-construction: the container
# is cap-dropped (ALL), non-root (1000:1000), memory-capped, loopback-bound, on
# an isolated network, with a schema-scoped DB role — so the blast radius is the
# project's own container. Bounded by timeout + output cap. (Egress lockdown is
# a follow-up; today outbound is open.) A small denylist blocks the obvious
# foot-guns. Lets the agent run npm install / lint / tests / the dev server.
_EXEC_DENY = ("rm -rf /", ":(){", "mkfs", "dd if=", "/dev/sd", "shutdown", "reboot")
_EXEC_ENV_ENUM_RE = re.compile(
    r"""(?ix)
    \b(?:env|printenv)\b
    |
    (?:^|[;&|]\s*)(?:export|set|declare\s+-x)(?:\s*(?:$|[;&|]))
    |
    /proc/[^\s]*/environ
    |
    \b(?:process\.env|os\.environ|os\.getenv)\b
    |
    (?:^|[\s/])\.env(?:\.[\w.-]+)?(?:\s|$)
    |
    \$\{?[A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|PASS|PRIVATE_KEY|ACCESS_KEY|API_KEY|DATABASE_URL)[A-Z0-9_]*\}?
    """
)
_EXEC_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?im)^(\s*(?:export\s+)?[A-Z0-9_]*"
    r"(?:SECRET|TOKEN|PASSWORD|PASS|PRIVATE_KEY|ACCESS_KEY|API_KEY|DATABASE_URL)"
    r"[A-Z0-9_]*\s*=\s*)([^\r\n]*)$"
)
_EXEC_DSN_RE = re.compile(
    r"(?i)\b((?:postgres(?:ql)?|mysql|mariadb|redis|mongodb)"
    r"(?:\+[a-z0-9_]+)?://[^:\s/@]+:)([^@\s/]+)(@)"
)
_EXEC_AUTH_HEADER_RE = re.compile(
    r"(?im)^(\s*(?:authorization|x-api-key|x-max-bot-api-secret)\s*:\s*)"
    r"([^\r\n]+)$"
)
_EXEC_KNOWN_TOKEN_RE = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,})\b")
_EXEC_PRIVATE_KEY_RE = re.compile(
    r"(?s)-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----"
)


def _redact_exec_output(value: str) -> str:
    redacted = _EXEC_SECRET_ASSIGNMENT_RE.sub(r"\1[REDACTED]", value)
    redacted = _EXEC_DSN_RE.sub(r"\1[REDACTED]\3", redacted)
    redacted = _EXEC_AUTH_HEADER_RE.sub(r"\1[REDACTED]", redacted)
    redacted = _EXEC_KNOWN_TOKEN_RE.sub("[REDACTED]", redacted)
    return _EXEC_PRIVATE_KEY_RE.sub("[REDACTED PRIVATE KEY]", redacted)


def _command_exposes_environment(cmd: str) -> bool:
    return bool(_EXEC_ENV_ENUM_RE.search(cmd.strip()))


@router.get("/{project_id}/deploy", response_model=DeployResponse)
async def get_deploy(
    project_id: str,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> DeployResponse:
    """Last deploy state for a project (phase / prod_url / image_tag / error)."""
    _verify_token(x_internal_token)

    from yleum_orchestrator.services.cell_publication import get_cell_publication_service

    public = get_cell_publication_service().get(UUID(project_id))
    if public is not None:
        return public
    # No publication yet. The legacy deploy journal went with the site builder,
    # so an app that has never been published is simply idle.
    return DeployResponse(project_id=UUID(project_id), phase="idle")


@router.get("/{project_id}/deploy/history", response_model=list[DeployResponse])
async def get_deploy_history(
    project_id: str,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> list[DeployResponse]:
    _verify_token(x_internal_token)

    from yleum_orchestrator.services.cell_publication import get_cell_publication_service

    return get_cell_publication_service().history(UUID(project_id))


@router.get("/{project_id}/status", response_model=StatusResponse)
async def status(
    project_id: str,
    slug: str | None = None,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> StatusResponse:
    """Container state derived from Docker inspect.

    Resolves the dev container by the `omnia.project_id` label (`slug` is an
    optional fallback). Returns the browser-reachable nginx dev URL — not the
    `127.0.0.1:<port>` loopback, which was the "connection refused" preview.
    """
    _verify_token(x_internal_token)

    name = await find_project_container(project_id, kind="dev")
    keep_alive = is_keep_alive_enabled(project_id)
    if name is None and slug:
        name = f"omnia-dev-{slug}"
    if name is None:
        return StatusResponse(
            project_id=UUID(project_id),
            state="stopped",
            keep_alive=keep_alive,
        )

    info = await docker_container_status(name)
    if info["state"] == "not_found":
        return StatusResponse(
            project_id=UUID(project_id),
            state="stopped",
            keep_alive=keep_alive,
        )

    state_map = {
        "running": "running",
        "paused": "paused",
        "exited": "stopped",
        "created": "provisioning",
        "restarting": "provisioning",
        "dead": "failed",
    }
    derived_slug = name.removeprefix("omnia-dev-")

    # Area C (DARK): expose the per-project AUTH_SECRET so the gate worker can
    # re-derive the seed operator's password and drive a real login. Populated
    # ONLY when OMNIA_GATE_SEED=1; null otherwise → contract unchanged. The
    # secret comes from _load_or_create_auth_secret, which is idempotent and
    # read-only once the per-project secret file exists.
    gate_seed: dict[str, str] | None = None
    if rebrand_env("GATE_SEED") == "1":
        from yleum_orchestrator.services.provisioner import (
            _load_or_create_auth_secret,
        )

        gate_seed = {
            "email": rebrand_env("GATE_SEED_EMAIL", "gate@omnia.local"),
            "auth_secret": _load_or_create_auth_secret(project_id),
        }

    return StatusResponse(
        project_id=UUID(project_id),
        state=state_map.get(info["state"], "stopped"),
        container_name=name,
        port=int(info["port"]) if info["port"] else None,
        dev_url=nginx_writer.dev_url(derived_slug) if derived_slug else None,
        keep_alive=keep_alive,
        gate_seed=gate_seed,
    )


@router.post("/{project_id}/destroy")
async def destroy(
    project_id: str,
    slug: str,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    """Full teardown of a project's runtime. Mirrors :func:`provision` in reverse.

    Removes the dev + prod containers, releases both ports, archives the
    per-project Postgres schema (soft-delete — rule 5: user data is kept for a
    grace window, not hard-dropped), and removes the dev + prod nginx vhosts.

    Idempotent (R-10): every step is a no-op when its resource is already gone,
    so apps/api can safely retry after a partial failure. `slug` query param has
    the same rationale as `status`/`hot-reload` (no project_id↔name registry).
    """
    _verify_token(x_internal_token)

    pid = UUID(project_id)

    # Public Cell recovery must be durably disabled before the API deletes its
    # owner/bot rows. Keep retained business data, but never resurrect ingress.
    from yleum_orchestrator.services.cell_publication import get_cell_publication_service

    await get_cell_publication_service().disable(pid, slug)

    # 1. Containers — dev + prod. Missing is a no-op.
    await destroy_container(f"omnia-dev-{slug}")
    await destroy_container(f"omnia-app-{slug}")
    await destroy_project_network(
        project_id,
        service_names=(get_settings().runtime_db_container_name,),
    )

    # 2. Ports — dev + prod pools.
    await get_port_allocator().release(pid)
    await get_prod_port_allocator().release(pid)

    # 3. Per-project Postgres — soft archive (rename aside), keep data recoverable.
    await postgres_admin.archive_schema(pid)

    # 4. nginx vhosts — dev + prod. Missing site is a no-op.
    await nginx_writer.unpublish(nginx_writer.dev_host(slug))
    await nginx_writer.unpublish(nginx_writer.prod_host(slug))

    return {"state": "destroyed"}
