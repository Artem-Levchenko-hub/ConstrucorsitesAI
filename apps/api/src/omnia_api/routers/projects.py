import asyncio
import io
import zipfile
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, Response, status
from fastapi.responses import StreamingResponse
from slugify import slugify
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from omnia_api.core.config import get_settings
from omnia_api.core.deps import (
    CurrentUserDep,
    OptionalUserDep,
    SessionDep,
    set_session_cookie,
)
from omnia_api.core.errors import ApiError
from omnia_api.core.minio import get_exe_object, preview_public_url
from omnia_api.core.redis import publish_event
from omnia_api.core.security import create_access_token
from omnia_api.models.message import Message
from omnia_api.models.project import Project
from omnia_api.models.snapshot import Snapshot
from omnia_api.models.user import User
from omnia_api.models.wallet import Wallet
from omnia_api.schemas.project import (
    ProjectCreate,
    ProjectPublic,
    ProjectUpdate,
    is_fullstack,
)
from omnia_api.services import orchestrator_client
from omnia_api.services import repo as repo_svc
from omnia_api.services.design_presets import PRESETS
from omnia_api.services.run_bundle import build_launchers
from omnia_api.services.fork_recap import build_fork_recap
from omnia_api.services.preset_classifier import classify_preset_sync
from omnia_api.services.queue import enqueue_preview, enqueue_build_exe

_UNTITLED_NAMES = frozenset({"untitled", "новый проект", "проект", "new project"})

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

router = APIRouter(prefix="/api/projects", tags=["projects"])


async def _ensure_anon_user(session: SessionDep, response: Response) -> User:
    """Mint an ephemeral anonymous principal and hand the caller its session.

    Backs the V4.1a anon-project seam: an unauthenticated visitor can create a
    project owned by this row, then keep editing it via the issued cookie, and
    later `claim` it onto a real account. The anon user has no credentials and a
    zero-balance wallet (no free funds given away to a throwaway principal).
    """
    anon = User(email=None, password_hash=None, is_anon=True)
    anon.wallet = Wallet(balance_rub=Decimal("0"))
    session.add(anon)
    await session.flush()
    set_session_cookie(response, create_access_token(anon.id))
    return anon


@router.post("", response_model=ProjectPublic, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreate,
    session: SessionDep,
    response: Response,
    current_user: OptionalUserDep,
) -> Project:
    owner = current_user if current_user is not None else await _ensure_anon_user(
        session, response
    )
    short_id = uuid4().hex[:6]
    base_slug = slugify(payload.name)[:60] or "project"
    slug = f"{base_slug}-{short_id}"

    # Auto-classify design preset from project name if informative.
    # Heuristic-only (sync, no LLM) on hot path — if name is generic ("Untitled")
    # or too short, leave NULL; classifier in routers/messages.py will
    # fill it on the first prompt via Haiku-fallback.
    preset_id: str | None = None
    name_stripped = payload.name.strip()
    if len(name_stripped) > 5 and name_stripped.lower() not in _UNTITLED_NAMES:
        preset_id = classify_preset_sync(
            project_name=name_stripped,
            template=payload.template,
            first_prompt=None,
        ) or None

    project = Project(
        owner_id=owner.id,
        name=payload.name,
        slug=slug,
        template=payload.template,
        design_preset_id=preset_id,
    )
    session.add(project)
    await session.flush()

    template_dir = TEMPLATES_DIR / payload.template
    commit_sha = await asyncio.to_thread(
        repo_svc.init_repo, project.id, template_dir, payload.template
    )

    snapshot = Snapshot(
        project_id=project.id,
        commit_sha=commit_sha,
        prompt_text=None,
        model_id=None,
        parent_id=None,
    )
    session.add(snapshot)
    await session.flush()

    project.current_snapshot_id = snapshot.id

    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        raise ApiError(
            "conflict", "slug already exists", status.HTTP_409_CONFLICT
        ) from e

    await session.refresh(project)
    await session.refresh(snapshot)

    await asyncio.to_thread(enqueue_preview, snapshot.id)
    await publish_event(
        project.id,
        "snapshot.created",
        {
            "snapshot": {
                "id": str(snapshot.id),
                "project_id": str(snapshot.project_id),
                "commit_sha": snapshot.commit_sha,
                "prompt_text": snapshot.prompt_text,
                "model_id": snapshot.model_id,
                "parent_id": str(snapshot.parent_id) if snapshot.parent_id else None,
                "preview_url": preview_public_url(snapshot.preview_key),
                "is_rollback_target": snapshot.is_rollback_target,
                "created_at": snapshot.created_at.isoformat(),
            }
        },
    )

    return project


