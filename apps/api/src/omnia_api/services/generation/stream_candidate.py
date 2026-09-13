from __future__ import annotations

import logging
from typing import Any

from omnia_api.core.redis import publish_event
from omnia_api.schemas.project import CONTAINER_BROWSER_TEMPLATES as CONTAINER_NEXT
from omnia_api.services import (
    image_edit,
    zone_edit,
)
from omnia_api.services.file_extractor import UnsafePathError
from omnia_api.services.generation.contracts import (
    GenerationIds,
    ProjectGenerationFacts,
    SourceBaseline,
)
from omnia_api.services.generation.file_transforms import (
    _EMPTY_RESPONSE_FALLBACKS,
    _extract_files_and_edits,
    _looks_truncated,
)
from omnia_api.services.generation.stream_attempt import ModelStream
from omnia_api.services.generation.stream_support import _craft_image_prompt
from omnia_api.services.llm_client import stream_chat_completion

_log = logging.getLogger("omnia_api.routers.messages")


async def generate_initial_output(
    *,
    baseline: SourceBaseline,
    force_model: str | None,
    ids: GenerationIds,
    model_id: str,
    project_info: ProjectGenerationFacts,
    prompt_text: str,
    selected_elements: list[dict[str, Any]] | None,
    stream: ModelStream,
    surgical: bool,
) -> tuple[str, dict[str, Any] | None, Any]:
    usage_data: dict[str, Any] | None = None
    _direct_image_edit: str | None = None
    _img_req = image_edit.is_image_request(prompt_text)
    # A "new background" request ("сделай новый фон" / "поменяй фон", no named
    # colour) also routes here — IF the clicked zone actually has a full-bleed
    # bg image to regenerate. The deterministic server-built <edit> avoids the
    # text model reconstructing the (already-changed) hero and missing every
    # SEARCH — exactly the "ничего не сделал" the owner hit on "сделай новый фон".
    _bg_req = image_edit.is_background_request(prompt_text)
    if (
        surgical
        and selected_elements
        and project_info.image_gen_enabled
        and baseline.files.get("index.html")
        and (_img_req or _bg_req)
    ):
        _idx_src = baseline.files["index.html"]
        _z = zone_edit.find_enclosing_block(
            _idx_src, zone_edit.distinctive_anchors(selected_elements)
        )
        _scope = _idx_src[_z[0] : _z[1]] if _z else _idx_src
        _img_hit = image_edit.find_first_img(_scope)
        _bg = _img_hit is not None and image_edit.is_fullbleed_bg(_img_hit[2])
        # A pure background request only takes this path when the target really
        # is a full-bleed bg image; otherwise "поменяй фон" is a colour edit and
        # belongs on the normal edit path.
        if _img_hit is not None and (_img_req or _bg):
            _old_img_tag = _img_hit[2]
            _gp, _gp_usage = await _craft_image_prompt(
                prompt_text,
                project_info.design_preset_id,
                _old_img_tag,
                force_model,
                ids.user_id,
                ids.project_id,
                ids.assistant_message_id,
            )
            _new_img_tag = image_edit.rebuild_img_with_gen(_old_img_tag, _gp)
            _sr_pairs: list[tuple[str, str]] = [(_old_img_tag, _new_img_tag)]
            if _bg:
                # A full-bleed bg image stays invisible behind the heavy dark
                # overlay AND the WebGL shader — lighten the masking gradient(s)
                # and dim the shader in the same zone so the regenerated image
                # is actually seen (text stays readable).
                _sr_pairs.extend(image_edit.lighten_overlay_edits(_scope))
                _sr_pairs.extend(image_edit.dim_shader_edits(_scope))
            _blocks = "".join(
                f"<<<<<<< SEARCH\n{_s}\n=======\n{_r}\n>>>>>>> REPLACE\n" for _s, _r in _sr_pairs
            )
            _direct_image_edit = (
                "Генерирую новое фоновое изображение и осветляю затемнение, чтобы оно было видно.\n"
                if _bg
                else "Генерирую новое изображение для выделенной зоны.\n"
            ) + f'<edit path="index.html">\n{_blocks}</edit>\n'
            if _gp_usage:
                usage_data = _gp_usage
            print(
                f"[PP] direct_image edit built bg={_bg} pairs={len(_sr_pairs)} gp={_gp[:60]!r}",
                flush=True,
            )

    # --- Pass 1: primary model (or the server-built image edit) ---
    if _direct_image_edit is not None:
        accumulated = _direct_image_edit
        stream_error = None
        await publish_event(
            ids.project_id,
            "llm.chunk",
            {
                "message_id": str(ids.assistant_message_id),
                "delta": "*Генерирую изображение для выделенной зоны…*\n\n",
            },
        )
    else:
        await stream.run(model_id, force_all=force_model)
        accumulated = str(stream.last_attempt.text)
        usage_data = stream.last_attempt.usage
        stream_error = stream.last_attempt.error

    return accumulated, usage_data, stream_error


