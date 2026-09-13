from __future__ import annotations

import logging
from typing import Any

from omnia_api.core.config import (
    get_settings,
    model_for_role,
)
from omnia_api.core.redis import publish_event
from omnia_api.schemas.project import CONTAINER_BROWSER_TEMPLATES as CONTAINER_NEXT
from omnia_api.services import (
    image_edit,
    pipeline_debug,
)
from omnia_api.services.contrast_guard import enforce_contrast
from omnia_api.services.file_extractor import UnsafePathError
from omnia_api.services.generation.contracts import (
    GenerationIds,
    ProjectGenerationFacts,
    SourceBaseline,
)
from omnia_api.services.generation.file_transforms import (
    _ensure_kit_linked,
    _extract_files_and_edits,
)
from omnia_api.services.generation.stream_attempt import ModelStream
from omnia_api.services.generation.stream_support import (
    _AUDIT_JUDGE_HIGH,
    _AUDIT_JUDGE_LOW,
    _audit_judge_wants_retry,
)
from omnia_api.services.link_validator import (
    find_dead_links,
    repair_dead_links_inline,
)
from omnia_api.services.prompt_builder import KIT_FILES
from omnia_api.services.ui_audit import (
    AuditReport,
    format_failures_for_retry,
)
from omnia_api.services.ui_audit import audit as ui_audit
from omnia_api.services.visual_enricher import enrich_files as enrich_visual_files
from omnia_api.services.visual_enricher import ensure_signature_floor

_log = logging.getLogger("omnia_api.routers.messages")


async def repair_dead_links(
    *,
    _is_code: bool,
    accumulated: str,
    baseline: SourceBaseline,
    files: dict[str, str],
    force_model: str | None,
    ids: GenerationIds,
    project_info: ProjectGenerationFacts,
    stream: ModelStream,
    surgical: bool,
) -> tuple[dict[str, str], str]:
    if files and not surgical and project_info.template not in CONTAINER_NEXT and not _is_code:
        initial_dead = find_dead_links(files)
        if initial_dead:
            files = repair_dead_links_inline(files)
            post_inline = find_dead_links(files)
            print(
                f"[PP] dead_links initial={len(initial_dead)} after_inline={len(post_inline)}",
                flush=True,
            )
            dead = post_inline
        else:
            dead = []
        # OWNER 2026-06-14: auto full-page regeneration is OFF by default
        # (auto_regenerate_enabled). The inline href fixer above already ran
        # (a targeted edit, kept); the LLM re-roll regenerates whole files, so
        # it only fires when auto-regen is explicitly re-enabled.
        if get_settings().auto_regenerate_enabled and len(dead) > 3:
            print(f"[PP] dead_links remain={len(dead)} -> LLM repair pass", flush=True)
            prior_answer = accumulated
            notice = (
                "\n\n*Проверка ссылок: часть кнопок вела в никуда — "
                "перегенерирую с рабочими ссылками…*\n\n"
            )
            accumulated = accumulated + notice
            await publish_event(
                ids.project_id,
                "llm.chunk",
                {"message_id": str(ids.assistant_message_id), "delta": notice},
            )
            repair_request = (
                "В предыдущем ответе есть ссылки/кнопки, ведущие в никуда:\n"
                + "\n".join(f"— {d}" for d in dead[:20])
                + "\n\nВерни ПОЛНЫЕ исправленные файлы целиком (в тех же "
                "<file>-блоках). Каждая ссылка обязана вести на существующий "
                "якорь (создай секцию с нужным id), tel:/mailto:/мессенджер или "
                'реальную страницу. Ни одного href="#", пустого href или '
                "javascript:void(0). Больше ничего не меняй."
            )
            stream.messages.append({"role": "assistant", "content": prior_answer})
            stream.messages.append({"role": "user", "content": repair_request})
            # B2 — dead-link repair is the `link_repair` role's job (cheap
            # Haiku). force_single_shot so a budget model in the multipass
            # set edits the existing files instead of regenerating the page.
            await stream.run(
                model_for_role("link_repair", override=force_model),
                force_single_shot=True,
            )
            repaired_acc = str(stream.last_attempt.text)
            try:
                repaired_files, _ = _extract_files_and_edits(repaired_acc, baseline.files)
            except (UnsafePathError, ValueError):
                repaired_files = {}
            if repaired_files and len(find_dead_links(repaired_files)) < len(dead):
                files = repaired_files
                accumulated = accumulated + repaired_acc
                print(f"[PP] repair applied files={len(files)}", flush=True)
            else:
                print("[PP] repair skipped (no improvement)", flush=True)

    return files, accumulated


