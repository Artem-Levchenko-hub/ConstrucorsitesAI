from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Collection
from typing import TYPE_CHECKING, Any, NoReturn
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yleum_api.core.errors import ApiError
from yleum_api.services import agent_builder
from yleum_api.services.generation.contracts import (
    AgentRuntimeBindings,
    GenerationIds,
    GenerationRuntime,
    ProjectGenerationFacts,
    SourceBaseline,
)
from yleum_api.services.generation.runtime import (
    _apply_project_cell_preview_files,
    _require_project_cell,
)
from yleum_api.services.project_cell_errors import raise_if_terminal_cell_error
from yleum_api.services.project_cell_proofs import failure_detail_excerpt

if TYPE_CHECKING:
    from yleum_api.services.generation.recovery_execution import ExecutionDriver

_log = logging.getLogger("yleum_api.routers.messages")


async def _verify_max_starter(
    runtime: GenerationRuntime, bindings: AgentRuntimeBindings,
) -> None:
    """Require the real core proof and retain the failed check's safe diagnostic."""
    dimension: object = None
    operation_id: object = None
    detail: object
    if runtime.coordinator is not None:
        proof = await runtime.coordinator.fast_check()
        ok = proof.outcome == "green"
        detail = proof.redacted_detail
        dimension = getattr(proof, "dimension", None)
        operation_id = getattr(proof, "operation_id", None)
    else:
        result = await bindings.probe_build()
        ok = bool(result.get("ok"))
        detail = result.get("detail")
    if ok:
        return

    context = ["MAX platform core build failed"]
    if isinstance(dimension, str) and dimension in {
        "bootstrap", "fast_check", "full_build", "runtime", "release",
    }:
        context.append(f"dimension={dimension}")
    if isinstance(operation_id, UUID):
        context.append(f"operation={operation_id}")
    diagnostic = failure_detail_excerpt(
        detail if isinstance(detail, str) and detail.strip() else "Check returned no diagnostic.",
        max_bytes=3072,
    )
    raise RuntimeError(" ".join(context) + "\n" + diagnostic)


async def stage_owner_fonts(*, factory: async_sessionmaker[AsyncSession], ids: GenerationIds,
                            baseline: SourceBaseline, runtime: GenerationRuntime) -> dict[str, str]:
    from yleum_api.services.font_assets import apply_owner_fonts, message_font_bundles
    from yleum_api.services.generation.retry_request import resolve_request_source

    async with factory() as session:
        source = await resolve_request_source(session, ids=ids, baseline=baseline)
        bundles = await message_font_bundles(
            session, ids.project_id, ids.user_id, source.message_id,
        )
    if not bundles:
        return {}
    if runtime.handle is None:
        raise ApiError('font_target_unavailable',
                       'Для выбранных шрифтов требуется рабочее приложение', 422)
    before = await runtime.handle.snapshot_files()
    after = apply_owner_fonts(before, bundles)
    changed = {path: content for path, content in after.items() if before.get(path) != content}
    if changed:
        await runtime.handle.stage_patch(changed, ())
    # Full materialized font source is retained in prepared entry on cold resume,
    # even when this idempotent retry did not need another physical write.
    return {path: content for path, content in after.items() if (
        path.startswith('src/app/fonts/') or path.startswith('public/fonts/licenses/owner-')
        or path in {'src/app/typography.css', 'src/app/globals.css', 'typography.json', 'DESIGN.md'}
    )}


async def render_current_max_starter(
    *,
    factory: async_sessionmaker[AsyncSession],
    ids: GenerationIds,
    project_info: ProjectGenerationFacts,
    prompt_text: str,
    runtime: GenerationRuntime,
) -> dict[str, str]:
    from yleum_api.models.max_project_config import MaxProjectConfig
    from yleum_api.schemas.max_studio import MaxProjectConfigPayload
    from yleum_api.services.max_project_kit import render_max_starter_files

    async with factory() as _max_session:
        _max_record = await _max_session.get(MaxProjectConfig, ids.project_id)
    _max_config = (
        MaxProjectConfigPayload.model_validate(_max_record.config)
        if _max_record is not None
        else MaxProjectConfigPayload(
            app_name=project_info.name or "MAX Mini App",
            app_type="custom",
            summary=prompt_text[:1000] or "Сервис внутри MAX",
        )
    )
    return render_max_starter_files(
        _max_config,
        ids.project_id,
        portable=bool(runtime.handle is not None and runtime.handle.is_portable()),
    )


def draft_product_test_paths(paths: Collection[str], *, portable: bool) -> set[str]:
    from yleum_api.services.max_project_kit import _template_candidates

    # The seeded package runs direct tests/*.test.mjs, including platform
    # tests the core renderer does not rewrite.
    tests = {
        path for path in paths
        if path.startswith("tests/") and path.count("/") == 1 and path.endswith(".test.mjs")
    }
    if tests:
        for directory in _template_candidates("tests"):
            if directory.is_dir():
                platform_tests = {f"tests/{path.name}" for path in directory.glob("*.test.mjs")}
                if portable:
                    # next_machine_seed excludes this platform image-only test.
                    platform_tests.discard("tests/dependency-install.test.mjs")
                return tests - platform_tests
    return set()


async def _complete_max_starter(
    runtime: GenerationRuntime,
    bindings: AgentRuntimeBindings,
    emit: Callable[[str, dict[str, Any]], Awaitable[None]],
) -> None:
    # Staging source proves neither dependencies nor the database.
    # This check must finish before any model request can spend money.
    await _verify_max_starter(runtime, bindings)
    await emit("agent.step", {
        "step": 0,
        "action": "build",
        "human": "Основа готова — запускаю генератор",
        "path": "",
        "detail": (
            "Проверки ядра MAX пройдены; теперь генератор "
            "проектирует продукт без готового UI-шаблона."
        ),
        "ok": True,
    })


