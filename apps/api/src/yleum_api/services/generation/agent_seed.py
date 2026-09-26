from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yleum_api.services import agent_builder
from yleum_api.services.generation.contracts import (
    AgentRuntimeBindings,
    GenerationIds,
    GenerationRuntime,
    ProjectGenerationFacts,
)
from yleum_api.services.generation.runtime import (
    _apply_project_cell_preview_files,
    _require_project_cell,
    _split_project_cell_preview_patch,
)
from yleum_api.services.project_cell_errors import raise_if_terminal_cell_error

_log = logging.getLogger("yleum_api.routers.messages")


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
) -> tuple[dict[str, str], agent_builder.AgentResult | None]:
    _max_seed_files: dict[str, str] = {}
    # A new MAX project starts from the verified platform CORE with no
    # product page, then ALWAYS continues through the bounded native Google
    # agent below. There is no product UI template to recolour or mistake
    # for a completed application.
    if orchestrate and not _max_has_generated_snapshot:
        try:
            _starter_files = await _render_current_max_starter_files()
            if _design_contract:
                from yleum_api.services.design_plugin import seed_design_memory

                # Part of the existing core seed/snapshot: no extra model
                # call, generation phase, repair or visible version.
                _starter_files = seed_design_memory(_starter_files, _design_contract)
            await _agent_emit(
                "agent.step",
                {
                    "step": 0,
                    "action": "platform_core",
                    "human": "Подготавливаю защищённое ядро MAX",
                    "path": "",
                    "detail": (
                        f"Готовлю {len(_starter_files)} инфраструктурных файлов без "
                        "продуктовой страницы; затем Google AI-агент построит продукт."
                    ),
                    "ok": True,
                },
            )
            # Kit v12 and older left a real root page in the container.
            # Remove it before seeding v13 so a legacy canvas can neither
            # enter model context nor survive a failed first generation.
            _starter_patch = {**_starter_files, "src/app/page.tsx": ""}
            if runtime.coordinator is not None:
                assert runtime.handle is not None
                _starter_writes, _starter_deletes, _ = _split_project_cell_preview_patch(
                    _starter_patch
                )
                await runtime.handle.stage_patch(
                    _starter_writes,
                    _starter_deletes,
                )
            else:
                await _apply_project_cell_preview_files(
                    files=_starter_patch,
                    project_cell_handle=_require_project_cell(runtime.handle),
                )
            _max_seed_files = _starter_files
            if runtime.handle is not None and runtime.handle.is_portable():
                from yleum_api.services.max_project_kit import include_portable_manifest

                _max_seed_files = include_portable_manifest(
                    _starter_files,
                    await runtime.handle.snapshot_files(),
                )
            _starter_build = (
                {"ok": True, "detail": "platform core staged"}
                if runtime.coordinator is not None
                else await bindings.probe_build()
            )
            if _starter_build.get("ok"):
                await _agent_emit(
                    "agent.step",
                    {
                        "step": 0,
                        "action": "build",
                        "human": "Основа готова — запускаю Google AI-агента",
                        "path": "",
                        "detail": (
                            "Ядро MAX собирается чисто; теперь Google AI-агент "
                            "проектирует продукт без готового UI-шаблона."
                        ),
                        "ok": True,
                    },
                )
            else:
                print(
                    "[PP] MAX starter build red; handing to bounded Google agent",
                    flush=True,
                )
        except Exception as _starter_exc:
            raise_if_terminal_cell_error(_starter_exc)
            print(f"[PP] MAX starter preparation skipped: {_starter_exc!r}", flush=True)
            # Never spend a model call against an unverified or legacy UI
            # base. The user can retry after infrastructure recovery without
            # paying for a generation that was unsafe before turn one.
            _agent_res = agent_builder.AgentResult(
                done=False,
                summary=(
                    "Генерация не запускалась: не удалось подготовить чистое ядро "
                    "MAX без продуктового шаблона. Деньги за вызов модели не списаны."
                ),
                files={},
                steps=0,
                stop_reason="core_preparation_failed",
            )
            await _agent_emit(
                "agent.step",
                {
                    "step": 0,
                    "action": "platform_core",
                    "human": "Не удалось подготовить чистое ядро MAX",
                    "path": "",
                    "detail": (
                        "Google AI-агент не запущен, чтобы не тратить деньги на "
                        "генерацию поверх старого шаблона."
                    ),
                    "ok": False,
                },
            )

    return _max_seed_files, _agent_res
