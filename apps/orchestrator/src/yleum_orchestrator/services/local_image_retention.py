"""Conservative, addressed cleanup of unused Project Cell environment images.

The caller supplies complete current roots, including pending publication source
workspaces. Producers must hold the same workspace lock before publishing an
image or reference. Docker's no-force removal is the final consumer race guard.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import docker  # type: ignore[import-untyped]

from yleum_orchestrator.core.cell_resources import WorkspaceLockTimeout, WorkspaceLockUnavailable
from yleum_orchestrator.services.cell_lock import WorkspaceOperationLock

_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_TAG = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}\Z")
_REGISTRY = re.compile(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?(?::[0-9]{1,5})?\Z")
_CREATED = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.(?P<fraction>[0-9]{1,9}))?(?:Z|[+-][0-9]{2}:[0-9]{2})\Z"
)
_IDENTITY_FIELDS = ("managed", "project_id", "owner_id", "workspace_id", "resource_kind")
_MIN_AGE_SECONDS = 7200


@dataclass(frozen=True, slots=True)
class ImageRoots:
    image_ids: frozenset[str]
    image_refs: frozenset[str]
    busy_workspaces: frozenset[str]


class LocalImageRetentionError(RuntimeError):
    """Incomplete inventory; fixed error codes never include Docker/client payloads."""


class _ImageGone(Exception):
    pass


@dataclass(frozen=True, slots=True)
class _Image:
    image_id: str
    project_id: str
    owner_id: str
    workspace_id: str
    created: datetime
    tags: tuple[str, ...]
    digests: tuple[str, ...]
    labels: tuple[tuple[str, str], ...]


async def _call(function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    # Keep the producer lock until an in-flight daemon request has finished,
    # including when the sweep is cancelled while a removal is in progress.
    request = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(request)
    except asyncio.CancelledError:
        try:
            await asyncio.shield(request)
        except Exception:
            pass
        raise


def _canonical_uuid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(UUID(value)) == value
    except ValueError:
        return False


async def _roots(load_roots: Callable[[], ImageRoots]) -> ImageRoots:
    try:
        value = await _call(load_roots)
    except Exception:
        raise LocalImageRetentionError("local_image_roots_unavailable") from None
    if (
        not isinstance(value, ImageRoots)
        or any(
            not isinstance(items, frozenset)
            or any(not isinstance(item, str) or not item or item.strip() != item for item in items)
            for items in (value.image_ids, value.image_refs, value.busy_workspaces)
        )
        or any(_DIGEST.fullmatch(item) is None for item in value.image_ids)
        or any(not _canonical_uuid(item) for item in value.busy_workspaces)
    ):
        raise LocalImageRetentionError("local_image_roots_invalid")
    return value


async def _image_ids(client: Any) -> tuple[str, ...]:
    try:
        images = await _call(client.images.list, all=True)
        if not isinstance(images, list):
            raise ValueError
        values = [item.id for item in images]
        if any(not isinstance(item, str) or _DIGEST.fullmatch(item) is None for item in values):
            raise ValueError
        if len(set(values)) != len(values):
            raise ValueError
        return tuple(sorted(values))
    except Exception:
        raise LocalImageRetentionError("local_image_inventory_unavailable") from None


async def _consumers(client: Any) -> frozenset[str]:
    try:
        containers = await _call(client.containers.list, all=True)
        if not isinstance(containers, list):
            raise ValueError
        image_ids: set[str] = set()
        for container in containers:
            attrs = container.attrs
            if not isinstance(attrs, dict):
                raise ValueError
            # List responses contain ImageID; inspected containers use Image.
            image_id = attrs.get("ImageID", attrs.get("Image"))
            if not isinstance(image_id, str) or _DIGEST.fullmatch(image_id) is None:
                raise ValueError
            image_ids.add(image_id)
        return frozenset(image_ids)
    except Exception:
        raise LocalImageRetentionError("local_image_consumers_unavailable") from None


def _references(value: object) -> tuple[str, ...] | None:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        return None
    if len(set(value)) != len(value):
        return None
    return tuple(sorted(value))


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    match = _CREATED.fullmatch(value)
    if match is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        # Docker timestamps have nanoseconds; datetime otherwise truncates the
        # remainder and could shorten the minimum grace at its exact boundary.
        fraction = match["fraction"] or ""
        if any(digit != "0" for digit in fraction[6:]):
            parsed += timedelta(microseconds=1)
        if parsed.tzinfo is not None and parsed.utcoffset() is not None:
            return parsed.astimezone(UTC)
    except (ValueError, OverflowError):
        pass
    return None


def _identity(labels: dict[str, str]) -> dict[str, str] | None:
    # A whole trusted namespace must establish identity. A partial alias may
    # corroborate it, but may never contradict it or assemble a mixed identity.
    identities = []
    for prefix in ("omnia", "yleum"):
        identity = {
            field: labels[f"{prefix}.{field}"]
            for field in _IDENTITY_FIELDS
            if f"{prefix}.{field}" in labels
        }
        if len(identity) == len(_IDENTITY_FIELDS):
            identities.append(identity)
    if not identities:
        return None
    identity = identities[0]
    if (
        identity["managed"] != "true"
        or identity["resource_kind"] != "environment"
        or any(not _canonical_uuid(identity[field]) for field in _IDENTITY_FIELDS[1:4])
        or any(
            labels.get(f"{prefix}.{field}", value) != value
            for field, value in identity.items()
            for prefix in ("omnia", "yleum")
        )
    ):
        return None
    return identity


async def _inspect(client: Any, image_id: str, registry: str) -> _Image | None:
    try:
        image = await _call(client.images.get, image_id)
        attrs = image.attrs
        if image.id != image_id or not isinstance(attrs, dict) or attrs.get("Id") != image_id:
            return None
    except docker.errors.ImageNotFound:
        raise _ImageGone from None
    except Exception:
        raise LocalImageRetentionError("local_image_inspection_unavailable") from None
    config = attrs.get("Config")
    if not isinstance(config, dict):
        return None
    labels = config.get("Labels")
    if not isinstance(labels, dict) or any(
        not isinstance(key, str) or not isinstance(value, str) for key, value in labels.items()
    ):
        return None
    identity = _identity(labels)
    created = _timestamp(attrs.get("Created"))
    tags = _references(attrs.get("RepoTags"))
    digests = _references(attrs.get("RepoDigests"))
    if identity is None or created is None or tags is None or digests is None:
        return None
    repository = f"{registry}/max-app/{identity['project_id']}"
    if any(
        not tag.startswith(repository + ":") or _TAG.fullmatch(tag[len(repository) + 1:]) is None
        for tag in tags
    ) or any(
        not digest.startswith(repository + "@")
        or _DIGEST.fullmatch(digest[len(repository) + 1:]) is None
        for digest in digests
    ):
        return None
    return _Image(
        image_id=image_id,
        project_id=identity["project_id"],
        owner_id=identity["owner_id"],
        workspace_id=identity["workspace_id"],
        created=created,
        tags=tags,
        digests=digests,
        labels=tuple(sorted(labels.items())),
    )


def _digest(reference: str) -> str | None:
    digest = reference.rsplit("@", 1)[-1]
    return digest if _DIGEST.fullmatch(digest) is not None else None


def _protected(image: _Image, roots: ImageRoots) -> bool:
    protected_ids = roots.image_ids | frozenset(
        digest for ref in roots.image_refs if (digest := _digest(ref)) is not None
    )
    references = frozenset((*image.tags, *image.digests, image.image_id))
    return bool(
        image.workspace_id in roots.busy_workspaces
        or references & roots.image_refs
        or references & protected_ids
        or any(_digest(ref) in protected_ids for ref in references)
    )


def _eligible(
    image: _Image, roots: ImageRoots, consumers: frozenset[str], now: datetime, min_age: int,
) -> bool:
    return (
        not _protected(image, roots)
        and image.image_id not in consumers
        and (now - image.created).total_seconds() >= min_age
    )


async def _remove(
    candidate: _Image,
    load_roots: Callable[[], ImageRoots],
    client: Any,
    registry: str,
    now: datetime,
    min_age: int,
) -> str:
    expected = candidate
    changed = False
    for reference in (*candidate.tags, candidate.image_id):
        roots = await _roots(load_roots)
        consumers = await _consumers(client)
        try:
            current = await _inspect(client, candidate.image_id, registry)
        except _ImageGone:
            # Docker may remove the image together with its last addressed tag.
            return "removed" if changed else "preserved"
        if candidate.workspace_id in roots.busy_workspaces:
            return "busy"
        if (
            current is None
            or current != expected
            or _protected(candidate, roots)
            or not _eligible(current, roots, consumers, now, min_age)
        ):
            return "preserved"
        try:
            await _call(client.images.remove, image=reference, force=False, noprune=True)
        except Exception:
            # A refusal is an ordinary preservation outcome. Never include the
            # client's text, retry with force, or invoke a broader deletion API.
            return "refused"
        changed = True
        expected = replace(expected, tags=tuple(tag for tag in expected.tags if tag != reference))
    return "removed"


async def collect_local_images(
    root: Path,
    load_roots: Callable[[], ImageRoots],
    *,
    apply: bool = False,
    min_age_seconds: int = _MIN_AGE_SECONDS,
    client: Any = None,
    now: datetime | None = None,
    registry_host: str = "registry.yleum.ru",
) -> dict[str, int]:
    """Plan by default; apply only exact, revalidated removals under producer locks.

    ``candidates`` counts the initial plan. ``preserved`` counts excluded images
    and apply-time skips/refusals; dry-run candidates remain only in candidates.
    Inventory failures raise a sanitized error and never substitute empty roots.
    """
    if type(min_age_seconds) is not int or min_age_seconds < _MIN_AGE_SECONDS:
        raise ValueError("min_age_seconds must be at least 7200")
    observed_at = now if now is not None else datetime.now(UTC)
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("now must include a timezone")
    if not isinstance(registry_host, str) or _REGISTRY.fullmatch(registry_host) is None:
        raise ValueError("registry_host must be a registry hostname with an optional port")
    stats = dict(scanned=0, candidates=0, removed=0, preserved=0, busy=0, refused=0, errors=0)
    owned = client is None
    try:
        if owned:
            try:
                client = await _call(docker.from_env)
            except Exception:
                raise LocalImageRetentionError("local_image_client_unavailable") from None
        roots = await _roots(load_roots)
        image_ids = await _image_ids(client)
        consumers = await _consumers(client)
        candidates = []
        for image_id in image_ids:
            stats["scanned"] += 1
            try:
                image = await _inspect(client, image_id, registry_host)
            except _ImageGone:
                image = None
            if image is None or not _eligible(
                image, roots, consumers, observed_at, min_age_seconds,
            ):
                stats["preserved"] += 1
            else:
                candidates.append(image)
        stats["candidates"] = len(candidates)
        if apply and candidates:
            try:
                operation_lock = WorkspaceOperationLock(root, acquire_timeout_seconds=0.1)
            except Exception:
                raise LocalImageRetentionError("local_image_lock_unavailable") from None
            for candidate in candidates:
                try:
                    async with operation_lock.hold(UUID(candidate.workspace_id)):
                        outcome = await _remove(
                            candidate, load_roots, client, registry_host,
                            observed_at, min_age_seconds,
                        )
                except WorkspaceLockTimeout:
                    outcome = "busy"
                except WorkspaceLockUnavailable:
                    outcome = "errors"
                if outcome != "removed":
                    stats["preserved"] += 1
                if outcome != "preserved":
                    stats[outcome] += 1
        return stats
    finally:
        if owned and client is not None:
            try:
                await _call(client.close)
            except Exception:
                raise LocalImageRetentionError("local_image_client_close_failed") from None