async def apply_initial_quality_guards(
    *,
    _is_code: bool,
    files: dict[str, str],
    ids: GenerationIds,
    project_info: ProjectGenerationFacts,
    surgical: bool,
) -> tuple[dict[str, str], Any]:
    if (
        files
        and project_info.template not in CONTAINER_NEXT
        and not _is_code
        and not project_info.is_imported
    ):
        files = {p: c for p, c in files.items() if p not in KIT_FILES}
        files = _ensure_kit_linked(files)

    # Image-resolver: if Haiku/Sonnet wrote <img data-omnia-gen="..."> tags,
    # call gateway → MinIO and rewrite the tags with real src URLs BEFORE
    # the commit so the snapshot / GitHub export / rollback all carry final
    # URLs (single source of truth in git). For fullstack the subsequent
    # hot_reload pushes the rewritten files into the dev container, so HMR
    # picks up the real images automatically. Per-project opt-out via
    # projects.image_gen_enabled (TopBar toggle).
    # Visual enricher — DORMANT by default (USE_VISUAL_ENRICHER, off).
    # Раньше лепил декор в каждый <section> голого HTML безусловно, но
    # циклил dot-grid / diagonal-lines / mesh по ВСЕМ секциям механически —
    # output читался как generative AI-slop («полоски/точки»). Owner-call
    # 2026-05-31: выкл совсем. Re-enable per-env via USE_VISUAL_ENRICHER=true.
    if files and get_settings().use_visual_enricher:
        try:
            files, enr_count, enr_total = enrich_visual_files(files)
            print(
                f"[PP] visual_enricher enriched={enr_count} sections={enr_total}",
                flush=True,
            )
        except Exception as enr_exc:
            print(f"[PP] visual_enricher failed: {enr_exc!r}", flush=True)

    # Signature-moment floor — guarantee ONE "expensive" scroll moment per
    # build (owner doctrine «всегда вау»). Surgical: injects a single
    # .omnia-draw line-art divider ONLY when the page carries no
    # .pin-stage/.compare/.omnia-draw/.scroll-clip-reveal. ON by default
    # (USE_SIGNATURE_FLOOR); fail-soft (.html only, never raises).
    if files and not surgical and get_settings().use_signature_floor:
        try:
            files, _sig_n = ensure_signature_floor(files)
            if _sig_n:
                print(f"[PP] signature_floor injected={_sig_n}", flush=True)
        except Exception as _sig_exc:
            print(f"[PP] signature_floor failed: {_sig_exc!r}", flush=True)

    # Hero background visible — owner: «главный экран генерится с фоткой/
    # нарисованным фоном, в тему». The writer buries the hero's full-bleed
    # photo/graphic under a /70-/90 black wash → flat monotone screen. Lighten
    # that overlay + dim the shader so the on-theme image/graphic actually
    # shows. Build-only, deterministic, fail-soft.
    if files and not surgical and get_settings().use_hero_bg_visible:
        try:
            _idx_html = files.get("index.html")
            if _idx_html:
                _unmasked, _hb_changed = image_edit.unmask_hero_bg(_idx_html)
                if _hb_changed:
                    files["index.html"] = _unmasked
                    print("[PP] hero_bg_unmasked", flush=True)
        except Exception as _hb_exc:
            print(f"[PP] hero_bg_visible skipped: {_hb_exc!r}", flush=True)

    # Phase K (2026-05-27) — objective UI audit. ``ui_audit`` runs the
    # 10-point Malewicz Ch27 + Phase G rubric (typography/color/button/
    # accessibility/no-lorem/etc.) on the final HTML pool. We log the
    # score so we can baseline per-model design quality from prod logs
    # and emit it over WS so the workspace UI can surface a quality
    # indicator. NOT a re-generate trigger yet — measurement first,
    # feedback loop comes in Sprint 2 once we have a baseline score
    # distribution. Best-effort: any audit failure logs and continues.
    report: AuditReport | None = None
    if files:
        try:
            html_pool = {
                p: c for p, c in files.items() if p.endswith(".html") or p.endswith(".htm")
            }
            if html_pool:
                report = ui_audit(html_pool)
                failed_ids = [f.check_id for f in report.failures]
                print(
                    f"[PP] ui_audit score={report.score}/{report.max} failed={failed_ids}",
                    flush=True,
                )
                if pipeline_debug.enabled():
                    pipeline_debug.dump(
                        ids.project_id,
                        ids.assistant_message_id,
                        "06_ui_audit.md",
                        f"score={report.score}/{report.max}\n\n"
                        + "\n".join(
                            f"- [{f.severity}] {f.check_id}: {f.description} | {f.evidence}"
                            for f in report.failures
                        ),
                    )
                await publish_event(
                    ids.project_id,
                    "llm.audit",
                    {
                        "message_id": str(ids.assistant_message_id),
                        "score": report.score,
                        "max": report.max,
                        "failures": [
                            {
                                "id": f.check_id,
                                "severity": f.severity,
                                "description": f.description,
                                "evidence": f.evidence,
                            }
                            for f in report.failures
                        ],
                    },
                )
        except Exception as audit_exc:
            print(f"[PP] ui_audit failed: {audit_exc!r}", flush=True)

    return files, report


