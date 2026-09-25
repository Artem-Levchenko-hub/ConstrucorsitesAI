from __future__ import annotations

from dataclasses import replace

from yleum_api.core.config import get_settings
from yleum_api.models.project import Project
from yleum_api.schemas.message import SurveyQuestion
from yleum_api.services.chip_pixel_gate import spec_from_discovery, spec_preview
from yleum_api.services.discovery import (
    DiscoveryResult,
    PlannedQuestion,
    _infer_code_from_text,
    classify_result_type,
    confident_enough_to_build,
    cumulative_idea,
    gather_answers,
    infer_niche_label,
    infer_result_type_from_text,
    plan_discovery_questions,
    recap_labels,
    run_discovery,
    serve_planned_question,
    zero_question_build,
)


def _compose_build_prompt(result: DiscoveryResult) -> str:
    """Fold the discovery brief (+ recommended stack hint) into the generator
    prompt. Stack provisioning is wired separately (P1 subtask 5); until then the
    recommendation rides in the brief so the build is aware of the intended shape."""
    brief = result.brief.strip()
    if result.stack == "code":
        brief = (
            f"{brief}\n\n[Это запрос на код/программу, НЕ сайт. Выдай рабочую "
            "программу/скрипт на запрошенном языке (файлы + README), без HTML/"
            "вёрстки.]"
        )
    elif result.stack and result.stack != "static":
        brief = (
            f"{brief}\n\n[Рекомендованный стек: {result.stack} — полноценное приложение с данными.]"
        )
    return brief


def _build_onboarding_survey(plan: object) -> list[SurveyQuestion] | None:
    """Assemble the whole onboarding popup from the planned question batch + a
    palette question (owner 2026-06-19 — «несколько вопросов сразу + палитра»).

    Returns the full survey (every planned text question, each with its chips +
    «Другое», then a clickable preset-palette question) so the workspace renders
    ONE form instead of a chat turn per question. None when the plan is empty."""
    if not isinstance(plan, list) or not plan:
        return None
    out: list[SurveyQuestion] = []
    for q in plan:
        if not isinstance(q, dict):
            continue
        msg = str(q.get("message") or "").strip()
        if not msg:
            continue
        out.append(
            SurveyQuestion(
                message=msg,
                kind="text",
                choices=[c for c in (q.get("choices") or []) if isinstance(c, str)],
                allow_custom=bool(q.get("allow_custom", True)),
                multi_select=bool(q.get("multi_select", False)),
            )
        )
    if not out:
        return None
    # Palette question — clickable preset swatches (owner: «нажать на палитру
    # которая нравится»). Reuses the Awwwards preset catalog; the pick rides back
    # as PromptRequest.design_preset_id. First few presets keep the popup compact.
    from yleum_api.services.design_presets import PRESETS as _PRESETS

    options = [
        {
            "id": pid,
            "name": preset.name,
            "one_liner": preset.one_liner,
            "bg": preset.palette.get("bg", "#ffffff"),
            "accent": preset.palette.get("accent", "#0a0a0a"),
        }
        for pid, preset in list(_PRESETS.items())[:6]
    ]
    out.append(
        SurveyQuestion(
            message="Какая палитра вам ближе? (можно пропустить — подберём сами)",
            kind="palette",
            choices=[],
            allow_custom=True,
            multi_select=False,
            options=options,
        )
    )
    return out


_RESULT_TYPE_QUESTION = (
    "Что именно сделать — лендинг с заявкой, приложение с аккаунтами, или интерактивный инструмент?"
)


_RESULT_TYPE_CHOICES = (
    "Лендинг с заявкой",
    "Приложение с аккаунтами",
    "Интерактивный инструмент",
    "Просто сайт",
)


async def _maybe_result_type_question(prompt: str, language: str) -> PlannedQuestion | None:
    """One clarifying question about the RESULT TYPE when genuinely ambiguous
    (RT-1 bug 3). Returns a PlannedQuestion to PREPEND to the design plan (so the
    existing serve/index machinery runs the design interview right after the type
    answer — no separate-turn off-by-one, no blind build), or None when the type
    is clear. Gated by result_type_clarify_question; off → always None (today)."""
    s = get_settings()
    if not (s.use_result_type_router and s.result_type_clarify_question):
        return None
    if infer_result_type_from_text(prompt) is not None:
        return None  # keyword net is sure
    rt, conf = await classify_result_type(prompt, language=language)
    if rt is not None and conf >= 0.6:
        return None  # classifier is sure
    return PlannedQuestion(
        message=_RESULT_TYPE_QUESTION,
        choices=_RESULT_TYPE_CHOICES,
        allow_custom=True,
        multi_select=False,
    )


