"""Auto stack-routing (P1 — owner directive 2026-06-09, last mile).

Progressive discovery (``services/discovery``) recommends a stack when it decides
to build. Until now that recommendation only rode along in the brief text — the
project stayed ``static`` no matter what, so an "интернет-магазин помады" still
came out as a flat HTML page. This module closes the gap: when discovery picks a
*container* stack for a still-static project, we

  1. flip ``project.template`` to that stack,
  2. re-scaffold the project's git from the matching template (so the generator
     works from the right starter — empty for ``nextjs_entities``, real files for
     ``fullstack`` — exactly like a project created with that template), and
  3. ask the orchestrator to provision the dev container (warm it up so the
     post-build ``hot_reload`` has a live target).

Two public calls keep the rule set + orchestrator wiring hidden behind a trivial
surface (R-01): :func:`switch_to_stack` (DB + git, run in the request handler so
the build is spawned off the right snapshot) and :func:`ensure_provisioned` (the
slow container start, run inside the build worker so it warms in parallel with
generation). Both are fail-soft (R-10): a provisioning hiccup never blocks the
build — the snapshot still lands in git and the user can hit «Запустить».
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from uuid import UUID

from omnia_api.models.project import Project
from omnia_api.models.snapshot import Snapshot
from omnia_api.schemas.project import is_fullstack, orchestrator_template
from omnia_api.services import orchestrator_client
from omnia_api.services import repo as repo_svc

log = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

# Discovery emits its own small stack vocabulary (``services/discovery._STACKS``).
# Map it onto the project ``template`` values. ``static`` has no entry — it means
# "leave the project as the static template it already is". ``spa`` →
# ``vite-react-spa`` (the no-backend interactive escape hatch, Phase 7.2);
# ``orchestrator_template`` + ``is_fullstack`` already treat ``spa`` as a
# container stack, so the only wiring it needed was this discovery→template entry.
_DISCOVERY_STACK_TO_TEMPLATE: dict[str, str] = {
    "fullstack": "fullstack",
    "nextjs_entities": "nextjs_entities",
    "spa": "spa",
    # `code` (owner 2026-06-18) — language-agnostic source. NOT container-backed
    # (``is_fullstack``/``orchestrator_template`` omit it) and it has NO scaffold
    # dir under ``templates/`` — the writer creates every file into the project's
    # existing (blank) git. ``switch_to_stack`` flips the template without a
    # re-scaffold for it; ``ensure_provisioned`` no-ops (no container).
    "code": "code",
}


def discovery_stack_to_template(stack: str) -> str | None:
    """Map a discovery stack id to a project ``template`` value.

    Returns the container template name for ``fullstack`` / ``nextjs_entities`` /
    ``spa``, or ``None`` for ``static`` / anything unknown (→ no switch needed).
    """
    return _DISCOVERY_STACK_TO_TEMPLATE.get((stack or "").strip().lower())


async def switch_to_stack(
    session: object, project: Project, stack: str
) -> UUID | None:
    """Switch a still-static project to the container stack discovery picked.

    Flips ``project.template``, re-scaffolds the project's git from the matching
    template, and replaces the starter snapshot — leaving the project in exactly
    the state it would have had if the user had picked that template up front.

    Idempotent: returns ``None`` (no change) when the stack is static/unknown or
    the project is already a container stack. On success returns the **new
    starter snapshot id** so the caller spawns the build off the right scaffold.

    The session is committed here (the new template + snapshot must be durable
    before the background build reads them).
    """
    target = discovery_stack_to_template(stack)
    if target is None:
        return None  # static — nothing to switch
    if is_fullstack(project.template):
        return None  # already a container stack — leave it be (idempotent)

    # `templates/<target>` may not exist on disk for some stacks (nextjs_entities,
    # spa, code have no api-side scaffold dir). That is fine: `repo_svc.init_repo`
    # treats a missing dir as an EMPTY starter repo — exactly what `code` wants
    # (blank git; the writer authors every file). So `code` flows through the same
    # path as entities/spa: flip template, init (empty) repo, new starter snapshot.
    template_dir = TEMPLATES_DIR / target
    # pygit2 + MinIO upload are blocking — keep them off the event loop.
    commit_sha = await asyncio.to_thread(
        repo_svc.init_repo, project.id, template_dir, target
    )

    snapshot = Snapshot(
        project_id=project.id,
        commit_sha=commit_sha,
        prompt_text=None,
        model_id=None,
        parent_id=None,
    )
    session.add(snapshot)  # type: ignore[attr-defined]
    await session.flush()  # type: ignore[attr-defined]

    project.template = target
    project.current_snapshot_id = snapshot.id
    await session.commit()  # type: ignore[attr-defined]
    await session.refresh(snapshot)  # type: ignore[attr-defined]

    log.info(
        "stack_routing: project %s switched static→%s (snapshot %s)",
        project.id,
        target,
        snapshot.id,
    )
    return snapshot.id


async def pivot_code_to_web(session: object, project: Project) -> bool:
    """Pivot a `code` project to a runnable WEB page (owner 2026-06-19).

    A `code` project (language-agnostic source) has NO live preview. When the user
    asks on a FOLLOW-UP to run it as a web page ("сделай веб-вид", "в браузере",
    "запусти здесь"), flip its template to `static` — a self-contained ``index.html``
    served at ``/p/<slug>`` (instant preview, no container). Non-destructive: the
    existing source files stay in git (so the next build can PORT the logic to the
    page, and the old code is still in the timeline) — we only change the template.
    The static build adds ``index.html`` on top; the workspace then renders the live
    page instead of the "это код-проект" panel.

    Returns True when it flipped, False when the project wasn't `code` (no-op).
    Commits so the background build reads the new template.
    """
    if project.template != "code":
        return False
    project.template = "static"
    await session.commit()  # type: ignore[attr-defined]
    log.info(
        "stack_routing: project %s pivoted code→static (runnable web preview)",
        project.id,
    )
    return True


async def ensure_provisioned(project_id: UUID, slug: str, template: str) -> bool:
    """Provision the project's orchestrator dev container if the stack needs one.

    No-op (returns ``False``) for static templates. For container templates calls
    the orchestrator's idempotent ``provision`` (safe to call when the container
    already exists). Fail-soft: any orchestrator error is logged and swallowed —
    the build proceeds and the post-build ``hot_reload`` (or a manual «Запустить»)
    retries. Returns ``True`` when provisioning was attempted and succeeded.
    """
    orch_template = orchestrator_template(template)
    if orch_template is None:
        return False  # static — no container
    try:
        await orchestrator_client.provision(
            project_id=project_id,
            slug=slug,
            template=orch_template,
            tier="free",
        )
        log.info("stack_routing: provisioned %s (%s)", project_id, orch_template)
        return True
    except Exception as exc:
        log.warning(
            "stack_routing: provision failed for %s (%s) — build continues: %r",
            project_id,
            orch_template,
            exc,
        )
        return False


__all__ = [
    "discovery_stack_to_template",
    "ensure_provisioned",
    "pivot_code_to_web",
    "switch_to_stack",
]
