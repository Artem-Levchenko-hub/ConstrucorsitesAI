from __future__ import annotations

import logging
from typing import Any

from omnia_api.core.config import get_settings
from omnia_api.services import pipeline_debug
from omnia_api.services.generation.contracts import (
    GenerationIds,
    ProjectGenerationFacts,
    SourceBaseline,
)
from omnia_api.services.generation.progress import MessageStream
from omnia_api.services.generation.stream_attempt import ModelStream
from omnia_api.services.prompt_builder import build_messages

_log = logging.getLogger("omnia_api.routers.messages")


def prepare_model_stream(
    *,
    baseline: SourceBaseline,
    force_model: str | None,
    history_serialized: list[dict[str, str]],
    ids: GenerationIds,
    model_id: str,
    orchestrate: bool,
    project_info: ProjectGenerationFacts,
    prompt_text: str,
    pub: MessageStream,
    selected_elements: list[dict[str, Any]] | None,
    surgical: bool,
) -> ModelStream:
    messages = build_messages(
        baseline.files,
        history_serialized,
        prompt_text,
        project_info.template,
        selected_elements,
        preset_id=project_info.design_preset_id,
        image_gen_enabled=project_info.image_gen_enabled,
        # Stable seed for the `ui-ux-pro-max` UX-guidelines sample —
        # re-prompts inside one project always surface the same rules.
        project_id=str(ids.project_id),
        # Phase F.2 — flows the model id to the prompt assembler so it
        # trims detail-polish blocks for budget/balanced tiers. Budget
        # single-shot (multipass disabled) gets ~6 KB shorter prompt;
        # balanced gets ~3 KB shorter; premium keeps the full brief.
        model_id=model_id,
        # Lean edit-only prompt (preserve everything, surgical <edit>) when
        # the triage routed this to a cheap targeted edit.
        edit_mode=surgical,
        # V2.5c — generation-side of the chip→design causality bridge: the
        # persisted onboarding chip choices steer the writer's palette /
        # theme / sections (the gauntlet already JUDGES against this spec).
        discovery_spec=project_info.discovery_spec,
        # Phase A3 — language parameterisation.  "ru" is the default;
        # non-RU injects a top-of-prompt OVERRIDE so all content is
        # generated in the project language instead of Russian.
        language=project_info.language,
        # B4 — imported repos use a stack-neutral generic edit identity
        # instead of the Omnia static/Next.js-specific ones.
        is_imported=project_info.is_imported,
        project_memory_context=project_info.memory_context + project_info.restoration_context,
    )
    print(f"[PP] messages_built count={len(messages)} surgical={surgical}", flush=True)

    # Phase 11 — resolve the generation mode ONCE from the routing model so
    # the prompt we built (freeform vs catalog) and the way we parse the
    # answer below never disagree. The empty-response fallback may switch
    # the model later, but the mode is fixed by the first pass's prompt.
    from omnia_api.core.config import generation_mode as _generation_mode

    _gen_mode = _generation_mode(model_id, str(ids.project_id))
    # Non-web templates never emit a web PageIR. `code` (any-language source)
    # and the Python backends (`tgbot`/`api`) write source via <file> blocks,
    # so force them off catalog mode — must match build_messages' own guard so
    # the prompt we built and the way we parse below agree (owner 2026-06-18).
    _is_code = project_info.template == "code"
    if _gen_mode == "catalog" and project_info.template in ("code", "tgbot", "api"):
        _gen_mode = "freeform"
    print(f"[PP] gen_mode={_gen_mode}", flush=True)
    if pipeline_debug.enabled():
        try:
            _route_settings = get_settings()
            pipeline_debug.dump(
                ids.project_id,
                ids.assistant_message_id,
                "_route.md",
                f"preset_id={project_info.design_preset_id}\n"
                f"gen_mode={_gen_mode}\n"
                f"model_id={model_id}\n"
                f"template={project_info.template}\n"
                f"image_gen_enabled={project_info.image_gen_enabled}\n"
                f"use_art_director_freeform={_route_settings.use_art_director_freeform}\n"
                f"use_visual_enricher={_route_settings.use_visual_enricher}\n"
                f"use_acceptance_gate={_route_settings.use_acceptance_gate}\n"
                f"acceptance_score_only={_route_settings.acceptance_score_only}\n"
                f"acceptance_min_score={_route_settings.acceptance_min_score}\n"
                f"use_section_catalog={_route_settings.use_section_catalog}\n"
                f"use_originality={_route_settings.use_originality}\n"
                f"use_vision_audit={_route_settings.use_vision_audit}\n"
                f"use_design_judge={_route_settings.use_design_judge}\n",
            )
        except Exception as _rt_exc:
            print(f"[PP] debug_route_failed {_rt_exc!r}", flush=True)

    # ──────────────────────────────────────────────────────────────
    # Inner stream loop, extracted so we can retry the whole thing
    # against a fallback model when the primary returns junk.
    # `accumulated` and `usage_data` are mutated through closure refs
    # via the dict trick — Python doesn't let us rebind outer names
    # cleanly from a nested coroutine.
    # ──────────────────────────────────────────────────────────────

    # Phase B.3 — surface the two post-writer freeform stages (Картинки,
    # Проверка) on the SAME llm.pass channel the Art-Director/Writer passes
    # use, so PassProgressBar fills all four segments (Замысел → Вёрстка →
    # Картинки → Проверка) instead of stalling at 2/4. Gated on the freeform
    # build path and best-effort — a progress ping must never abort a build.

    # Phase B — multipass for budget models is ON by default.
    # `effective_multipass_models` = CHEAP_MODELS ∪ env override.
    # First-time user on Haiku/Nano gets the 4-pass enterprise output
    # without any env setup. Operator can ADD non-budget models via
    # `MULTIPASS_MODELS=gpt-5-mini,…` or kill the whole pipeline with
    # `MULTIPASS_MODELS=off`. See services/multipass_generator.py.
    multipass_set = get_settings().effective_multipass_models

    stream = ModelStream(
        ids=ids,
        project_info=project_info,
        prompt_text=prompt_text,
        model_id=model_id,
        force_model=force_model,
        orchestrate=orchestrate,
        generation_mode=_gen_mode,
        messages=messages,
        multipass_set=multipass_set,
        pub=pub,
    )

    return stream