@router.post("/{project_id}/claim", response_model=ProjectPublic)
async def claim_project(
    project_id: UUID,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> Project:
    """Bind an anonymous-owned project to the authenticated caller (V4.1a).

    The viewer→creator handoff: a visitor builds anonymously, signs up, and
    claims their work. Re-points ``owner_id`` only — snapshots/messages FK the
    project id, so all source rows survive untouched. Idempotent if the caller
    already owns it; a project owned by a *different real* account is 403 (never
    steal an account-bound project).
    """
    project = await session.get(Project, project_id)
    if project is None:
        raise ApiError("not_found", "project not found", status.HTTP_404_NOT_FOUND)
    if project.owner_id == current_user.id:
        return project

    owner = await session.get(User, project.owner_id)
    if owner is None or not owner.is_anon:
        raise ApiError("forbidden", "project is not claimable", status.HTTP_403_FORBIDDEN)

    project.owner_id = current_user.id
    await session.commit()
    await session.refresh(project)
    return project


@router.post(
    "/{project_id}/fork",
    response_model=ProjectPublic,
    status_code=status.HTTP_201_CREATED,
)
async def fork_project(
    project_id: UUID,
    session: SessionDep,
    response: Response,
    current_user: OptionalUserDep,
) -> Project:
    """Zero-signup instant fork — the viral "Remix this" seam (V4.1b).

    A visitor on ``/p/<slug>`` forks the app into their own editable copy with
    one request and zero credentials: the fork gets a distinct ``project_id``,
    an anon owner (or the caller, if already authenticated), a deep-copied git
    repo on its own MinIO key, and the source's HEAD as its first snapshot so it
    is immediately previewable/editable. Editing the fork mutates only the fork
    — the source's repo bytes and rows stay byte-identical (isolation invariant:
    distinct id, deep-copied repo, no shared rows).
    """
    source = await session.get(Project, project_id)
    if source is None:
        raise ApiError("not_found", "project not found", status.HTTP_404_NOT_FOUND)
    return await perform_fork(session, response, source, current_user)


async def perform_fork(
    session: AsyncSession,
    response: Response,
    source: Project,
    current_user: User | None,
) -> Project:
    """Core of the zero-signup fork — shared by the POST ``/fork`` endpoint and
    the same-origin ``GET /p/<slug>/remix`` link (public.py).

    The cross-origin ``fetch`` the in-page CTA uses is blocked from a deployed
    container (different origin + ``SameSite=lax`` cookie), so the container
    viral path needs a top-level-navigation entry that lands on this same logic.
    Resolving the source is the caller's job (by id vs by slug); everything that
    mutates state — minting the anon owner, deep-copying the repo, carrying the
    HEAD snapshot, committing — lives here so the two entrypoints can never
    drift in isolation behaviour.
    """
    owner = current_user if current_user is not None else await _ensure_anon_user(
        session, response
    )

    short_id = uuid4().hex[:6]
    base_slug = slugify(source.name)[:60] or "project"
    slug = f"{base_slug}-{short_id}"

    fork = Project(
        owner_id=owner.id,
        name=source.name,
        slug=slug,
        template=source.template,
        design_preset_id=source.design_preset_id,
        discovery_spec=source.discovery_spec,
        image_gen_enabled=source.image_gen_enabled,
        # V4.9 — the fork's first surface is a byte-copy of the source's HEAD,
        # so the source's floor verdict applies to it verbatim. Carrying the
        # flag makes the viral pool transitively floor-gated: a fork is itself
        # re-shareable only if the app it copied cleared the beauty floor (the
        # V4.7 fork-tree invariant). The fork's own gate re-stamps it on the
        # first re-generation/edit.
        viral_eligible=source.viral_eligible,
        forked_from=source.id,
    )
    session.add(fork)
    await session.flush()

    # Deep-copy the source repo onto the fork's own MinIO key. A later commit on
    # the fork re-uploads only the fork's key → the source stays byte-identical.
    await asyncio.to_thread(repo_svc.duplicate_repo, source.id, fork.id)

    # Carry the source's HEAD as the fork's first snapshot so it is immediately
    # previewable. A NEW row keyed to the fork's project_id → source snapshots
    # are never touched. The preview PNG is immutable, so its key is shared.
    source_head = (
        await session.get(Snapshot, source.current_snapshot_id)
        if source.current_snapshot_id
        else None
    )
    snapshot: Snapshot | None = None
    if source_head is not None:
        snapshot = Snapshot(
            project_id=fork.id,
            commit_sha=source_head.commit_sha,
            prompt_text=source_head.prompt_text,
            model_id=source_head.model_id,
            preview_key=source_head.preview_key,
            parent_id=None,
        )
        session.add(snapshot)
        await session.flush()
        fork.current_snapshot_id = snapshot.id

    # Land the remixer in a WARM workspace (NORTH STAR pillar 4): seed ONE
    # assistant recap that names what they forked, echoes its captured design
    # DNA, and offers one-tap starter edits. Without this the fork has zero chat
    # rows and the client shows the cold generic "Поговорим о вашем сайте" empty
    # state. Pure + LLM-free, so it never adds a build cost or a failure surface.
    # tokens_out=0 (not NULL) keeps the client from treating the seed as a live
    # mid-stream reply; tokens_in stays NULL so no "0 tokens" footer renders.
    preset_name = (
        PRESETS[source.design_preset_id].name
        if source.design_preset_id and source.design_preset_id in PRESETS
        else None
    )
    session.add(
        Message(
            project_id=fork.id,
            role="assistant",
            content=build_fork_recap(source.name, source.discovery_spec, preset_name),
            model_id=None,
            tokens_in=None,
            tokens_out=0,
        )
    )

    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        raise ApiError(
            "conflict", "slug already exists", status.HTTP_409_CONFLICT
        ) from e

    await session.refresh(fork)
    if snapshot is not None:
        await session.refresh(snapshot)
        await publish_event(
            fork.id,
            "snapshot.created",
            {
                "snapshot": {
                    "id": str(snapshot.id),
                    "project_id": str(snapshot.project_id),
                    "commit_sha": snapshot.commit_sha,
                    "prompt_text": snapshot.prompt_text,
                    "model_id": snapshot.model_id,
                    "parent_id": None,
                    "preview_url": preview_public_url(snapshot.preview_key),
                    "is_rollback_target": snapshot.is_rollback_target,
                    "created_at": snapshot.created_at.isoformat(),
                }
            },
        )

    return fork


@router.get("", response_model=list[ProjectPublic])
async def list_projects(
    session: SessionDep, current_user: CurrentUserDep
) -> list[Project]:
    res = await session.execute(
        select(Project)
        .where(Project.owner_id == current_user.id)
        .order_by(Project.created_at.desc())
    )
    projects = list(res.scalars().all())

    # Attach each project's current-snapshot thumbnail in ONE batch query
    # (not N+1) so the projects grid can show a mini preview per card.
    snap_ids = [p.current_snapshot_id for p in projects if p.current_snapshot_id]
    previews: dict[UUID, str | None] = {}
    if snap_ids:
        rows = await session.execute(
            select(Snapshot.id, Snapshot.preview_key).where(Snapshot.id.in_(snap_ids))
        )
        previews = {sid: preview_public_url(key) for sid, key in rows.all()}
    for p in projects:
        p.preview_url = previews.get(p.current_snapshot_id)
    return projects


@router.get("/{project_id}", response_model=ProjectPublic)
async def get_project(
    project_id: UUID, session: SessionDep, current_user: CurrentUserDep
) -> Project:
    project = await session.get(Project, project_id)
    if project is None or project.owner_id != current_user.id:
        raise ApiError("not_found", "project not found", status.HTTP_404_NOT_FOUND)
    if project.current_snapshot_id:
        snap = await session.get(Snapshot, project.current_snapshot_id)
        project.preview_url = preview_public_url(snap.preview_key) if snap else None
    # Transitive remix lineage (V4 #3): resolve the source's name + slug so the
    # workspace remix badge can attribute it ("ремикс <name>") and link to
    # /p/<slug>. A deleted source leaves both None → the badge degrades to a
    # link-less attribution instead of a broken link.
    if project.forked_from:
        source = await session.get(Project, project.forked_from)
        if source is not None:
            project.forked_from_name = source.name
            project.forked_from_slug = source.slug
    return project


@router.get("/{project_id}/download")
async def download_project(
    project_id: UUID, session: SessionDep, current_user: CurrentUserDep
) -> StreamingResponse:
    """Download ALL files of the project's current snapshot as a single .zip.

    Owner directive 2026-06-19: one obvious button, zero thinking — the user gets a
    real archive of their actual code/site (snake.py, requirements.txt, index.html,
    …) straight from git, with no dependence on what the model wrote into the page.
    Owner-scoped (404 for a foreign/unknown project); 404 when nothing's generated
    yet. The zip is built in memory from the committed snapshot (single source of
    truth = git), so it always matches what's live."""
    project = await session.get(Project, project_id)
    if project is None or project.owner_id != current_user.id:
        raise ApiError("not_found", "project not found", status.HTTP_404_NOT_FOUND)
    if project.current_snapshot_id is None:
        raise ApiError("not_found", "nothing generated yet", status.HTTP_404_NOT_FOUND)
    snap = await session.get(Snapshot, project.current_snapshot_id)
    if snap is None:
        raise ApiError("not_found", "snapshot missing", status.HTTP_404_NOT_FOUND)
    files = await asyncio.to_thread(repo_svc.read_files, project_id, snap.commit_sha)
    if not files:
        raise ApiError("not_found", "no files to download", status.HTTP_404_NOT_FOUND)
    # One-click run bundle (owner 2026-06-19 — «скачал → уже играешь»): add a
    # double-click launcher (run.bat/run.sh/run.command + RU instructions) that
    # creates a venv, installs deps and runs the entry point, so a Python/Node
    # project goes from download → running in one more click. `setdefault` so we
    # never clobber a launcher the project already ships. No-op for plain websites.
    for name, content in build_launchers(files).items():
        files.setdefault(name, content)
    # A .zip drops the Unix executable bit, so a double-clicked run.command/run.sh
    # would open in TextEdit on macOS instead of running. Stamp the exec bit on the
    # shell launchers via ZipInfo.external_attr (S_IFREG | mode) << 16.
    _exec_launchers = {"run.sh", "run.command"}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, content in files.items():
            data = content.encode("utf-8") if isinstance(content, str) else content
            info = zipfile.ZipInfo(path)
            info.compress_type = zipfile.ZIP_DEFLATED
            mode = 0o100755 if path in _exec_launchers else 0o100644
            info.external_attr = mode << 16
            zf.writestr(info, data)
    buf.seek(0)
    fname = (project.slug or "project") + ".zip"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.patch("/{project_id}", response_model=ProjectPublic)
async def update_project(
    project_id: UUID,
    payload: ProjectUpdate,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> Project:
    project = await session.get(Project, project_id)
    if project is None or project.owner_id != current_user.id:
        raise ApiError("not_found", "project not found", status.HTTP_404_NOT_FOUND)
    if payload.image_gen_enabled is not None:
        project.image_gen_enabled = payload.image_gen_enabled
    await session.commit()
    await session.refresh(project)
    return project


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: UUID, session: SessionDep, current_user: CurrentUserDep
) -> None:
    """Delete a project the caller owns: tear down its runtime, drop its git
    repo, then cascade-delete its rows.

    Owner-scoping: a missing project is 404; someone else's project is 403 (the
    caller is authenticated, so we can tell them it's simply not theirs).

    Order is teardown-first and fail-closed (R-10): for a container-backed
    project we ask the orchestrator to remove the container + archive the schema
    *before* deleting the DB row. If the orchestrator is unreachable we raise
    rather than delete — better a retryable error than an orphaned container
    still serving the user's data. The orchestrator side is idempotent, so the
    retry is safe.
    """
    project = await session.get(Project, project_id)
    if project is None:
        raise ApiError("not_found", "project not found", status.HTTP_404_NOT_FOUND)
    if project.owner_id != current_user.id:
        raise ApiError("forbidden", "not your project", status.HTTP_403_FORBIDDEN)

    if is_fullstack(project.template):
        # Containers/schema/nginx — idempotent teardown. Errors propagate
        # (503/4xx) so the project row survives for a retry, no orphans.
        await orchestrator_client.destroy(project.id, project.slug)

    # Bare-repo tarball in MinIO (idempotent). Snapshots + messages cascade at
    # the ORM layer when the project row goes.
    await asyncio.to_thread(repo_svc.delete_repo, project.id)

    await session.delete(project)
    await session.commit()


@router.post("/{project_id}/build-exe", status_code=status.HTTP_202_ACCEPTED)
async def build_exe_endpoint(
    project_id: UUID,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> dict:
    """Enqueue a Windows .exe + NSIS installer build from the project's current snapshot.

    Owner-scoped (404 for missing / foreign projects). Requires at least one ``.py``
    file in the committed snapshot (exe build is Python-only). Returns a ``build_id``
    that the client uses to track progress via SSE (``exe.stage`` / ``exe.ready`` /
    ``exe.failed`` events on the project channel).

    Gated by ``use_exe_build`` (default False) — returns 404 when the feature flag
    is off so the endpoint is invisible to the public API surface until the
    omnia-exe-builder sidecar is present on the host.
    """
    settings = get_settings()
    if not settings.use_exe_build:
        raise ApiError("not_found", "feature not enabled", status.HTTP_404_NOT_FOUND)

    project = await session.get(Project, project_id)
    if project is None or project.owner_id != current_user.id:
        raise ApiError("not_found", "project not found", status.HTTP_404_NOT_FOUND)
    if project.current_snapshot_id is None:
        raise ApiError("not_found", "nothing generated yet", status.HTTP_404_NOT_FOUND)

    snap = await session.get(Snapshot, project.current_snapshot_id)
    if snap is None:
        raise ApiError("not_found", "snapshot missing", status.HTTP_404_NOT_FOUND)

    files: dict[str, str] = await asyncio.to_thread(
        repo_svc.read_files, project_id, snap.commit_sha
    )
    if not any(p.endswith(".py") for p in files):
        raise ApiError(
            "bad_request",
            "exe build is Python-only: no .py files in the current snapshot",
            status.HTTP_400_BAD_REQUEST,
        )

    build_id = str(uuid4())
    await asyncio.to_thread(
        enqueue_build_exe, project.id, build_id, project.slug, files
    )
    return {"build_id": build_id}


@router.get("/{project_id}/exe/{build_id}/{artifact}")
async def download_exe_artifact(
    project_id: UUID,
    build_id: str,
    artifact: str,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> StreamingResponse:
    """Stream a built exe artifact to the owner (owner-scoped download).

    ``artifact`` must be ``setup`` (the NSIS installer, ``<Name>-Setup.exe``)
    or ``exe`` (the bare portable executable, ``<Name>.exe``). Returns 404 for
    a foreign/unknown project, an unknown artifact type, or a build_id whose
    artifacts haven't been uploaded yet (build still running or failed).
    """
    project = await session.get(Project, project_id)
    if project is None or project.owner_id != current_user.id:
        raise ApiError("not_found", "project not found", status.HTTP_404_NOT_FOUND)

    if artifact not in ("setup", "exe"):
        raise ApiError("not_found", "unknown artifact type", status.HTTP_404_NOT_FOUND)

    result = await asyncio.to_thread(
        get_exe_object, str(project_id), build_id, artifact
    )
    if result is None:
        raise ApiError("not_found", "artifact not found", status.HTTP_404_NOT_FOUND)

    stream, _ = result
    filename_suffix = "-Setup.exe" if artifact == "setup" else ".exe"
    fname = (project.slug or "app") + filename_suffix
    return StreamingResponse(
        stream,
        media_type="application/vnd.microsoft.portable-executable",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )
