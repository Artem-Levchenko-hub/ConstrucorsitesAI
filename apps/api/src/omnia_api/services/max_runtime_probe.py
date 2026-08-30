"""Functional proof for the authenticated MAX preview data plane."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlsplit
from uuid import UUID

import httpx

from omnia_api.services import orchestrator_client

_BOOTSTRAP_PATH = "/api/omnia/preview-session"
_SIGNATURE_RE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
_TIMEOUT = httpx.Timeout(20.0, connect=5.0)


@dataclass(frozen=True)
class MaxRuntimeProbe:
    ok: bool
    detail: str


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


async def probe_max_runtime(
    project_id: UUID,
    project_slug: str,
    *,
    base_url: str | None = None,
) -> MaxRuntimeProbe:
    """Prove signed preview auth plus one tenant-scoped protected DB read.

    Returned details contain status/cause only. The signed URL and session cookie
    never reach logs, model observations, or persisted attestations.
    """

    try:
        payload = await orchestrator_client.create_max_preview_session(project_id)
    except Exception as exc:
        return MaxRuntimeProbe(False, f"preview session unavailable: {type(exc).__name__}")
    bootstrap_url = _valid_bootstrap_url(
        payload,
        project_id=project_id,
        project_slug=project_slug,
        base_url=base_url,
    )
    if not bootstrap_url:
        return MaxRuntimeProbe(False, "preview session returned an invalid signed URL")

    parsed = urlsplit(bootstrap_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False) as client:
            bootstrap = await client.get(bootstrap_url)
            if bootstrap.status_code not in {200, 301, 302, 303, 307, 308}:
                return MaxRuntimeProbe(
                    False,
                    f"signed preview bootstrap failed (HTTP {bootstrap.status_code})",
                )
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
    except httpx.HTTPError as exc:
        return MaxRuntimeProbe(False, f"MAX data-plane request failed: {type(exc).__name__}")

    return MaxRuntimeProbe(True, "signed preview auth and protected MAX data read passed")


__all__ = ["MaxRuntimeProbe", "probe_max_runtime"]