async def render_catalog_output(
    *,
    accumulated: str,
    usage_data: dict[str, Any] | None,
    baseline: SourceBaseline,
    ids: GenerationIds,
    project_info: ProjectGenerationFacts,
    stream: ModelStream,
) -> tuple[str, dict[str, Any] | None]:
    if stream.generation_mode == "catalog" and project_info.template not in CONTAINER_NEXT:
        import json as _json

        from pydantic import ValidationError as _ValidationError

        from omnia_api.sections import PageIR, render_page  # noqa: F401
        from omnia_api.sections.renderer import render_to_files

        raw = accumulated.strip()
        # Strip ```json fences the model may have added despite instructions.
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()
        try:
            ir_dict = _json.loads(raw)
            ir = PageIR.model_validate(ir_dict)
            # Phase L8 — Smart Defaults engine: fill weak/null fields
            # (CTA hrefs → conversion anchor, palette → preset HEX,
            # pricing featured tier, footer copyright with year,
            # favicon per industry, dark_mode coercion, anchor dedup).
            # Pure + idempotent — safe on every IR. preset_id is the
            # classifier's pinned preset for this project.
            from omnia_api.sections import apply_smart_defaults

            ir = apply_smart_defaults(ir, preset_id=project_info.design_preset_id)
            # Reuse the existing omnia-kit on disk for the project's template.
            kit_css = baseline.files.get("src/assets/omnia-kit.css", "")
            kit_js = baseline.files.get("src/assets/omnia-kit.js", "")
            rendered = render_to_files(ir, kit_css=kit_css, kit_js=kit_js)
            # Re-pack into the <file path="..."> format the downstream
            # extractor expects. Order matters: index.html first.
            blocks = [f'<file path="{p}">\n{c}\n</file>' for p, c in rendered.items()]
            accumulated = "\n".join(blocks)
            print(
                f"[PP] catalog_ir_ok sections={len(ir.sections)} "
                f"html_len={len(rendered.get('index.html', ''))}",
                flush=True,
            )
        except (_json.JSONDecodeError, _ValidationError, ValueError) as ir_exc:
            _log.warning(
                "catalog IR parse/validate failed; retrying IR once with the "
                "director model before falling back to freeform: %r",
                ir_exc,
            )
            print(f"[PP] catalog_ir_fail err={ir_exc!r}", flush=True)
            # HARDENING (Phase M) — the polish model (cheap, gpt-5-nano) emitted
            # schema-invalid PageIR. Retry the IR ONCE with the strong director
            # model (Opus reliably holds the strict schema). Not streamed to the
            # user (no raw JSON in chat); on success we swap `accumulated` for the
            # rendered <file> blocks so a real site always ships. The free-gen
            # contextvar rides along, so this retry is not billed on a free gen.
            from omnia_api.core.config import model_for_role as _model_for_role

            try:
                _retry_parts: list[str] = []
                _retry_usage: dict[str, Any] | None = None
                async for _rev in stream_chat_completion(
                    stream.messages,
                    _model_for_role("director"),
                    str(ids.user_id),
                    str(ids.project_id),
                    str(ids.assistant_message_id),
                ):
                    if "delta" in _rev:
                        _retry_parts.append(str(_rev["delta"]))
                    elif "usage" in _rev:
                        _retry_usage = _rev["usage"]
                _raw2 = "".join(_retry_parts).strip()
                if _raw2.startswith("```"):
                    _raw2 = _raw2.split("\n", 1)[1] if "\n" in _raw2 else _raw2[3:]
                    if _raw2.endswith("```"):
                        _raw2 = _raw2[:-3]
                    _raw2 = _raw2.strip()
                _ir2 = PageIR.model_validate(_json.loads(_raw2))
                from omnia_api.sections import apply_smart_defaults as _asd

                _ir2 = _asd(_ir2, preset_id=project_info.design_preset_id)
                # NB: kit_css/kit_js are assigned in the try-block AFTER the
                # validate that just failed, so they're unbound here — fetch
                # them fresh from current_files (always in scope).
                _kit_css = baseline.files.get("src/assets/omnia-kit.css", "")
                _kit_js = baseline.files.get("src/assets/omnia-kit.js", "")
                _rendered2 = render_to_files(_ir2, kit_css=_kit_css, kit_js=_kit_js)
                accumulated = "\n".join(
                    f'<file path="{p}">\n{c}\n</file>' for p, c in _rendered2.items()
                )
                if _retry_usage:
                    usage_data = _retry_usage
                print(
                    f"[PP] catalog_ir_recovered_via_director sections={len(_ir2.sections)}",
                    flush=True,
                )
            except Exception as _ir_exc2:
                print(f"[PP] catalog_ir_retry_failed err={_ir_exc2!r}", flush=True)

    return accumulated, usage_data


