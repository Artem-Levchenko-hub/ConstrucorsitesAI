from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from omnia_api.core.config import get_settings
from omnia_api.core.redis import publish_event
from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.message import Message
from omnia_api.models.project import Project
from omnia_api.models.snapshot import Snapshot
from omnia_api.schemas.project import CONTAINER_BROWSER_TEMPLATES as CONTAINER_NEXT
from omnia_api.services import (
    app_doctor,
    app_errors,
    orchestrator_client,
)
from omnia_api.services import repo as repo_svc
from omnia_api.services.generation.contracts import (
    GenerationIds,
    GenerationRuntime,
)
from omnia_api.services.generation.publication import _snapshot_payload
from omnia_api.services.generation.runtime import (
    _apply_project_cell_preview_files,
    _project_cell_build,
    _project_cell_runtime_check,
)
from omnia_api.services.generation.supervisor import _BACKGROUND_TASKS
from omnia_api.services.project_memory import record_run_artifacts
from omnia_api.services.queue import enqueue_entity_gate, enqueue_preview

if TYPE_CHECKING:
    from omnia_api.services.project_cell_executor import ProjectCellExecutorHandle


async def _probe_compile_errors(
    factory: async_sessionmaker[AsyncSession],
    project_id: UUID,
    assistant_message_id: UUID,
    slug: str,
    project_cell_handle: ProjectCellExecutorHandle | None = None,
) -> None:
    """After a clean hot-reload, poll the dev server for a Next.js compile error,
    then probe the live route for a server-side 5xx, and surface either as a card.

    Turbopack recompiles asynchronously, so we give it a few seconds and poll a
    handful of times (bounded ~9s) for a *compile* error. A compile-clean app can
    still 5xx when the route actually renders (server components run lazily), so
    after the compile check is clean we do one *runtime* probe that GETs the page.
    Runs as a background task so it never delays ``llm.done`` — the card arrives
    later via its own ``app.error`` event. Fully fail-soft (R-10): any
    orchestrator hiccup is swallowed — a missing card is acceptable, a crashed
    build is not.
    """
    if project_cell_handle is not None:
        status, category = await _probe_app_error(
            project_id,
            slug,
            project_cell_handle=project_cell_handle,
        )
        if status is None:
            return
        await app_errors.publish(
            factory,
            project_id,
            assistant_message_id,
            category=cast(app_errors.ErrorCategory, category or "runtime"),
            detail=str(
                status.get("error")
                or status.get("detail")
                or "Project Cell draft runtime returned an error."
            ),
            file=status.get("file"),
        )
        return

    for _ in range(3):
        await asyncio.sleep(3)
        try:
            status = await orchestrator_client.compile_status(project_id, slug=slug)
        except Exception as exc:
            # Probe is best-effort: a missing card is fine, a crashed build isn't.
            print(f"[PP] compile_status probe failed: {exc!r}", flush=True)
            return
        if status.get("ok", True):
            continue  # clean (or still compiling) — keep watching
        await app_errors.publish(
            factory,
            project_id,
            assistant_message_id,
            category="compile",
            detail=status.get("error") or "Next.js не смог скомпилировать приложение.",
            file=status.get("file"),
        )
        return

    # Compile is clean — now force a render and catch a server-side 5xx that
    # lazy per-route compilation would otherwise hide until the user opens it.
    try:
        runtime = await orchestrator_client.runtime_status(project_id, slug=slug)
    except Exception as exc:
        print(f"[PP] runtime_status probe failed: {exc!r}", flush=True)
        return
    if runtime.get("ok", True):
        return
    code = runtime.get("status_code")
    detail = runtime.get("error") or (
        f"Приложение вернуло ошибку сервера (HTTP {code}) при открытии страницы."
        if code
        else "Приложение упало с ошибкой сервера при открытии страницы."
    )
    await app_errors.publish(
        factory,
        project_id,
        assistant_message_id,
        category="runtime",
        detail=detail,
        file=runtime.get("file"),
    )


def _spawn_compile_probe(
    factory: async_sessionmaker[AsyncSession],
    project_id: UUID,
    assistant_message_id: UUID,
    slug: str,
    project_cell_handle: ProjectCellExecutorHandle | None = None,
) -> None:
    """Fire-and-forget the compile probe with a strong reference (see
    ``_BACKGROUND_TASKS``)."""
    task = asyncio.create_task(
        _probe_compile_errors(
            factory,
            project_id,
            assistant_message_id,
            slug,
            project_cell_handle=project_cell_handle,
        )
    )
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)