async def _batch_discovery_turn(
    project: Project,
    history: list[dict[str, str]],
    prompt: str,
    *,
    asked_count: int,
    force_build: bool,
    language: str = "ru",
) -> DiscoveryResult:
    """Batch discovery (owner rule 13 #1 — NORTH STAR pillar 2).

    On the FIRST turn we plan the WHOLE set of product-tailored questions in ONE
    upfront gateway pass (unless the prompt is rich enough to skip the popup
    entirely) and stash it on ``project.discovery_plan`` (committed with the
    message rows by the caller). Every turn then SERVES the next pre-computed
    question with NO further gateway call — zero wait between steps. Once the plan
    is exhausted (all questions answered) — or the user forces it — we build,
    reusing ``run_discovery``'s battle-tested brief/stack compilation.

    Never raises (R-10): ``plan_discovery_questions`` degrades to a deterministic
    batch, and the exhausted/forced path delegates to the fail-soft builder.

    ``language`` is threaded into gateway calls so the model replies in the
    project's language. RU (the default) leaves prompts unchanged.
    """
    if force_build:
        return await run_discovery(
            history,
            prompt,
            asked_count=asked_count,
            force_build=True,
            language=language,
        )
    # Plan once, on the first turn, when nothing is stashed yet. A first prompt
    # that already pins the design wins the zero-question shortcut and never gets
    # a plan (the popup never appears).
    if asked_count == 0 and not project.discovery_plan:
        # Code/script request → no design interview at all (owner 2026-06-19). Build
        # straight away (run_discovery short-circuits code intent to a code build) —
        # never plan palette/audience questions for a program.
        if _infer_code_from_text(prompt):
            return await run_discovery(
                history,
                prompt,
                asked_count=asked_count,
                force_build=True,
                language=language,
            )
        zero = zero_question_build(history, prompt)
        if zero is not None:
            return zero
        questions = await plan_discovery_questions(prompt, language=language)
        # RT-1 (bug 3): when the RESULT TYPE is genuinely ambiguous, PREPEND a single
        # type-clarify question as plan[0] so the existing serve/index machinery asks
        # the type first and flows straight into the design interview (no separate
        # turn, no off-by-one blind build). Gated/off by default → plan is unchanged.
        _type_q = await _maybe_result_type_question(prompt, language)
        if _type_q is not None:
            questions = [_type_q, *questions]
        project.discovery_plan = [q.to_dict() for q in questions]
    # LIVE niche (pillar 2 causality): re-infer on the CUMULATIVE answers (idea +
    # every reply), not just the first prompt, so the badge sharpens turn-by-turn
    # as the conversation reveals more. Deterministic; unrecognised → "" (no
    # suffix).
    niche = infer_niche_label(cumulative_idea(history, prompt))
    # Confidence-skip (pillar 2 — «лучший онбординг — его отсутствие»): once the
    # gathered answers pin a recognised niche + ≥2 design axes, build now instead
    # of asking the rest of the batch — the decisive user gets a shorter path.
    # Fail-soft: an unclear interview keeps serving the planned questions.
    if (
        confident_enough_to_build(history, prompt, asked_count=asked_count, niche=niche)
        and serve_planned_question(project.discovery_plan or [], asked_count) is not None
    ):
        return await run_discovery(
            history,
            prompt,
            asked_count=asked_count,
            force_build=True,
            language=language,
        )
    ask = serve_planned_question(project.discovery_plan or [], asked_count)
    if ask is not None:
        # Answer-recap (pillar 2 — «вас услышали»): echo the answers gathered so
        # far back as «✓ …» chips above the next question, so the loop visibly
        # reacts to what the user said.
        recap = recap_labels(gather_answers(history, prompt, asked_count))
        # LIVE design-preview (pillars 2×3 — «покажи ЧТО построим»): resolve the
        # cumulative answers into design tokens so the popup paints a mini-hero
        # that morphs turn-by-turn. Same spec_from_discovery the gauntlet uses.
        design_preview = spec_preview(spec_from_discovery(history, prompt))
        return replace(ask, niche=niche, recap=recap, design_preview=design_preview)
    # Plan exhausted — every question answered → build from the full Q&A.
    return await run_discovery(
        history,
        prompt,
        asked_count=asked_count,
        force_build=True,
        language=language,
    )