async def _raise_max_starter_failure(
    exc: Exception, emit: Callable[[str, dict[str, Any]], Awaitable[None]],
) -> NoReturn:
    raise_if_terminal_cell_error(exc)
    diagnostic = failure_detail_excerpt(str(exc), max_bytes=3584)
    print(f"[PP] MAX starter preparation skipped: {diagnostic!r}", flush=True)
    await emit("agent.step", {
        "step": 0,
        "action": "platform_core",
        "human": "Не удалось подготовить чистое ядро MAX",
        "path": "",
        "detail": (
            "генератор не запущен, чтобы не тратить деньги на "
            "генерацию поверх старого шаблона.\n\n" + diagnostic
        ),
        "ok": False,
    })
    # Recovering an empty agent result would misclassify a partially staged seed.
    raise ApiError(
        code="runtime_unavailable",
        message=(
            "MAX_CORE_PREPARATION_FAILED: "
            "Генерация не запускалась: не удалось подготовить среду MAX. "
            "Деньги за вызов модели не списаны. Повторите после восстановления среды.\n"
            + diagnostic
        ),
        status_code=503,
    ) from exc


async def verify_max_retry_starter(
    *,
    runtime: GenerationRuntime,
    bindings: AgentRuntimeBindings,
    emit: Callable[[str, dict[str, Any]], Awaitable[None]],
    draft_tests: tuple[str, ...],
    continuation: ExecutionDriver,
) -> None:
    from yleum_api.services.generation.recovery_pipeline import run_bootstrap_stage

    handle = _require_project_cell(runtime.handle)
    if draft_tests:
        # A separate effect keeps historical starter_apply receipts replayable.
        await run_bootstrap_stage(
            continuation, "starter_isolate", lambda: _apply_project_cell_preview_files(
                files=dict.fromkeys(draft_tests, ""),
                project_cell_handle=handle, sync_preview=False,
            ), handle=handle,
        )
    try:
        # Run proof roles separately from the source receipt. A cold source
        # replay must not reserve a fresh role at an older physical ordinal.
        await _complete_max_starter(runtime, bindings, emit)
    except Exception as exc:
        await _raise_max_starter_failure(exc, emit)


async def stage_max_starter(
    *,
    _agent_emit: Callable[[str, dict[str, Any]], Awaitable[None]],
    _agent_res: agent_builder.AgentResult | None,
    _design_contract: Any,
    _max_has_generated_snapshot: bool,
    _render_current_max_starter_files: Callable[[], Awaitable[dict[str, str]]],
    bindings: AgentRuntimeBindings,
    ids: GenerationIds,
    orchestrate: bool,
    project_info: ProjectGenerationFacts,
    runtime: GenerationRuntime,
    new_checkpoint_paths: Collection[str] = (),
    clean_baseline_paths: Collection[str] | None = None,
    defer_verification: bool = False,
) -> tuple[dict[str, str], agent_builder.AgentResult | None]:
    _max_seed_files: dict[str, str] = {}
    # A new MAX project starts from the verified platform CORE with no
    # product page, then ALWAYS continues through the bounded native Google
    # agent below. There is no product UI template to recolour or mistake
    # for a completed application.
    if orchestrate and not _max_has_generated_snapshot:
        try:
            _starter_files = await _render_current_max_starter_files()
            handle = _require_project_cell(runtime.handle)
            portable = handle.is_portable()
            draft_paths = new_checkpoint_paths
            if portable:
                from yleum_api.services.max_project_kit import include_portable_manifest

                workspace_files = await handle.snapshot_files()
                _starter_files = include_portable_manifest(_starter_files, workspace_files)
                if clean_baseline_paths is not None:
                    draft_paths = workspace_files.keys() - clean_baseline_paths
            if _design_contract:
                from yleum_api.services.design_plugin import seed_design_memory

                # Part of the existing core seed/snapshot: no extra model
                # call, generation phase, repair or visible version.
                _starter_files = seed_design_memory(_starter_files, _design_contract)
            # Keep trusted source metadata before sync: a failed runtime build can
            # already have written these files. It must not turn the platform's
            # own seed into an untrusted candidate or remove the portable manifest.
            _max_seed_files = _starter_files
            runtime.migration_baseline = {
                **(runtime.migration_baseline or {}), **_max_seed_files,
            }
            await _agent_emit(
                "agent.step",
                {
                    "step": 0,
                    "action": "platform_core",
                    "human": "Подготавливаю защищённое ядро MAX",
                    "path": "",
                    "detail": (
                        f"Готовлю {len(_starter_files)} инфраструктурных файлов без "
                        "продуктовой страницы; затем генератор построит продукт."
                    ),
                    "ok": True,
                },
            )
            # Kit v12 and older left a real root page in the container.
            # Remove it before seeding v13 so a legacy canvas can neither
            # enter model context nor survive a failed first generation.
            # A fresh build drops draft-only product tests from an earlier
            # failed run. A retry restores its checkpoint after this proof.
            draft_tests = draft_product_test_paths(draft_paths, portable=portable)
            _starter_patch = {**_starter_files, "src/app/page.tsx": ""}
            _starter_patch.update(
                dict.fromkeys(draft_tests - runtime.migration_baseline.keys(), ""),
            )
            await _apply_project_cell_preview_files(
                files=_starter_patch,
                project_cell_handle=handle,
                sync_preview=runtime.coordinator is None,
            )
            if not defer_verification:
                await _complete_max_starter(runtime, bindings, _agent_emit)
        except Exception as exc:
            await _raise_max_starter_failure(exc, _agent_emit)

    return _max_seed_files, _agent_res