async def _probe_app_error(
    project_id: UUID,
    slug: str,
    *,
    compile_tries: int = 3,
    sleep: float = 3.0,
    project_cell_handle: ProjectCellExecutorHandle | None = None,
) -> tuple[dict[str, Any] | None, str]:
    """Probe the live dev container for a REAL error → ``(status, category)``.

    Returns ``(None, "")`` when the app is compiling clean AND renders without a
    5xx (or the probe itself was inconclusive — a probe hiccup is never reported
    as "broken"). Otherwise ``(status_dict, "compile"|"runtime")`` where status
    carries ``error`` / ``file``. Mirrors ``_probe_compile_errors`` but RETURNS the
    verdict instead of publishing a card — the self-repair loop drives it."""
    if project_cell_handle is not None:
        build = await _project_cell_build(project_cell_handle)
        if not build.get("ok", True):
            return {
                "error": str(build.get("error") or build.get("detail") or "build failed"),
                "file": build.get("file"),
            }, "compile"
        runtime = await _project_cell_runtime_check(project_cell_handle)
        if runtime.get("ok", True):
            return None, ""
        return {
            "error": str(runtime.get("error") or runtime.get("detail") or "runtime failed"),
            "file": runtime.get("file"),
        }, "runtime"

    for _ in range(max(1, compile_tries)):
        await asyncio.sleep(sleep)
        try:
            st = await orchestrator_client.compile_status(project_id, slug=slug)
        except Exception as exc:
            print(f"[PP] self_repair compile probe failed (inconclusive): {exc!r}", flush=True)
            return None, ""
        if st.get("ok", True):
            continue  # clean or still compiling — keep watching
        return st, "compile"
    try:
        rt = await orchestrator_client.runtime_status(project_id, slug=slug)
    except Exception as exc:
        print(f"[PP] self_repair runtime probe failed (inconclusive): {exc!r}", flush=True)
        return None, ""
    if rt.get("ok", True):
        return None, ""
    return rt, "runtime"


async def _run_app_self_repair(
    project_id: UUID,
    slug: str,
    files: dict[str, str],
    *,
    passes: int,
    on_notice: Callable[[str], Awaitable[None]] | None = None,
    project_cell_handle: ProjectCellExecutorHandle | None = None,
) -> tuple[dict[str, str], dict[str, Any] | None, str]:
    """Bounded «probe → fix → hot-reload → re-probe» loop (Claude-Code verify→fix).

    Returns ``(changed_files, final_error_status, final_category)``:
    * ``changed_files`` — cumulative repaired files (``{}`` if nothing changed);
      the caller commits them as a follow-up snapshot so the fix survives a rebuild.
    * ``final_error_status`` — ``None`` when the app ends up GREEN; otherwise the
      last error status dict (the caller surfaces it as a card — old behaviour).

    Fully fail-soft (R-10): the model giving nothing usable, or any orchestrator
    hiccup, ends the loop and falls back to the card. ``files`` is not mutated."""
    changed: dict[str, str] = {}
    work = dict(files)
    last_status: dict[str, Any] | None = None
    last_category = ""
    applied = 0  # number of fixes actually hot-reloaded

    def _metric(outcome: str, cat: str) -> None:
        # One grep-able efficacy line per build (mirrors the originality[shadow]
        # metric): outcome=clean (no error) | healed (doctor fixed it green) |
        # failed (still broken after N passes / no usable fix). Lets ops measure
        # how often DeepSeek's container builds compile vs need / get auto-healed:
        #   docker logs omnia-prod-worker | grep self_repair_metric
        print(
            f"[PP] self_repair_metric outcome={outcome} passes={applied}/{passes} cat={cat}",
            flush=True,
        )

    for i in range(max(0, passes)):
        status, category = await _probe_app_error(
            project_id,
            slug,
            project_cell_handle=project_cell_handle,
        )
        if status is None:
            # green (or probe inconclusive → don't claim broken). On pass 0 with no
            # fix yet that's a clean build; if we already applied a fix, it healed.
            _metric("healed" if applied else "clean", last_category or "none")
            return changed, None, ""
        last_status, last_category = status, category
        fix = await app_doctor.propose_fix(
            category=category,
            detail=str(status.get("error") or ""),
            file_path=status.get("file"),
            files=work,
        )
        if not fix:
            break  # model gave nothing usable → stop, surface the card
        work.update(fix)
        changed.update(fix)
        try:
            if project_cell_handle is not None:
                await _apply_project_cell_preview_files(
                    project_id=project_id,
                    project_slug=slug,
                    files=fix,
                    project_cell_handle=project_cell_handle,
                )
            else:
                await orchestrator_client.hot_reload(project_id=project_id, slug=slug, files=fix)
        except Exception as exc:
            print(f"[PP] self_repair hot_reload failed: {exc!r}", flush=True)
            break
        applied += 1
        print(f"[PP] self_repair pass={i + 1} fixed={list(fix)} cat={category}", flush=True)
        if on_notice:
            await on_notice(
                f"\n\n*Нашёл ошибку ({category}) — чиню и пересобираю ({i + 1}/{passes})…*\n\n"
            )
    # Final verdict after the last fix recompiled.
    status, category = await _probe_app_error(
        project_id,
        slug,
        project_cell_handle=project_cell_handle,
    )
    if status is None:
        _metric("healed" if applied else "clean", last_category or "none")
        return changed, None, ""
    _metric("failed", last_category or category)
    return changed, (last_status or status), (last_category or category)


