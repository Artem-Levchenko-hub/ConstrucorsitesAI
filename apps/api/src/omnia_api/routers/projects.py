import asyncio
import io
import zipfile
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, status
from fastapi.responses import StreamingResponse
from slugify import slugify
from sqlalchemy import delete, or_, select, update
from sqlalchemy.exc import IntegrityError

from omnia_api.core.config import get_settings
from omnia_api.core.crypto import decrypt_strong
from omnia_api.core.deps import (
    CurrentUserDep,
    OptionalUserDep,
    SessionDep,
)
from omnia_api.core.errors import ApiError
from omnia_api.core.minio import preview_public_url
from omnia_api.core.redis import publish_event
from omnia_api.models.custom_domain import CustomDomain
from omnia_api.models.deploy_target import DeployTarget
from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.max_integration import MaxIntegration
from omnia_api.models.message import Message
from omnia_api.models.project import Project
from omnia_api.models.project_cell import (
    ProjectCellCandidate,
    ProjectCellProof,
    ProjectCellWorkspace,
)
from omnia_api.models.snapshot import Snapshot
from omnia_api.models.usage import Usage
from omnia_api.models.wallet_charge import WalletCharge
from omnia_api.schemas.project import (
    ProjectCreate,
    ProjectPublic,
    ProjectUpdate,
    is_fullstack,
)
from omnia_api.schemas.snapshot import snapshot_event_dict
from omnia_api.services import max_client, orchestrator_client
from omnia_api.services import repo as repo_svc
from omnia_api.services.entitlements import assert_can_create_project
from omnia_api.services.max_access import require_max_studio_access
from omnia_api.services.preset_classifier import classify_preset_sync
from omnia_api.services.project_cell_access import admit_new_project_cell
from omnia_api.services.project_cell_deletion import teardown_project_cell
from omnia_api.services.queue import enqueue_preview
from omnia_api.services.run_bundle import build_launchers

_UNTITLED_NAMES = frozenset({"untitled", "новый проект", "проект", "new project"})

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

router = APIRouter(prefix="/api/projects", tags=["projects"])


async def _commit_first_snapshot(session: SessionDep, project: Project, commit_sha: str) -> Project:
    """Commit the flushed project together with its first snapshot, then announce it."""
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
        raise ApiError("conflict", "slug already exists", status.HTTP_409_CONFLICT) from e

    await session.refresh(project)
    await session.refresh(snapshot)

    # For a MAX project the worker returns at once: MAX thumbnails are captured
    # under the generation lease, never by this deferred job.
    await asyncio.to_thread(enqueue_preview, snapshot.id)
    await publish_event(
        project.id,
        "snapshot.created",
        {"snapshot": snapshot_event_dict(snapshot)},
    )

    return project


@router.post("", response_model=ProjectPublic, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreate,
    session: SessionDep,
    current_user: OptionalUserDep,
) -> Project:
    if current_user is None:
        raise ApiError(
            "max_registration_required",
            "Для MAX Studio нужна регистрация",
            status.HTTP_403_FORBIDDEN,
        )
    require_max_studio_access(current_user)
    # Plan limit on the number of apps (402 `entitlement_exceeded` when full).
    await assert_can_create_project(session, current_user.id)
    owner = current_user
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
        preset_id = (
            classify_preset_sync(
                project_name=name_stripped,
                template=payload.template,
                first_prompt=None,
            )
            or None
        )

    project = Project(
        owner_id=owner.id,
        name=payload.name,
        slug=slug,
        template=payload.template,
        design_preset_id=preset_id,
        project_cell_enabled=admit_new_project_cell(owner),
    )
    session.add(project)
    await session.flush()

    template_dir = TEMPLATES_DIR / payload.template
    commit_sha = await asyncio.to_thread(
        repo_svc.init_repo, project.id, template_dir, payload.template
    )

    return await _commit_first_snapshot(session, project, commit_sha)


@router.get("", response_model=list[ProjectPublic])
async def list_projects(
    session: SessionDep, current_user: CurrentUserDep
) -> list[ProjectPublic]:
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
    public_projects: list[ProjectPublic] = []
    for project in projects:
        preview = (
            previews.get(project.current_snapshot_id)
            if project.current_snapshot_id is not None
            else None
        )
        public_projects.append(
            ProjectPublic.model_validate(project).model_copy(
                update={"preview_url": preview}
            )
        )
    return public_projects