async def retry_catalog_audit(
    *,
    accumulated: str,
    baseline: SourceBaseline,
    files: dict[str, str],
    force_model: str | None,
    ids: GenerationIds,
    project_info: ProjectGenerationFacts,
    report: Any,
    stream: ModelStream,
    usage_data: dict[str, Any] | None,
) -> tuple[dict[str, str], str, dict[str, Any] | None]:
    _RETRY_SCORE_THRESHOLD = 7  # max=10
    try:
        _last_report = report
        # B3 — deterministic rubric verdict, refined by the optional LLM
        # judge (role `audit`, Sonnet) ONLY in the borderline 6–7/10 band
        # where the rubric is least decisive. The judge can both *promote*
        # a 7 to a re-roll and *spare* a 6 that's actually fine, so it owns
        # the close calls; clear-cut scores never pay for it.
        _score = int(getattr(_last_report, "score", 10)) if _last_report else 10
        _wants_retry = _score < _RETRY_SCORE_THRESHOLD
        _retry_eligible = (
            # Catalog-only retry (re-rolls the IR). Freeform quality is
            # handled by the Phase 11 acceptance gate, not here.
            stream.generation_mode == "catalog" and _last_report is not None
        )
        if _retry_eligible and _AUDIT_JUDGE_LOW <= _score <= _AUDIT_JUDGE_HIGH:
            _judge_html = "\n".join(
                c for p, c in files.items() if p.endswith(".html") or p.endswith(".htm")
            )
            _audit_retry_verdict = await _audit_judge_wants_retry(
                html=_judge_html,
                report=_last_report,
                model=model_for_role("audit", override=force_model),
                user_id=ids.user_id,
                project_id=ids.project_id,
                message_id=ids.assistant_message_id,
            )
            if _audit_retry_verdict is not None:
                print(
                    f"[PP] audit_judge verdict="
                    f"{'RETRY' if _audit_retry_verdict else 'PASS'} "
                    f"score={_score}",
                    flush=True,
                )
                _wants_retry = _audit_retry_verdict
        if _retry_eligible and _wants_retry and _last_report is not None:
            retry_msg = format_failures_for_retry(_last_report)
            if retry_msg:
                print(
                    f"[PP] retry_triggered score={_last_report.score}/{_last_report.max} "
                    f"failures={len(_last_report.failures)}",
                    flush=True,
                )
                await publish_event(
                    ids.project_id,
                    "llm.retry",
                    {
                        "message_id": str(ids.assistant_message_id),
                        "reason": "audit_score_low",
                        "score": _last_report.score,
                        "max": _last_report.max,
                    },
                )
                # Append prior assistant response + retry feedback to
                # the same message list so prompt caching (system) hits.
                stream.messages.append({"role": "assistant", "content": accumulated})
                stream.messages.append({"role": "user", "content": retry_msg})
                # B3 — the re-roll is the `audit_retry` role's job (Opus,
                # director-grade). force_all pins every pass onto it so the
                # second attempt is uniformly strong, not the original mix.
                _retry_model = model_for_role("audit_retry", override=force_model)
                await stream.run(_retry_model, force_all=_retry_model)
                retry_accumulated = str(stream.last_attempt.text)
                retry_usage = stream.last_attempt.usage
                retry_error = stream.last_attempt.error
                if not retry_error and retry_accumulated:
                    # Replicate the L3 IR→HTML conversion on the retry
                    # output. Fail-soft: if it doesn't parse, keep the
                    # first-pass files unchanged.
                    import json as _json_r

                    from pydantic import ValidationError as _VE_r

                    from omnia_api.sections import PageIR as _PageIR_r
                    from omnia_api.sections.renderer import render_to_files as _render_to_files_r

                    raw_r = retry_accumulated.strip()
                    if raw_r.startswith("```"):
                        raw_r = raw_r.split("\n", 1)[1] if "\n" in raw_r else raw_r[3:]
                        if raw_r.endswith("```"):
                            raw_r = raw_r[:-3]
                        raw_r = raw_r.strip()
                    try:
                        ir_r = _PageIR_r.model_validate(_json_r.loads(raw_r))
                        # Phase L8 — Smart Defaults on retry IR too.
                        from omnia_api.sections import apply_smart_defaults as _smart_r

                        ir_r = _smart_r(ir_r, preset_id=project_info.design_preset_id)
                        kit_css_r = baseline.files.get("src/assets/omnia-kit.css", "")
                        kit_js_r = baseline.files.get("src/assets/omnia-kit.js", "")
                        rendered_r = _render_to_files_r(ir_r, kit_css=kit_css_r, kit_js=kit_js_r)
                        # Replace files for downstream stages.
                        files = dict(rendered_r)
                        accumulated = "\n".join(
                            f'<file path="{p}">\n{c}\n</file>' for p, c in rendered_r.items()
                        )
                        # Merge retry usage onto first-pass usage.
                        if usage_data is None:
                            usage_data = retry_usage
                        elif retry_usage:
                            usage_data = {
                                "tokens_in": (
                                    int((usage_data or {}).get("tokens_in", 0))
                                    + int((retry_usage or {}).get("tokens_in", 0))
                                ),
                                "tokens_out": (
                                    int((usage_data or {}).get("tokens_out", 0))
                                    + int((retry_usage or {}).get("tokens_out", 0))
                                ),
                                "cost_rub": (
                                    float((usage_data or {}).get("cost_rub", 0.0))
                                    + float((retry_usage or {}).get("cost_rub", 0.0))
                                ),
                            }
                        # Re-audit so logs show the post-retry score.
                        try:
                            html_pool_r = {
                                p: c
                                for p, c in files.items()
                                if p.endswith(".html") or p.endswith(".htm")
                            }
                            if html_pool_r:
                                report_r = ui_audit(html_pool_r)
                                print(
                                    f"[PP] ui_audit_retry score={report_r.score}/{report_r.max}",
                                    flush=True,
                                )
                                await publish_event(
                                    ids.project_id,
                                    "llm.audit",
                                    {
                                        "message_id": str(ids.assistant_message_id),
                                        "score": report_r.score,
                                        "max": report_r.max,
                                        "stage": "retry",
                                    },
                                )
                        except Exception:
                            pass
                    except (_json_r.JSONDecodeError, _VE_r, ValueError) as retry_ir_exc:
                        _log.warning(
                            "retry IR parse failed (keeping first-pass files): %r",
                            retry_ir_exc,
                        )
                        print(
                            f"[PP] retry_ir_fail err={retry_ir_exc!r}",
                            flush=True,
                        )
    except Exception as retry_exc:
        print(f"[PP] retry_branch_failed err={retry_exc!r}", flush=True)

    return files, accumulated, usage_data