_log = logging.getLogger("omnia_api.routers.messages")


async def realize_container_snapshot(
    *,
    factory: async_sessionmaker[AsyncSession],
    ids: GenerationIds,
    files: dict[str, str],
    new_sha: str,
    new_snapshot_id: UUID | None,
    project: Project | None,
    runtime: GenerationRuntime,
    snapshot_model_id: str,
) -> None:
    if project is not None and project.template in CONTAINER_NEXT:
        # Error cards for drizzle / sync failures are a strict improvement
        # over the old italic notice, so they're always on. Only the new,
        # riskier compile probe (extra orchestrator call) is flag-gated.
        probe_compile = get_settings().use_error_cards
        try:
            drizzle_exit = None
            if runtime.handle is not None:
                await _apply_project_cell_preview_files(
                    project_id=ids.project_id,
                    project_slug=project.slug,
                    files=files,
                    project_cell_handle=runtime.handle,
                )
                print("[PP] project_cell preview sync OK", flush=True)
            else:
                hot = await orchestrator_client.hot_reload(
                    project_id=ids.project_id,
                    slug=project.slug,
                    files=files,
                )
                print(
                    f"[PP] hot_reload OK written={hot.get('written')} "
                    f"drizzle={hot.get('drizzle_exit_code', 'n/a')}",
                    flush=True,
                )
                drizzle_exit = hot.get("drizzle_exit_code")
            if drizzle_exit and drizzle_exit not in ("0", "n/a"):
                # Drizzle push failed — surface it, don't fail the prompt.
                await app_errors.publish(
                    factory,
                    ids.project_id,
                    ids.assistant_message_id,
                    category="schema",
                    detail=(
                        hot.get("drizzle_stderr_tail")
                        or f"drizzle-kit push завершился с кодом {drizzle_exit}."
                    ),
                    file="src/lib/db/schema.ts",
                )
            elif int(get_settings().app_self_repair_passes) > 0:
                # App self-repair loop (Claude-Code verify→fix, DARK). Probe
                # the live container for a REAL compile/runtime error, ask the
                # app_doctor model (DeepSeek) to fix it, hot-reload, re-probe —
                # up to N passes — then commit the repaired files as a FOLLOW-UP
                # snapshot so the fix survives a rebuild. Fail-soft: any trouble
                # (or still broken) falls back to surfacing the card as before.
                _sr_passes = int(get_settings().app_self_repair_passes)

                async def _sr_notice(delta: str) -> None:
                    await publish_event(
                        ids.project_id,
                        "llm.chunk",
                        {"message_id": str(ids.assistant_message_id), "delta": delta},
                    )

                try:
                    _repaired, _sr_err, _sr_cat = await _run_app_self_repair(
                        ids.project_id,
                        project.slug,
                        files,
                        passes=_sr_passes,
                        on_notice=_sr_notice,
                        project_cell_handle=runtime.handle,
                    )
                except Exception as _sr_exc:
                    print(f"[PP] self_repair loop failed: {_sr_exc!r}", flush=True)
                    _repaired, _sr_err, _sr_cat = {}, None, ""
                if _repaired and _sr_err is None:
                    # GREEN after repair — commit the fix as a follow-up snapshot
                    # (the first snapshot already shipped; this records the heal
                    # and keeps it rollback-able + in git for the next rebuild).
                    try:
                        files = {**files, **_repaired}
                        _rep_sha = await asyncio.to_thread(
                            repo_svc.commit_files,
                            ids.project_id,
                            files,
                            "AI: авто-починка сборки",
                            new_sha,
                        )
                        async with factory() as _s:
                            _rep_snap = Snapshot(
                                project_id=ids.project_id,
                                commit_sha=_rep_sha,
                                prompt_text="авто-починка сборки",
                                model_id=snapshot_model_id,
                                parent_id=new_snapshot_id,
                            )
                            _s.add(_rep_snap)
                            await _s.flush()
                            _rep_proj = await _s.get(Project, ids.project_id)
                            if _rep_proj is not None:
                                _rep_proj.current_snapshot_id = _rep_snap.id
                            _rep_run = await _s.get(GenerationRun, ids.run_id)
                            if _rep_run is not None:
                                record_run_artifacts(
                                    _rep_run,
                                    snapshot_id=_rep_snap.id,
                                    commit_sha=_rep_sha,
                                    changed_files=list(_repaired),
                                )
                            _rep_msg = await _s.get(Message, ids.assistant_message_id)
                            if _rep_msg is not None:
                                _rep_msg.content = (_rep_msg.content or "") + (
                                    "\n\n*✓ Нашёл и починил ошибку сборки — приложение собирается.*"
                                )
                                _rep_msg.snapshot_id = _rep_snap.id
                            await _s.commit()
                            await _s.refresh(_rep_snap)
                        await asyncio.to_thread(enqueue_preview, _rep_snap.id)
                        await publish_event(
                            ids.project_id,
                            "snapshot.created",
                            {"snapshot": _snapshot_payload(_rep_snap)},
                        )
                        await _sr_notice("\n\n*✓ Починил ошибку — приложение собирается.*\n\n")
                        print(f"[PP] self_repair committed sha={_rep_sha[:8]}", flush=True)
                    except Exception as _rc_exc:
                        print(f"[PP] self_repair commit failed: {_rc_exc!r}", flush=True)
                elif _sr_err is not None:
                    # Still broken after the loop — surface the card (old path).
                    await app_errors.publish(
                        factory,
                        ids.project_id,
                        ids.assistant_message_id,
                        category=cast(
                            app_errors.ErrorCategory,
                            _sr_cat or "compile",
                        ),
                        detail=str(_sr_err.get("error") or "Не удалось собрать приложение."),
                        file=_sr_err.get("file"),
                    )
            elif probe_compile:
                # Files synced cleanly — Turbopack now recompiles async.
                # Probe for a compile error in the background so the card
                # arrives without holding up llm.done.
                _spawn_compile_probe(
                    factory,
                    ids.project_id,
                    ids.assistant_message_id,
                    project.slug,
                    project_cell_handle=runtime.handle,
                )
            # V1.6 16/5 — assert the awwwards COMPOSITION floor (taste +
            # hierarchy) on the LIVE container. Entity apps skip
            # acceptance.evaluate, so this worker job is the ONLY place the
            # pillar-1 beauty floor gets teeth on the dominant entity class.
            # Runs in the worker (the only process that can reach the dev
            # container over the runtime network), not here. Independent of
            # the compile probe — it surfaces its own quality card.
            if (not drizzle_exit or drizzle_exit in ("0", "n/a")) and project.template in (
                "nextjs_entities",
                "fullstack",
            ):
                if get_settings().acceptance_entity_composition_gate:
                    await asyncio.to_thread(
                        enqueue_entity_gate,
                        ids.assistant_message_id,
                        ids.project_id,
                        project.slug,
                    )
        except Exception as hot_exc:
            print(f"[PP] hot_reload failed: {hot_exc!r}", flush=True)
            await app_errors.publish(
                factory,
                ids.project_id,
                ids.assistant_message_id,
                category="runtime",
                title=(
                    "Синхронизация с Project Cell не удалась"
                    if runtime.handle is not None
                    else "Синхронизация с контейнером не удалась"
                ),
                detail=(
                    (
                        "Снапшот сохранён, но preview не синхронизировался с "
                        f"Project Cell draft runtime: {hot_exc}."
                    )
                    if runtime.handle is not None
                    else (
                        "Снапшот сохранён, но файлы не доехали до dev-контейнера: "
                        f"{hot_exc}. Нажми «Запустить» в верхней панели, чтобы поднять "
                        "среду выполнения."
                    )
                ),
                file=None,
                fixable=False,
            )
