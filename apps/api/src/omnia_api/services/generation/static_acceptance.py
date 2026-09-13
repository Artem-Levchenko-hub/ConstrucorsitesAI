from __future__ import annotations

import asyncio
import logging
from typing import Any

from omnia_api.core.config import get_settings
from omnia_api.core.redis import publish_event
from omnia_api.services import pipeline_debug
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
from omnia_api.services.generation.stream_support import _catalog_fallback_generate
from omnia_api.services.image_resolver import resolve_images
from omnia_api.services.prompt_builder import KIT_FILES
from omnia_api.services.visual_enricher import enrich_files as enrich_visual_files
from omnia_api.services.visual_enricher import ensure_signature_floor

_log = logging.getLogger("omnia_api.routers.messages")


async def accept_static_candidate(
    *,
    accumulated: str,
    baseline: SourceBaseline,
    effective_model: str,
    files: dict[str, str],
    force_model: str | None,
    history_serialized: list[dict[str, str]],
    ids: GenerationIds,
    project_info: ProjectGenerationFacts,
    prompt_text: str,
    selected_elements: list[dict[str, Any]] | None,
    stream: ModelStream,
    surgical: bool,
) -> tuple[dict[str, str], str, int | None]:
    _acc_settings = get_settings()
    _acc_fingerprint: int | None = None
    if (
        files
        and not surgical
        and project_info.template
        not in (
            "fullstack",
            "nextjs_entities",
            "spa",
            "tgbot",
            "api",
            "code",
            "realtime",
            "max_miniapp",
        )
        and _acc_settings.use_acceptance_gate
        and stream.generation_mode in ("freeform", "catalog")
    ):
        await stream.emit_stage("judge", "start")
        from omnia_api.services import acceptance as _acceptance

        # Design judge (premium / on-button): force the Awwwards vision-critic
        # and allow EXACTLY ONE repair re-roll even in score-only mode (owner:
        # the judge must NOT loop many times — 1 iteration is the whole point).
        # Otherwise keep score-only (0 repairs) / max-retries as before.
        _design_judge = _acc_settings.use_design_judge
        # OWNER 2026-06-14: with auto_regenerate_enabled OFF the gate still
        # EVALUATES (advisory verdict published) but never re-rolls — 0 repairs.
        _max_acc = (
            0
            if not _acc_settings.auto_regenerate_enabled
            else 1
            if _design_judge
            else 0
            if _acc_settings.acceptance_score_only
            else max(0, int(_acc_settings.acceptance_max_retries))
        )
        # Taste barrier (область T, DARK): a generic→repair pass is decoupled
        # from auto_regenerate_enabled (owner: never auto-regenerate the whole
        # page) — it allows at most `acceptance_taste_repair_passes` extra
        # re-rolls, and ONLY when `acceptance_taste_repair_on_generic` is on.
        # Default (flag OFF / passes 0) leaves `_max_acc` untouched.
        if _acc_settings.acceptance_taste_repair_on_generic:
            _max_acc = max(_max_acc, int(_acc_settings.acceptance_taste_repair_passes))
        # Area D (anti-sameness, DARK): soften the catalog fallback. A gauntlet/
        # structural fail (verdict "broken" / struct-not-ok — both already
        # `_repair_worthy`) earns up to `acceptance_gate_repair_passes` freeform
        # re-rolls WITH the gate's failed-class feedback BEFORE dropping to the
        # single catalog template — so a diverse page fixes the specific issue
        # instead of being wholesale-replaced. Decoupled from
        # `auto_regenerate_enabled` (a targeted feedback re-roll, not a blind
        # full-page regen). Default 0 → straight to catalog (today's behaviour).
        if int(_acc_settings.acceptance_gate_repair_passes) > 0:
            _max_acc = max(_max_acc, int(_acc_settings.acceptance_gate_repair_passes))
        # Repair floor: only spend a repair re-roll on a genuinely deficient
        # page (see acceptance_repair_floor docstring) — not on every
        # borderline vision score.
        _repair_floor = max(0, int(_acc_settings.acceptance_repair_floor))
        _acceptance_verdict: _acceptance.AcceptanceResult | None = None
        # Best-so-far guard: the design-judge repair can REGRESS (re-add dead
        # links → struct fails, or strip wow-features). Track the best-ranked
        # attempt and ship THAT — never a repair worse than what we had.
        _best_files = files
        _best_rank: tuple[int, int, int] | None = None
        try:
            for _acc_attempt in range(_max_acc + 1):
                # Hard cap: the design-judge (full-page screenshot + vision
                # model) must NEVER hang the build. On timeout → TimeoutError
                # bubbles to the gate's except → we ship the current page and
                # still reach commit (no lost work).
                _attempt_verdict = await asyncio.wait_for(
                    _acceptance.evaluate(
                        files,
                        project_id=str(ids.project_id),
                        prompt_context=prompt_text,
                        user_id=str(ids.user_id),
                        # Design judge forces the vision-critic ON (else follow the
                        # use_vision_audit setting). It now judges a FULL-PAGE,
                        # images-painted screenshot — no more "empty hero" misreads.
                        run_vision=(True if _design_judge else None),
                        # Originality: respect the setting (owner left it OFF — it
                        # fingerprinted unreliable shots and false-flagged minimal
                        # heroes as near-duplicates). Freeform-only when enabled.
                        run_originality=(
                            _acc_settings.use_originality and stream.generation_mode == "freeform"
                        ),
                        # V2.5.1 — feed the persisted onboarding answers to the
                        # chip-pixel fidelity leg so a request↔render mismatch
                        # (e.g. asked dark, rendered light) is a real finding
                        # instead of the empty-spec no-op it was before.
                        discovery_spec=project_info.discovery_spec,
                    ),
                    timeout=90,
                )
                _acceptance_verdict = _attempt_verdict
                _rank = (
                    1 if _attempt_verdict.structural_ok else 0,
                    1 if _attempt_verdict.responsive_ok else 0,
                    int(_attempt_verdict.score or 0),
                )
                if _best_rank is None or _rank > _best_rank:
                    _best_rank, _best_files = _rank, files
                print(
                    f"[PP] acceptance attempt={_acc_attempt} "
                    f"passed={_attempt_verdict.passed} "
                    f"verdict={_attempt_verdict.verdict} "
                    f"score={_attempt_verdict.score} "
                    f"struct={_attempt_verdict.structural_ok} "
                    f"resp={_attempt_verdict.responsive_ok} "
                    f"vision={_attempt_verdict.vision_ran}",
                    flush=True,
                )
                pipeline_debug.dump(
                    ids.project_id,
                    ids.assistant_message_id,
                    f"04_vision_attempt{_acc_attempt}.md",
                    f"verdict={_attempt_verdict.verdict} "
                    f"score={_attempt_verdict.score} "
                    f"passed={_attempt_verdict.passed} "
                    f"struct={_attempt_verdict.structural_ok} "
                    f"resp={_attempt_verdict.responsive_ok} "
                    f"vision_ran={_attempt_verdict.vision_ran}\n\n"
                    f"ISSUES ({len(_attempt_verdict.issues)}):\n"
                    + "\n".join(f"- {_i}" for _i in _attempt_verdict.issues)
                    + "\n\nFEEDBACK:\n"
                    + (_attempt_verdict.feedback or "(none)"),
                )
                await publish_event(
                    ids.project_id,
                    "llm.audit",
                    {
                        "message_id": str(ids.assistant_message_id),
                        "stage": "acceptance",
                        "passed": _attempt_verdict.passed,
                        "verdict": _attempt_verdict.verdict,
                        "score": _attempt_verdict.score,
                        "vision_status": (
                            "verified" if _attempt_verdict.vision_ran else "unverified"
                        ),
                    },
                )
                # Spend the repair re-roll ONLY on a genuinely deficient page:
                # a hard structural/responsive defect, a "broken" vision
                # verdict, or a vision score below the floor. A merely-not-
                # perfect page (struct+resp OK, score in [floor, min_score))
                # ships as attempt-0 — this is the reflexive-repair cut.
                # Taste barrier (область T, DARK): when
                # `acceptance_taste_repair_on_generic` is on, a GENERIC verdict
                # (not only "broken") that vision really produced is also
                # repair-worthy — "not ugly but generic" earns one re-roll with
                # the vision issues as feedback. Default OFF → unchanged.
                _taste_repair = (
                    _acc_settings.acceptance_taste_repair_on_generic
                    and _attempt_verdict.vision_ran
                    and _attempt_verdict.verdict == "generic"
                )
                _repair_worthy = (
                    not _attempt_verdict.structural_ok
                    or not _attempt_verdict.responsive_ok
                    or _attempt_verdict.verdict == "broken"
                    or (
                        _attempt_verdict.vision_ran
                        and _attempt_verdict.score is not None
                        and int(_attempt_verdict.score) < _repair_floor
                    )
                    or _taste_repair
                )
                if (
                    _attempt_verdict.passed
                    or not _attempt_verdict.feedback
                    or _acc_attempt >= _max_acc
                    or not _repair_worthy
                ):
                    break
                notice = (
                    f"\n\n*Приёмка {_acc_attempt + 1}/{_max_acc}: правлю вёрстку "
                    f"({_attempt_verdict.verdict})…*\n\n"
                )
                accumulated = accumulated + notice
                await publish_event(
                    ids.project_id,
                    "llm.chunk",
                    {"message_id": str(ids.assistant_message_id), "delta": notice},
                )
                stream.messages.append({"role": "assistant", "content": accumulated})
                stream.messages.append({"role": "user", "content": _attempt_verdict.feedback})
                # Hard cap: a stuck repair re-roll must not hang the build —
                # on timeout we ship the pre-repair page and reach commit.
                await asyncio.wait_for(
                    stream.run(
                        effective_model,
                        force_all=force_model,
                        allow_art_director=False,
                    ),
                    timeout=120,
                )
                if stream.last_attempt.error or not str(stream.last_attempt.text).strip():
                    print(
                        f"[PP] acceptance_repair_empty err={stream.last_attempt.error!r}",
                        flush=True,
                    )
                    break
                _repair_acc = str(stream.last_attempt.text)
                try:
                    _repaired, _ = _extract_files_and_edits(_repair_acc, baseline.files)
                except (UnsafePathError, ValueError):
                    _repaired = {}
                if not _repaired:
                    print("[PP] acceptance_repair_no_files", flush=True)
                    break
                _repaired = {p: c for p, c in _repaired.items() if p not in KIT_FILES}
                _repaired = _ensure_kit_linked(_repaired)
                if _acc_settings.use_visual_enricher:
                    try:
                        _repaired, _, _ = enrich_visual_files(_repaired)
                    except Exception:
                        pass
                if _acc_settings.use_signature_floor:
                    try:
                        _repaired, _ = ensure_signature_floor(_repaired)
                    except Exception:
                        pass
                if project_info.image_gen_enabled:
                    try:
                        _repaired, _, _ = await asyncio.wait_for(
                            resolve_images(_repaired, str(ids.project_id)), timeout=75
                        )
                    except Exception:
                        pass
                files = _repaired
                accumulated = accumulated + _repair_acc

            # Best-so-far: if every repair ranked below an earlier attempt,
            # ship the best one — never regress (e.g. attempt0 struct-OK but
            # vision-flagged → attempt1 re-added dead links → struct broken).
            if _best_files is not files:
                print(f"[PP] acceptance_best_so_far revert rank={_best_rank}", flush=True)
                files = _best_files

            # Remember an accepted freeform page's fingerprint (Sprint 4)
            # so later generations can be nudged off near-duplicates.
            if _acceptance_verdict is not None and _acceptance_verdict.passed:
                _acc_fingerprint = _acceptance_verdict.fingerprint

            # Freeform exhausted its retries and still fails → regenerate
            # once via the catalog/IR path (guaranteed valid page).
            # Taste-barrier guard (область T, review HIGH-2): a vision-ONLY
            # block (struct/resp/gauntlet all OK, only the taste verdict flipped
            # passed) must NOT drop the rich freeform to the uglier catalog
            # fallback unless a taste re-roll is configured — otherwise enabling
            # acceptance_vision_block_enabled alone would catalog-bomb a large
            # share of structurally-fine freeform builds. With taste-repair on,
            # the re-roll already ran (Loop A); catalog stays the last resort.
            _vision_only_block = (
                _acceptance_verdict is not None
                and getattr(_acceptance_verdict, "vision_blocked", False)
                and not _acc_settings.acceptance_taste_repair_on_generic
            )
            if (
                _acceptance_verdict is not None
                and not _acceptance_verdict.passed
                and not _vision_only_block
                and stream.generation_mode == "freeform"
                and _acc_settings.use_section_catalog
                and not _acc_settings.acceptance_score_only
            ):
                print("[PP] acceptance->catalog fallback", flush=True)
                _fb_files = await _catalog_fallback_generate(
                    history=history_serialized,
                    prompt_text=prompt_text,
                    selected_elements=selected_elements,
                    preset_id=project_info.design_preset_id,
                    project_id=ids.project_id,
                    user_id=ids.user_id,
                    assistant_message_id=ids.assistant_message_id,
                    current_files=baseline.files,
                    discovery_spec=project_info.discovery_spec,
                )
                if _fb_files:
                    _fb_files = {p: c for p, c in _fb_files.items() if p not in KIT_FILES}
                    _fb_files = _ensure_kit_linked(_fb_files)
                    files = _fb_files
                    notice = (
                        "\n\n*Свободная вёрстка не прошла приёмку — собрал "
                        "через надёжный каталог.*\n\n"
                    )
                    accumulated = accumulated + notice
                    await publish_event(
                        ids.project_id,
                        "llm.chunk",
                        {"message_id": str(ids.assistant_message_id), "delta": notice},
                    )
        except Exception as _acc_exc:
            print(f"[PP] acceptance_gate_failed err={_acc_exc!r}", flush=True)
        await stream.emit_stage("judge", "end")

    return files, accumulated, _acc_fingerprint