async def apply_final_quality_guards(
    *,
    files: dict[str, str],
    ids: GenerationIds,
    project_info: ProjectGenerationFacts,
    stream: ModelStream,
    surgical: bool,
) -> tuple[dict[str, str]]:
    if files and not surgical:
        if pipeline_debug.enabled():
            pipeline_debug.dump(
                ids.project_id,
                ids.assistant_message_id,
                "07_pre_palette_guard.html",
                files.get("index.html", ""),
            )
        try:
            from omnia_api.services.app_theme import apply_app_palette
            from omnia_api.services.design_tokens import tokens_for_project
            from omnia_api.services.palette_guard import enforce_palette

            _palette = tokens_for_project(
                str(ids.project_id), industry_hint=project_info.design_preset_id
            ).palette
            if pipeline_debug.enabled():
                pipeline_debug.dump(
                    ids.project_id,
                    ids.assistant_message_id,
                    "07b_forced_palette.md",
                    repr(_palette),
                )
            # Entity/.tsx apps theme via a brand :root override in
            # (app)/layout.tsx, NOT via index.html — enforce_palette below is
            # HTML-only and skips them. Snap the brand --primary to a colour
            # that's actually visible on the kit's light canvas (the writer
            # routinely emits a dark-palette near-white primary that vanishes).
            files = apply_app_palette(files, _palette)
            files = enforce_palette(files, _palette)
        except Exception as _pg_exc:
            print(f"[PP] palette_guard skipped err={_pg_exc!r}", flush=True)
        files = enforce_contrast(files)
        if pipeline_debug.enabled():
            pipeline_debug.dump(
                ids.project_id,
                ids.assistant_message_id,
                "08_post_palette_guard.html",
                files.get("index.html", ""),
            )

    # ── Brief-narration bake (v2.21 #1A, pillar 3+4) — BEFORE commit ──
    # The most-shared public surface (freeform static /p/<slug>) was born
    # SILENT: a colleague pasting the link saw a finished page, none of the
    # "AI is designing this" reveal that hooks the viral loop. Bake the
    # art-director brief + a self-contained reveal into index.html so the
    # shared link plays the SAME birth narration for a stranger (the brief
    # reached only the workspace/iframe before). Freeform only — container
    # apps (nextjs_entities/fullstack) 302-redirect to a live app on another
    # origin where the template's own omnia-brief-narration.js handles it.
    # Fail-soft + idempotent (services/brief_narration); 0-line brief = no-op.
    _bn_brief = stream.brief
    if (
        files
        and not surgical
        and stream.generation_mode == "freeform"
        and isinstance(_bn_brief, dict)
        and files.get("index.html")
    ):
        try:
            from omnia_api.services.brief_narration import inject_brief_narration

            files["index.html"] = inject_brief_narration(files["index.html"], _bn_brief)
            print("[PP] brief_narration baked into index.html", flush=True)
        except Exception as _bn_exc:
            print(f"[PP] brief_narration skipped err={_bn_exc!r}", flush=True)

    return (files,)
