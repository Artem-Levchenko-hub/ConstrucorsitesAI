"""Functional proof for the authenticated MAX preview data plane."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urljoin, urlsplit
from uuid import UUID

import httpx

from yleum_api.services import orchestrator_client

_BOOTSTRAP_PATH = "/api/omnia/preview-session"
_SIGNATURE_RE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
_TIMEOUT = httpx.Timeout(20.0, connect=5.0)
# Cell drafts compile their authenticated routes on first access. The real
# isolated Next.js cold start exceeded 20s; retain a finite startup budget.
_CELL_STARTUP_TIMEOUT = httpx.Timeout(120.0, connect=5.0)


@dataclass(frozen=True)
class MaxRuntimeProbe:
    ok: bool
    detail: str
    artifact_digest: str | None = None
    artifact_ref: str | None = None


def _valid_bootstrap_url(
    payload: object,
    *,
    project_id: UUID,
    project_slug: str,
    base_url: str | None,
) -> str | None:
    if not isinstance(payload, dict) or not base_url:
        return None
    if str(payload.get("project_id") or "") != str(project_id):
        return None
    raw = str(payload.get("bootstrap_url") or "")
    try:
        parsed = urlsplit(raw)
        expected = urlsplit(base_url)
        parsed_port = parsed.port
        expected_port = expected.port
        hostname = parsed.hostname or ""
        expected_hostname = expected.hostname or ""
    except ValueError:
        return None
    query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
    query = dict(query_pairs)
    expires = query.get("expires", "")
    signature = query.get("signature", "")
    expected_origin = (
        parsed.scheme == expected.scheme
        and hostname == expected_hostname
        and parsed_port == expected_port
    )
    valid = (
        parsed.scheme == "https"
        and bool(hostname)
        and hostname.startswith(f"{project_slug}-dev.")
        and parsed.username is None
        and parsed.password is None
        and parsed_port is None
        and parsed.path == _BOOTSTRAP_PATH
        and not parsed.fragment
        and [key for key, _value in query_pairs] == ["expires", "signature"]
        and expires.isdigit()
        and bool(_SIGNATURE_RE.fullmatch(signature))
        and expected_origin
    )
    return raw if valid else None


# Platform-managed endpoints answer for every generated app, so they prove nothing
# about the product's own data plane.
_MANAGED_PREFIXES = ("/api/omnia/", "/api/max/")
_BUSINESS_PATH_RE = re.compile(r"^/api/[A-Za-z0-9][A-Za-z0-9._~/-]{0,200}$")


def _shape_of(value: object, depth: int = 0) -> object:
    """Describe an answer by its structure only; no value ever leaves this function."""
    if depth > 6:
        return "..."
    if isinstance(value, dict):
        return {str(key): _shape_of(item, depth + 1) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return [_shape_of(value[0], depth + 1)] if value else []
    if value is None:
        return "null"
    return type(value).__name__


async def probe_signed_business_endpoint(
    client: httpx.AsyncClient,
    path: str,
    expected_status: int = 200,
    *,
    expected_origin: str | None = None,
) -> dict[str, object]:
    """Read one business route with the signed owner session and report it safely.

    A green build and a healthy platform endpoint say nothing about whether the
    generated application can serve its own data: in the live failure the schema
    declared nine technical tables, the database had one, and the product route
    answered 500 while everything else was green. This is the missing proof.

    The result carries a status, a reason and a digest of the answer's SHAPE. The
    answer itself — business rows, error text — never travels out of here.
    """
    if (
        _BUSINESS_PATH_RE.fullmatch(path) is None
        or path.startswith(_MANAGED_PREFIXES)
        or "//" in path
    ):
        return {
            "ok": False,
            "status": None,
            "reason": "signed_business_route_invalid",
            "shape_digest": None,
        }
    if expected_origin is not None and str(client.base_url).rstrip("/") != expected_origin.rstrip(
        "/"
    ):
        return {
            "ok": False,
            "status": None,
            "reason": "signed_business_route_foreign_binding",
            "shape_digest": None,
        }
    try:
        response = await client.get(path)
    except httpx.HTTPError:
        return {
            "ok": False,
            "status": None,
            "reason": "signed_business_route_unreachable",
            "shape_digest": None,
        }
    status = response.status_code
    if 300 <= status < 400:
        reason = "signed_business_route_redirected"
    elif status in {401, 403}:
        reason = "signed_business_route_unauthorized"
    elif status != expected_status:
        reason = "signed_business_route_failed"
    else:
        reason = "ok"
    shape_digest: str | None = None
    if reason == "ok":
        try:
            shape = _shape_of(response.json())
        except ValueError:
            shape = "non-json"
        shape_digest = hashlib.sha256(
            json.dumps(shape, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
    return {
        "ok": reason == "ok",
        "status": status,
        "reason": reason,
        "shape_digest": shape_digest,
    }


async def probe_max_cell_runtime(
    preview: orchestrator_client.ProjectCellPreviewSession,
    *,
    path: str = "/",
    fallback_paths: Sequence[str] = (),
    portable_project_id: UUID | None = None,
    expected_epoch: int | None = None,
    proof_key: str | None = None,
    business_path: str | None = None,
) -> MaxRuntimeProbe:
    """Use the validated, lease-scoped cell session without project-runtime fallback.

    ``business_path`` is the product's own data route from the accepted contract. When
    it is given, serving a page is no longer enough: the same signed session must also
    read that route, because a green build with a broken data plane looked identical to
    success in the live failure. It is never a path chosen by the model.
    """
    candidate_paths = (path, *tuple(fallback_paths))
    for candidate_path in candidate_paths:
        if (
            not candidate_path
            or not candidate_path.startswith("/")
            or candidate_path.startswith("//")
            or "\\" in candidate_path
        ):
            return MaxRuntimeProbe(False, "runtime path must be same-origin")
    if portable_project_id is not None and (type(expected_epoch) is not int or expected_epoch < 1):
        return MaxRuntimeProbe(False, "portable proof requires the active lease epoch")
    result = await _probe_signed_runtime(
        preview.bootstrap_url,
        path=path,
        fallback_paths=fallback_paths,
        request_timeout=_CELL_STARTUP_TIMEOUT,
        portable_project_id=portable_project_id,
        expected_epoch=expected_epoch,
    )
    if not result.ok:
        return result
    if business_path is not None:
        parsed = urlsplit(preview.bootstrap_url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        async with httpx.AsyncClient(
            base_url=origin, timeout=_TIMEOUT, follow_redirects=False
        ) as business_client:
            business = await probe_signed_business_endpoint(business_client, business_path)
        if not business["ok"]:
            return MaxRuntimeProbe(
                False,
                f"{business['reason']} (HTTP {business['status']})"
                if business["status"] is not None
                else str(business["reason"]),
            )
    if proof_key is None:
        return result
    if not re.fullmatch(r"[0-9a-f]{64}", proof_key):
        return MaxRuntimeProbe(False, "runtime proof key is invalid")
    artifact_digest = hashlib.sha256(
        json.dumps(
            {
                "proof_key": proof_key,
                "path": path,
                "fallback_paths": list(fallback_paths),
                "project_id": str(portable_project_id),
                "fencing_epoch": expected_epoch,
                "evidence": result.detail,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return MaxRuntimeProbe(
        True,
        result.detail,
        artifact_digest=artifact_digest,
        artifact_ref=f"runtime/sha256/{artifact_digest}",
    )


async def _probe_signed_runtime(
    bootstrap_url: str,
    *,
    path: str | None = None,
    fallback_paths: Sequence[str] = (),
    request_timeout: httpx.Timeout = _TIMEOUT,
    portable_project_id: UUID | None = None,
    expected_epoch: int | None = None,
) -> MaxRuntimeProbe:
    parsed = urlsplit(bootstrap_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    product_detail = ""
    try:
        async with httpx.AsyncClient(timeout=request_timeout, follow_redirects=False) as client:
            if portable_project_id is not None:
                for headers in ({}, {"Cookie": "__Host-max_session=invalid.invalid"}):
                    rejected = await client.get(f"{origin}/__omnia/identity", headers=headers)
                    if rejected.status_code != 401:
                        return MaxRuntimeProbe(
                            False,
                            "portable boundary accepts unauthenticated identity"
                            if 200 <= rejected.status_code < 300
                            else "portable boundary rejection check failed "
                            f"(HTTP {rejected.status_code}; expected 401)",
                        )
            bootstrap = await client.get(bootstrap_url)
            if bootstrap.status_code not in {200, 301, 302, 303, 307, 308}:
                return MaxRuntimeProbe(
                    False,
                    f"signed preview bootstrap failed (HTTP {bootstrap.status_code})",
                )
            if 300 <= bootstrap.status_code < 400:
                location = bootstrap.headers.get("location")
                landing = urlsplit(urljoin(origin + "/", location or ""))
                if (
                    not location
                    or (landing.scheme, landing.netloc) != (parsed.scheme, parsed.netloc)
                ):
                    return MaxRuntimeProbe(
                        False,
                        "signed preview bootstrap escaped its origin",
                    )
            paths = list(
                dict.fromkeys(
                    [candidate for candidate in (path, *tuple(fallback_paths)) if candidate]
                )
            )
            if not paths:
                paths = ["/"]
            failures: list[str] = []
            success_path: str | None = None
            for product_path in paths:
                route = await client.get(f"{origin}{product_path}")
                # A redirect to a managed endpoint is not product evidence.
                valid_status = (
                    200 <= route.status_code < 300
                    if portable_project_id
                    else 200 <= route.status_code < 400
                )
                if valid_status:
                    success_path = product_path
                    break
                failures.append(f"{product_path} -> HTTP {route.status_code}")
            if success_path is None:
                if len(paths) == 1:
                    return MaxRuntimeProbe(
                        False,
                        f"runtime route failed (HTTP {failures[0].rsplit(' ', 1)[-1]})",
                    )
                return MaxRuntimeProbe(
                    False,
                    "runtime routes failed: " + "; ".join(failures),
                )
            if success_path != paths[0]:
                product_detail = f" via {success_path}"
            protected = await client.get(f"{origin}/api/omnia/actions?limit=1")
            if protected.status_code != 200:
                return MaxRuntimeProbe(
                    False,
                    f"protected MAX data read failed (HTTP {protected.status_code})",
                )
            try:
                body: Any = protected.json()
            except ValueError:
                body = None
            if not isinstance(body, dict) or not isinstance(body.get("actions"), list):
                return MaxRuntimeProbe(False, "protected MAX data response is malformed")
            if portable_project_id is not None:
                identity = await client.get(f"{origin}/__omnia/identity")
                try:
                    identity_body = identity.json()
                except ValueError:
                    identity_body = None
                if (
                    identity.status_code != 200
                    or not isinstance(identity_body, dict)
                    or identity_body.get("project_id") != str(portable_project_id)
                    or not isinstance(identity_body.get("user_id"), str)
                    or type(identity_body.get("epoch")) is not int
                    or identity_body["epoch"] != expected_epoch
                ):
                    return MaxRuntimeProbe(False, "portable boundary identity proof failed")
    except httpx.HTTPError as exc:
        return MaxRuntimeProbe(False, f"MAX data-plane request failed: {type(exc).__name__}")

    return MaxRuntimeProbe(
        True,
        "signed preview auth and protected MAX data read passed" + product_detail,
    )


__all__ = ["MaxRuntimeProbe"]
