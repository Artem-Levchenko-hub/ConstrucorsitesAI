"""Wire the pure demo-data generator into the live `records` table.

Called from the `hot_reload` hot-path right after a freshly generated app's
files land in its container. The single worst first impression a generated app
can give is an **empty catalog**: one prompt, a polished dashboard, and then a
blank list screen. This service fills the PUBLIC entity catalogs with realistic
demo rows so the first browse screen the user — and the colleague they share
`/p/<slug>` with — sees is alive (NORTH STAR pillars 1 & 4).

Responsibilities split (R-01 / R-04):
  * ``services.demo_seeder``  — PURE generator: schema → row payloads.
  * ``core.postgres_admin.seed_public_records`` — the DB write (SQL + idempotency).
  * THIS module — the thin policy layer: pick which entities to seed (PUBLIC
    only — owner-scoped rows would filter to ``created_by = me`` and stay
    invisible), generate their rows deterministically, hand them to the writer.

Fail-soft by contract: seeding is a nice-to-have on top of the hot-reload that
delivers the user's app, so any error here is logged and swallowed — a seeding
failure must never turn a successful generation into a 5xx.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any
from uuid import UUID

import structlog

from omnia_orchestrator.core import postgres_admin
from omnia_orchestrator.services import demo_seeder

log = structlog.get_logger("omnia_orchestrator.demo_seed_writer")

_ENTITY_PREFIX = "entities/"
_ENTITY_SUFFIX = ".json"

# PUBLIC rows everyone sees; ADMIN rows the operator's back-office dashboard
# reads. The first signup is now the admin operator (auth.roleForNewUser skips
# the password-less demo owner), so admin entities (Orders/Inquiries) DO show on
# first paint — seeding them keeps the dashboard hero from reading a dead "0 ₽".
# `owner` rows filter per-viewer (an empty "my items" is the right first state),
# so they stay unseeded — demo-owned owner rows would be invisible anyway.
_SEEDABLE_ACCESS = frozenset({"public", "admin"})


def _build_batches(
    project_id: UUID, files: Mapping[str, str], niche: str | None = None
) -> dict[str, list[dict[str, Any]]]:
    """Parse the entity schemas in `files` and generate demo rows for each
    seedable (public + admin) entity. The project id is the deterministic seed, so the same app
    yields byte-identical demo data on every run and machine. `niche` (the app
    slug) lets the seeder pick niche-realistic catalog titles."""
    seed = str(project_id)
    batches: dict[str, list[dict[str, Any]]] = {}
    for path, content in files.items():
        if not (path.startswith(_ENTITY_PREFIX) and path.endswith(_ENTITY_SUFFIX)):
            continue
        try:
            raw = json.loads(content)
        except (ValueError, TypeError):
            continue  # malformed JSON — skip, never raise on the hot-path
        if not isinstance(raw, Mapping):
            continue
        shape = demo_seeder.parse_entity(raw)
        if shape.access not in _SEEDABLE_ACCESS or not shape.fields:
            continue
        count = demo_seeder.row_count(shape.name, seed)
        rows = demo_seeder.generate_rows(
            shape.name, shape.fields, count=count, seed=seed, niche=niche
        )
        if rows:
            batches[shape.name] = rows
    return batches


async def seed_demo_data(
    project_id: UUID, files: Mapping[str, str], niche: str | None = None
) -> dict[str, int]:
    """Fill empty PUBLIC catalogs of a freshly hot-reloaded app with demo rows.

    `niche` is the app slug — a free-text hint that makes catalog titles
    niche-realistic (a pharmacy lists real products, not "Максимум 1").

    Returns ``{entity: inserted_count}`` (empty if nothing was seeded). Never
    raises — failures are logged and swallowed so seeding can't break the
    hot-reload that delivers the user's app.
    """
    try:
        batches = _build_batches(project_id, files, niche)
        if not batches:
            return {}
        inserted = await postgres_admin.seed_public_records(project_id, batches)
        total = sum(inserted.values())
        if total:
            log.info(
                "demo_seed.done",
                project_id=str(project_id),
                inserted=inserted,
            )
        return inserted
    except Exception as exc:  # fail-soft: never break the hot-reload on seeding
        log.warning("demo_seed.failed", project_id=str(project_id), err=str(exc))
        return {}