async def recover_incomplete_output(
    *,
    accumulated: str,
    baseline: SourceBaseline,
    files: dict[str, str],
    force_model: str | None,
    ids: GenerationIds,
    model_id: str,
    stream: ModelStream,
    usage_data: dict[str, Any] | None,
) -> tuple[dict[str, str], str, dict[str, Any] | None, str]:
    retried_models: list[str] = []
    effective_model = model_id
    if _looks_truncated(accumulated, files):
        # A.5 — retry the *same* model via multipass before switching
        # models. Most "empty response" failures on cheap models are
        # prompt-overwhelm (Haiku/Nano lose focus on a 28+ KB single
        # shot); splitting into 4 narrow passes (skeleton → content
        # → visual → assembly) usually recovers without spending a
        # second model's quota. Skipped when the single shot already
        # ran through multipass (then the failure is the model, not
        # the prompt size — go straight to model-switch fallbacks).
        already_multipass = model_id in stream.multipass_set
        if not already_multipass:
            notice = (
                f"\n\n*Модель `{effective_model}` дала пустой ответ "
                f"({len(accumulated)} симв.). Пробую тот же "
                f"`{effective_model}` в multipass-режиме (4 узких прохода)…*\n\n"
            )
            accumulated = accumulated + notice
            await publish_event(
                ids.project_id,
                "llm.chunk",
                {"message_id": str(ids.assistant_message_id), "delta": notice},
            )
            print(
                f"[PP] same_model_multipass_retry {effective_model}",
                flush=True,
            )
            await stream.run(
                model_id,
                force_multipass=True,
                force_all=force_model,
                allow_art_director=False,
            )
            mp_acc = str(stream.last_attempt.text)
            mp_usage = stream.last_attempt.usage
            mp_err = stream.last_attempt.error
            accumulated = accumulated + mp_acc
            if mp_usage and isinstance(mp_usage, dict):
                usage_data = mp_usage
            retried_models.append(f"{model_id}#multipass")
            if mp_err:
                print(
                    f"[PP] same_model_multipass_error {model_id}: {mp_err!r}",
                    flush=True,
                )
            else:
                try:
                    files, _ = _extract_files_and_edits(accumulated, baseline.files)
                except (UnsafePathError, ValueError):
                    files = {}
                # If multipass of the same model recovered the output
                # there's nothing left to fall back to — skip the
                # model-switch loop entirely.
                if not _looks_truncated(mp_acc, files):
                    if retried_models:
                        model_id = effective_model

        for fb_model in _EMPTY_RESPONSE_FALLBACKS.get(model_id, []):
            # A.5 — if a prior retry (same-model multipass) already
            # recovered the output, don't burn another model's quota.
            if not _looks_truncated(accumulated, files):
                break
            notice = (
                f"\n\n*Модель `{effective_model}` вернула пустой ответ "
                f"({len(accumulated)} символов). Переключаюсь на "
                f"`{fb_model}`…*\n\n"
            )
            # Append to accumulated AND broadcast to UI so user sees what's happening.
            accumulated = accumulated + notice
            await publish_event(
                ids.project_id,
                "llm.chunk",
                {"message_id": str(ids.assistant_message_id), "delta": notice},
            )
            print(f"[PP] empty_fallback {effective_model} -> {fb_model}", flush=True)
            await stream.run(fb_model, force_all=fb_model)
            fb_acc = str(stream.last_attempt.text)
            fb_usage = stream.last_attempt.usage
            fb_err = stream.last_attempt.error
            accumulated = accumulated + fb_acc
            if fb_usage and isinstance(fb_usage, dict):
                # Keep token counts of the successful model — that's the
                # one actually billed (gateway charges per call).
                usage_data = fb_usage
            retried_models.append(fb_model)
            effective_model = fb_model
            if fb_err:
                print(f"[PP] fallback_error {fb_model}: {fb_err!r}", flush=True)
                continue
            try:
                files, _ = _extract_files_and_edits(accumulated, baseline.files)
            except (UnsafePathError, ValueError):
                files = {}
            if not _looks_truncated(fb_acc, files):
                break

        if retried_models:
            # Reflect the actually-used model on the message row so the
            # chat header shows e.g. "claude-haiku-4-5" instead of the
            # original Gemini that failed.
            model_id = effective_model

    return files, accumulated, usage_data, effective_model
