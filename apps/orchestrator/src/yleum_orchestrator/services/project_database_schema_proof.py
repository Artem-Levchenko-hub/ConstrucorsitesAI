"""Versioned schema comparison with an observed, private role-upgrade bridge.

Callers hold lifecycle locks, verify physical database identity, fence guest
writers and validate the actual role policy. This module executes no SQL and
does not attest role security: v2 deliberately omits the three managed roles.
Historical release/checkpoint digests and archive bytes are never rewritten.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any

from pglast import ast, enums, parse_sql

from yleum_orchestrator.services.cell_state import _read_plain_json_file
from yleum_orchestrator.services.project_database_roles import (
    PROJECT_MIGRATOR_ROLE,
    PROJECT_OWNER_ROLE,
    PROJECT_RUNTIME_ROLE,
)
from yleum_orchestrator.services.project_machine import write_controller_json

_MANAGED = frozenset((PROJECT_OWNER_ROLE, PROJECT_MIGRATOR_ROLE, PROJECT_RUNTIME_ROLE))
_ATTRIBUTES = frozenset(
    (
        "superuser",
        "inherit",
        "createrole",
        "createdb",
        "canlogin",
        "isreplication",
        "bypassrls",
        "connectionlimit",
        "validUntil",
    )
)
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_DOLLAR = re.compile(r"\$(?:[A-Za-z_][A-Za-z_0-9]*)?\$")


class SchemaProofError(ValueError):
    """Fail closed without exposing dump contents, credentials or driver errors."""


def legacy_schema_digest(dump: str) -> str:
    """Exact historical v1 normalization, including its treatment of blank lines."""
    lines = [
        line
        for line in dump.splitlines()
        if not line.startswith(("\\restrict ", "\\unrestrict ", "--"))
    ]
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def _statements(dump: str) -> list[str]:
    """Split outside quoted literals/identifiers/bodies and nested comments.

    Dump comments outside stored bodies are presentation; comments *inside*
    a literal/dollar body remain byte-for-byte. Known psql connection commands
    remain in the projection so database boundaries cannot be erased.
    """
    if not isinstance(dump, str) or not dump.strip() or "\x00" in dump:
        raise SchemaProofError("invalid schema dump")
    result: list[str] = []
    text: list[str] = []
    index = 0
    while index < len(dump):
        char = dump[index]
        if dump.startswith("--", index):
            end = dump.find("\n", index)
            index = len(dump) if end < 0 else end
            text.append(" ")
            continue
        if dump.startswith("/*", index):
            level = 1
            index += 2
            while index < len(dump) and level:
                if dump.startswith("/*", index):
                    level += 1
                    index += 2
                elif dump.startswith("*/", index):
                    level -= 1
                    index += 2
                else:
                    index += 1
            if level:
                raise SchemaProofError("incomplete schema comment")
            text.append(" ")
            continue
        if char == "\\":
            # A psql command is allowed only on its own line, between statements.
            if "".join(text).strip() or dump[dump.rfind("\n", 0, index) + 1 : index].strip():
                raise SchemaProofError("unexpected schema command")
            end = dump.find("\n", index)
            if end < 0:
                end = len(dump)
            command = dump[index:end].strip()
            if re.fullmatch(r"\\(?:un)?restrict [A-Za-z0-9_]+", command):
                pass
            elif command.startswith("\\connect ") and command[9:].strip():
                result.append(command)
            else:
                raise SchemaProofError("unsupported schema command")
            text.clear()
            index = end
            continue
        if char in ("'", '"'):
            start = index
            # Escape strings use E'...'; ordinary strings only double quotes.
            prefix = "".join(text)
            escaped = char == "'" and bool(re.search(r"(?<![\w$])[eE]\Z", prefix))
            index += 1
            closed = False
            while index < len(dump):
                if escaped and dump[index] == "\\":
                    index += 2
                elif dump[index] == char:
                    index += 1
                    if index < len(dump) and dump[index] == char:
                        index += 1
                    else:
                        closed = True
                        break
                else:
                    index += 1
            if not closed:
                raise SchemaProofError("incomplete schema literal")
            text.append(dump[start:index])
            continue
        if char == "$" and (index == 0 or not re.match(r"[\w$]", dump[index - 1])):
            match = _DOLLAR.match(dump, index)
            if match is not None:
                end = dump.find(match.group(), match.end())
                if end < 0:
                    raise SchemaProofError("incomplete schema body")
                end += len(match.group())
                text.append(dump[index:end])
                index = end
                continue
        text.append(char)
        index += 1
        if char == ";":
            statement = "".join(text).strip()
            if statement != ";":
                result.append(statement)
            text.clear()
    if "".join(text).strip():
        raise SchemaProofError("incomplete schema statement")
    if not result:
        raise SchemaProofError("empty schema projection")
    return result


def _role_name(role: Any) -> str | None:
    return role.rolename if isinstance(role, ast.RoleSpec) else None


def _managed_statement(statement: str) -> bool:
    try:
        parsed = parse_sql(statement)
    except Exception:
        raise SchemaProofError("unsupported schema syntax") from None
    if len(parsed) != 1:
        raise SchemaProofError("ambiguous schema statement")
    node = parsed[0].stmt
    if isinstance(node, ast.CreateRoleStmt) and node.role in _MANAGED:
        if node.stmt_type != enums.RoleStmtType.ROLESTMT_ROLE or any(
            option.defname not in _ATTRIBUTES for option in node.options or ()
        ):
            raise SchemaProofError("unsupported managed role creation")
        return True
    if isinstance(node, ast.AlterRoleStmt) and _role_name(node.role) in _MANAGED:
        if node.action != 1 or any(
            option.defname not in _ATTRIBUTES for option in node.options or ()
        ):
            raise SchemaProofError("unsupported managed role alteration")
        return True
    if isinstance(node, ast.AlterRoleSetStmt) and _role_name(node.role) in _MANAGED:
        return True
    if isinstance(node, ast.GrantRoleStmt):
        parents = [role.priv_name for role in node.granted_roles or ()]
        children = [_role_name(role) for role in node.grantee_roles or ()]
        involved = any(role in _MANAGED for role in [*parents, *children])
        if involved:
            if (
                not parents
                or not children
                or any(
                    parent not in _MANAGED and child not in _MANAGED
                    for parent in parents
                    for child in children
                )
            ):
                raise SchemaProofError("mixed managed and unmanaged membership")
            return True
    if isinstance(node, ast.RenameStmt) and node.renameType == enums.ObjectType.OBJECT_ROLE:
        if node.subname in _MANAGED or node.newname in _MANAGED:
            raise SchemaProofError("managed role rename is not an upgrade")
    # Every other statement is retained, even if its strings/names mention a role.
    return False


def schema_proof(dump: str) -> dict[str, Any]:
    """v2: business/global schema excluding strictly recognized managed-role DDL."""
    remaining = [
        statement
        for statement in _statements(dump)
        if statement.startswith("\\connect ") or not _managed_statement(statement)
    ]
    return {"version": 2, "digest": hashlib.sha256("\n".join(remaining).encode()).hexdigest()}


def _binding(binding: dict[str, Any]) -> dict[str, Any]:
    if (
        not isinstance(binding, dict)
        or not {"identity", "reference", "epoch", "volume"} <= binding.keys()
    ):
        raise SchemaProofError("schema bridge binding incomplete")
    if type(binding["epoch"]) is not int or binding["epoch"] < 0:
        raise SchemaProofError("invalid schema bridge epoch")
    if any(
        type(binding[key]) is not str or not binding[key]
        for key in ("identity", "reference", "volume")
    ):
        raise SchemaProofError("invalid schema bridge identity")
    if any(
        not isinstance(key, str)
        or not key
        or (
            key != "epoch"
            and (type(value) not in (str, int) or (isinstance(value, str) and not value))
        )
        for key, value in binding.items()
    ):
        raise SchemaProofError("invalid schema bridge binding")
    return dict(binding)


def _hash(value: Any) -> bool:
    return isinstance(value, str) and _HASH.fullmatch(value) is not None


def _sync_directory(path: Path) -> None:
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _load(path: Path, binding: dict[str, Any]) -> dict[str, Any]:
    try:
        payload = _read_plain_json_file(path)
        stored_binding = payload.get("binding")
        if not isinstance(stored_binding, dict):
            raise SchemaProofError("schema bridge binding missing")
        _binding(stored_binding)
        # A retry cannot accept a rename whose directory durability was uncertain.
        _sync_directory(path)
    except Exception:
        raise SchemaProofError("schema bridge record unavailable") from None
    keys = {"version", "state", "binding", "before_digest", "business_proof"}
    if payload.get("state") == "completed":
        keys.add("after_digest")
    proof = payload.get("business_proof")
    if (
        payload.keys() != keys
        or type(payload.get("version")) is not int
        or payload["version"] != 1
        or payload.get("state") not in ("pending", "completed")
        or payload.get("binding") != binding
        or not _hash(payload.get("before_digest"))
        or not isinstance(proof, dict)
        or proof.keys() != {"version", "digest"}
        or type(proof.get("version")) is not int
        or proof["version"] != 2
        or not _hash(proof.get("digest"))
        or (payload["state"] == "completed" and not _hash(payload.get("after_digest")))
    ):
        raise SchemaProofError("schema bridge record mismatch")
    return payload


def _persist(path: Path, payload: dict[str, Any]) -> None:
    try:
        write_controller_json(path, payload)
        _sync_directory(path)
    except Exception:
        raise SchemaProofError("schema bridge persistence unconfirmed") from None


def begin_schema_bridge(
    path: Path,
    *,
    binding: dict[str, Any],
    accepted_digest: str,
    before_dump: str,
) -> dict[str, Any]:
    """Durably record exact accepted v1 pre-proof before any bootstrap effect.

    Path is private controller state, unique to this transition. Caller owns
    serialization; this API never overwrites a different binding/proof.
    """
    binding = _binding(binding)
    if not _hash(accepted_digest) or legacy_schema_digest(before_dump) != accepted_digest:
        raise SchemaProofError("accepted historical schema differs from live database")
    payload = {
        "version": 1,
        "state": "pending",
        "binding": binding,
        "before_digest": accepted_digest,
        "business_proof": schema_proof(before_dump),
    }
    if path.exists() or path.is_symlink():
        existing = _load(path, binding)
        if (
            existing["before_digest"] != accepted_digest
            or existing["business_proof"] != payload["business_proof"]
        ):
            raise SchemaProofError("schema bridge already belongs to another proof")
        return existing
    _persist(path, payload)
    return payload


def complete_schema_bridge(
    path: Path,
    *,
    binding: dict[str, Any],
    after_dump: str,
    policy_verified: bool,
) -> dict[str, Any]:
    """Commit equivalence only after the caller verifies real role policy.

    A pending record survives bootstrap/post-check failure. Crash recovery may
    rerun trusted idempotent bootstrap while keeping the same fenced identity.
    """
    binding = _binding(binding)
    if policy_verified is not True:
        raise SchemaProofError("project role policy is unverified")
    payload = _load(path, binding)
    if schema_proof(after_dump) != payload["business_proof"]:
        raise SchemaProofError("role reconciliation changed business schema")
    after = legacy_schema_digest(after_dump)
    if payload["state"] == "completed":
        if payload["after_digest"] != after:
            raise SchemaProofError("completed schema bridge is immutable")
        return payload
    completed = {**payload, "state": "completed", "after_digest": after}
    _persist(path, completed)
    return completed


def validate_schema_bridge(
    path: Path,
    *,
    binding: dict[str, Any],
    accepted_digest: str,
    dump: str,
) -> dict[str, Any]:
    """Validate a pending/completed retry *before* any role-bootstrap effects.

    Caller has confirmed quiesce and physical identity. This grants permission
    to rerun trusted reconciliation, not to start guests. A pending record still
    needs complete_schema_bridge and current role-policy verification.
    """
    payload = _load(path, _binding(binding))
    if not _hash(accepted_digest) or payload["before_digest"] != accepted_digest:
        raise SchemaProofError("schema bridge historical proof mismatch")
    if payload["business_proof"] != schema_proof(dump):
        raise SchemaProofError("schema bridge current business proof mismatch")
    return payload


def resolve_schema_proof(
    *,
    accepted_digest: str,
    binding: dict[str, Any],
    dump: str,
    receipt_path: Path,
) -> dict[str, Any]:
    """Resolve a historical hash only from an exact observation or completed bridge.

    This proves schema compatibility, never execution readiness or credential
    hygiene. Caller must independently verify the current managed-role policy.
    """
    binding = _binding(binding)
    if not _hash(accepted_digest):
        raise SchemaProofError("invalid historical schema digest")
    proof = schema_proof(dump)
    if receipt_path.exists() or receipt_path.is_symlink():
        receipt = _load(receipt_path, binding)
        if (
            receipt["state"] != "completed"
            or receipt["before_digest"] != accepted_digest
            or receipt["business_proof"] != proof
        ):
            raise SchemaProofError("schema bridge cannot accept this observation")
    elif legacy_schema_digest(dump) != accepted_digest:
        raise SchemaProofError("historical schema has no observed bridge")
    return proof
