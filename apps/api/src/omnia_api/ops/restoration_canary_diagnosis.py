"""Name the layer a red MAX canary is broken at — or admit it is not known.

A canary that answers 502 can mean four different things, and the production smoke
cannot tell them apart on its own: the route may no longer exist, nginx may have an
unreachable upstream, the application may be down, or — worst — the webhook may be
answering without authentication. In September 2026 the smoke stayed red for ten days
because the monitored URL pointed at a project that had been deleted, and nobody could
see that from the result.

This classifier takes one observation per hop and returns a verdict. It never guesses:
an incomplete set of observations is reported as ``unknown``, which the runbook treats
as "do not attempt a fix". Nothing from the observations travels into the verdict except
HTTP status codes and the agreed booleans — no URL, header, body or environment value.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any, Literal

Layer = Literal["external_route", "nginx", "runtime", "auth", "none", "unknown"]

_HOPS = ("external", "nginx", "runtime")
_EDGE_HOPS = ("external", "nginx")
# The webhook must refuse an unsigned request; these are the only honest answers.
_WEBHOOK_DENIED = frozenset({401, 403})
_ABSENT = object()


def _code(observation: Mapping[str, Any], key: str) -> int | None:
    value = observation.get(key)
    return value if type(value) is int and 100 <= value <= 599 else None


def _normalize(observations: object) -> dict[str, dict[str, Any]] | None:
    """Keep only what a verdict may depend on; refuse anything malformed."""
    if not isinstance(observations, Sequence) or isinstance(observations, (str, bytes)):
        return None
    normalized: dict[str, dict[str, Any]] = {}
    for item in observations:
        if not isinstance(item, Mapping):
            return None
        hop = item.get("hop")
        if hop not in _HOPS or hop in normalized:
            return None
        entry: dict[str, Any] = {"hop": hop}
        for key in ("health", "webhook"):
            code = _code(item, key)
            if code is not None:
                entry[key] = code
        # Presence facts are recorded as booleans only when the caller actually
        # looked: a missing key means "not observed", not "absent".
        for key in ("server_block", "container", "publication"):
            raw = item.get(key, _ABSENT)
            if raw is not _ABSENT:
                entry[f"{key}_present"] = bool(raw)
        expected, observed = item.get("expected_project"), item.get("observed_project")
        if expected is not None and observed is not None:
            entry["binding_matches"] = str(expected) == str(observed)
        normalized[str(hop)] = entry
    return normalized or None


def _verdict(
    layer: Layer,
    reason_code: str,
    normalized: Mapping[str, Mapping[str, Any]],
    release_sha: str | None,
) -> dict[str, object]:
    codes = {
        hop: {key: entry[key] for key in ("health", "webhook") if key in entry}
        for hop, entry in normalized.items()
    }
    canonical = json.dumps(
        [normalized[hop] for hop in sorted(normalized)], sort_keys=True, separators=(",", ":")
    )
    return {
        "layer": layer,
        "reason_code": reason_code,
        "observed_codes": {hop: value for hop, value in codes.items() if value},
        "release_sha": release_sha,
        "evidence_digest": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "healthy": layer == "none",
    }


def diagnose_canary(
    observations: Sequence[Mapping[str, Any]],
    *,
    release_sha: str | None = None,
) -> dict[str, object]:
    """Classify a canary failure from per-hop observations.

    ``observations`` carries at most one entry per hop (``external``, ``nginx``,
    ``runtime``) with the HTTP codes of the health and unauthenticated webhook probes,
    and optionally whether a matching nginx server block, container or publication was
    found and which project the route is bound to.
    """
    normalized = _normalize(observations)
    if normalized is None:
        return _verdict("unknown", "malformed_observations", {}, release_sha)

    # A route that serves somebody else's project is never "working", whatever it answers.
    if any(entry.get("binding_matches") is False for entry in normalized.values()):
        return _verdict("unknown", "foreign_route_binding", normalized, release_sha)

    # An unauthenticated webhook that is ACCEPTED outranks every availability question.
    # Only a 2xx is an acceptance: a redirect means the request never reached the app.
    if any(
        entry.get("webhook") is not None and 200 <= entry["webhook"] < 300
        for entry in normalized.values()
    ):
        return _verdict("auth", "webhook_accepts_unauthenticated", normalized, release_sha)

    runtime = normalized.get("runtime")
    edge = next((normalized[hop] for hop in _EDGE_HOPS if hop in normalized), None)
    if runtime is None or edge is None:
        return _verdict("unknown", "insufficient_observations", normalized, release_sha)

    external = normalized.get("external", edge)
    if external.get("health") == 200 and external.get("webhook") in _WEBHOOK_DENIED:
        return _verdict("none", "healthy", normalized, release_sha)

    # Proven absence everywhere the route should exist: the registration is stale, and
    # the fix is a new canary, not a change to nginx or to the application.
    nginx = normalized.get("nginx", {})
    route_absent = nginx.get("server_block_present") is False
    runtime_absent = (
        runtime.get("container_present") is False or runtime.get("publication_present") is False
    )
    if route_absent and runtime_absent:
        return _verdict("external_route", "stale_route_registration", normalized, release_sha)

    runtime_health = runtime.get("health")
    if runtime_health is not None and runtime_health != 200:
        return _verdict("runtime", "runtime_unhealthy", normalized, release_sha)
    if runtime_health == 200:
        return _verdict("nginx", "upstream_unavailable", normalized, release_sha)
    return _verdict("unknown", "insufficient_observations", normalized, release_sha)


__all__ = ["Layer", "diagnose_canary"]