@router.get("/{project_id}", response_model=ProjectPublic)
async def get_project(
    project_id: UUID, session: SessionDep, current_user: CurrentUserDep
) -> ProjectPublic:
    project = await session.get(Project, project_id)
    if project is None or project.owner_id != current_user.id:
        raise ApiError("not_found", "project not found", status.HTTP_404_NOT_FOUND)
    preview_url: str | None = None
    if project.current_snapshot_id:
        snap = await session.get(Snapshot, project.current_snapshot_id)
        preview_url = preview_public_url(snap.preview_key) if snap else None
    return ProjectPublic.model_validate(project).model_copy(update={"preview_url": preview_url})


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
    text_files = await asyncio.to_thread(
        repo_svc.read_files, project_id, snap.commit_sha
    )
    if not text_files:
        raise ApiError("not_found", "no files to download", status.HTTP_404_NOT_FOUND)
    # One-click run bundle (owner 2026-06-19 — «скачал → уже играешь»): add a
    # double-click launcher (run.bat/run.sh/run.command + RU instructions) that
    # creates a venv, installs deps and runs the entry point, so a Python/Node
    # project goes from download → running in one more click. `setdefault` so we
    # never clobber a launcher the project already ships. No-op for plain websites.
    for name, launcher_content in build_launchers(text_files).items():
        text_files.setdefault(name, launcher_content)
    files: dict[str, str | bytes] = dict(text_files)
    # Full runnable export (P5): for a CONTAINER stack the git snapshot is only the
    # generated files — overlay the skeleton template UNDER them so the zip is a
    # runnable repo (skeleton + your code + README), generated files winning. Gated
    # + fail-soft (no skeleton on disk → unchanged snapshot-only zip).
    if get_settings().use_full_container_export:
        from omnia_api.schemas.project import orchestrator_template
        from omnia_api.services import project_export

        _orch = orchestrator_template(project.template)
        if _orch:
            files = project_export.build_runnable_export(_orch, files)
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
    # BYO-VPS: назначить/снять цель деплоя. Отличаем «прислали null» (снять,
    # вернуть на наш хостинг) от «поле не прислали» (не трогать) через
    # model_fields_set. Чужую цель назначить нельзя — проверяем владение.
    if "deploy_target_id" in payload.model_fields_set:
        if (
            project.previous_deploy_target_id is not None
            and project.deploy_target_id != payload.deploy_target_id
        ):
            raise ApiError(
                "deploy_target_switch_pending",
                "Сначала опубликуйте проект на уже выбранной цели — затем можно сменить её снова.",
                status.HTTP_409_CONFLICT,
            )
        previous_target = (
            await session.get(DeployTarget, project.deploy_target_id)
            if project.deploy_target_id
            else None
        )
        selected_target: DeployTarget | None = None
        if payload.deploy_target_id is None:
            project.deploy_target_id = None
        else:
            target = await session.get(DeployTarget, payload.deploy_target_id)
            if target is None or target.owner_id != current_user.id:
                raise ApiError(
                    "deploy_target_not_found", "VPS не найден", status.HTTP_404_NOT_FOUND
                )
            if target.verify_status != "ok" or not target.known_host_key or not target.resolved_ip:
                raise ApiError(
                    "deploy_target_not_verified",
                    "Сначала подтвердите ключ сервера и завершите проверку VPS.",
                    status.HTTP_409_CONFLICT,
                )
            selected_target = target
            project.deploy_target_id = target.id
        if previous_target is not None and previous_target.id != project.deploy_target_id:
            project.previous_deploy_target_id = previous_target.id
        expected_ip = (
            str(selected_target.resolved_ip)
            if selected_target is not None
            else get_settings().our_public_ip
        )
        domains = (
            (
                await session.execute(
                    select(CustomDomain).where(CustomDomain.project_id == project.id)
                )
            )
            .scalars()
            .all()
        )
        for domain in domains:
            if domain.expected_ip != expected_ip:
                domain.expected_ip = expected_ip
                domain.dns_status = "pending"
                domain.cert_status = "none"
                domain.verified_at = None
                domain.last_detail = "Цель публикации изменилась — обновите A-запись."
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

    await teardown_project_cell(session, project)

    max_integration = (
        await session.execute(
            select(MaxIntegration).where(MaxIntegration.project_id == project.id)
        )
    ).scalar_one_or_none()
    if max_integration is not None and max_integration.webhook_url:
        try:
            await max_client.unsubscribe(
                decrypt_strong(max_integration.bot_token_enc),
                max_integration.webhook_url,
            )
        except max_client.MaxClientError as exc:
            raise ApiError(
                "max_webhook_failed",
                f"Не удалось отключить webhook MAX: {exc}",
                status.HTTP_502_BAD_GATEWAY,
            ) from exc

    remote_target_ids = {
        target_id
        for target_id in (
            project.deploy_target_id,
            project.previous_deploy_target_id,
        )
        if target_id is not None
    }
    if remote_target_ids:
        for target_id in remote_target_ids:
            target = await session.get(DeployTarget, target_id)
            if target is None or not target.known_host_key or not target.resolved_ip:
                continue
            await orchestrator_client.teardown_remote_project(
                project.id,
                {
                    "host": target.ssh_host,
                    "port": target.ssh_port,
                    "user": target.ssh_user,
                    "auth_type": target.ssh_auth_type,
                    "secret": decrypt_strong(target.ssh_secret_enc),
                    "known_host_key": target.known_host_key,
                    "resolved_ip": target.resolved_ip,
                },
            )
    elif is_fullstack(project.template):
        # Containers/schema/nginx — idempotent teardown. Errors propagate
        # (503/4xx) so the project row survives for a retry, no orphans.
        await orchestrator_client.destroy(project.id, project.slug)

    # Bare-repo tarball in MinIO (idempotent). Snapshots + messages cascade at
    # the ORM layer when the project row goes.
    await asyncio.to_thread(repo_svc.delete_repo, project.id)

    # Keep the immutable billing ledger while breaking every project-owned FK
    # path before PostgreSQL cascades messages and generation runs. Production
    # migrations can order the SET NULL/CASCADE triggers differently from a
    # fresh metadata schema; without this explicit update, deleting a project
    # may try to null one usage reference after another target row is gone.
    project_run_ids = select(GenerationRun.id).where(
        GenerationRun.project_id == project.id
    )
    project_message_ids = select(Message.id).where(Message.project_id == project.id)
    # Cell release evidence (proofs, their results, candidates) references the
    # project's generation runs with ON DELETE RESTRICT on purpose: evidence must
    # never vanish because a run was dropped. Deleting the whole project is the
    # one legitimate case where the evidence goes too, so remove it explicitly
    # here — otherwise PostgreSQL may cascade projects → generation_runs before
    # the workspace cascade has reached the proofs and refuse the delete
    # (seen live 2026-09-22: DELETE /api/projects/{id} → 500 IntegrityError).
    project_workspace_ids = select(ProjectCellWorkspace.id).where(
        ProjectCellWorkspace.project_id == project.id
    )
    evidence_scope = or_(
        ProjectCellCandidate.generation_run_id.in_(project_run_ids),
        ProjectCellCandidate.workspace_id.in_(project_workspace_ids),
    )
    # Candidates may point at each other (expected_accepted_candidate_id, also
    # RESTRICT); break those links before removing the rows.
    await session.execute(
        update(ProjectCellCandidate)
        .where(evidence_scope)
        .values(expected_accepted_candidate_id=None)
        .execution_options(synchronize_session=False)
    )
    await session.execute(
        delete(ProjectCellCandidate)
        .where(evidence_scope)
        .execution_options(synchronize_session=False)
    )
    await session.execute(
        delete(ProjectCellProof)
        .where(
            or_(
                ProjectCellProof.generation_run_id.in_(project_run_ids),
                ProjectCellProof.workspace_id.in_(project_workspace_ids),
            )
        )
        .execution_options(synchronize_session=False)
    )
    await session.execute(
        update(WalletCharge)
        .where(WalletCharge.message_id.in_(project_message_ids))
        .values(message_id=None)
        .execution_options(synchronize_session=False)
    )
    await session.execute(
        update(Usage)
        .where(
            or_(
                Usage.project_id == project.id,
                Usage.run_id.in_(project_run_ids),
                Usage.message_id.in_(project_message_ids),
            )
        )
        .values(project_id=None, run_id=None, message_id=None)
        .execution_options(synchronize_session=False)
    )

    await session.delete(project)
    await session.commit()
