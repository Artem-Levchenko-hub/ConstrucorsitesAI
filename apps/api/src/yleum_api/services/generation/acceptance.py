from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from fastapi import status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from yleum_api.core.config import (
    FREE_GENERATION_LIMIT,
    Settings,
    get_settings,
    model_for_role,
)
from yleum_api.core.errors import ApiError
from yleum_api.core.redis import get_redis
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.message import Message
from yleum_api.models.project import Project
from yleum_api.models.snapshot import Snapshot
from yleum_api.models.user import User
from yleum_api.models.wallet import Wallet
from yleum_api.schemas.message import (
    PromptRequest,
    PromptResponse,
)
from yleum_api.schemas.project import is_fullstack
from yleum_api.services import stack_routing
from yleum_api.services.billing_accounts import resolve_billing_account
from yleum_api.services.chip_pixel_gate import spec_from_discovery
from yleum_api.services.discovery import BUILD as DISCOVERY_BUILD
from yleum_api.services.discovery import (
    DiscoveryResult,
    _explicit_static,
    _has_account_intent,
    _has_conversion_intent,
    _infer_code_from_text,
    _infer_run_intent,
    _infer_run_intent_maybe,
    _infer_stack_from_text,
    _infer_web_pivot,
    _is_run_decline,
    _resolve_messenger_stack,
    classify_result_type,
    detect_appification,
    resolve_result_type,
    result_type_to_stack,
    run_discovery,
    wants_build_now,
    zero_question_build,
)
from yleum_api.services.generation.lightweight_turns import (
    _INSTALL_CARD_TEXT,
    _RUN_ASK_TEXT,
    _RUN_DECLINE_REPLY,
    _failed_build_explanation,
)
from yleum_api.services.generation.onboarding import (
    _batch_discovery_turn,
    _build_onboarding_survey,
    _compose_build_prompt,
)
from yleum_api.services.generation.publication import ORCHESTRATION_LABEL
from yleum_api.services.generation.supervisor import (
    _spawn_async_onboarding,
    _spawn_clarify,
    _spawn_process_prompt,
    _spawn_text_turn,
)
from yleum_api.services.generation_runs import (
    GenerationDispatch,
    reserve_generation_run,
    store_generation_dispatch,
)
from yleum_api.services.intent_triage import (
    EXPLAIN_FAILED_BUILD,
    ORCHESTRATE,
    decide_failed_build_followup,
    decide_intent,
)
from yleum_api.services.restoration_adaptation import (
    adaptation_reservation_prompt,
    prepare_adaptation,
)
from yleum_api.services.secret_safety import redact_provider_secrets

RESERVED_BALANCE = Decimal("5.0000")


class PromptAcceptance:
    """One request session, with explicit admission, routing and commit stages."""

    project_id: UUID
    payload: PromptRequest
    session: AsyncSession
    current_user: User
    project: Project
    generation_run: GenerationRun
    credential_redirect: bool
    is_first_build: bool
    explain_failed_build: bool
    failed_build_reply: str | None
    is_free: bool
    selected_dump: list[dict[str, Any]] | None
    run_intent: bool
    run_decline: bool
    run_ask: bool
    settings: Settings
    pivoted_to_web: bool
    escalated_to_app: bool
    discovery_result: DiscoveryResult | None
    async_onboarding: bool
    do_clarify: bool
    effective_prompt: str
    discovery_ask: bool
    orchestrate: bool
    force_model: str | None
    routing_model: str
    user_msg: Message
    assistant_msg: Message
    turn_mode: Literal["build", "edit", "clarify"]

    def __init__(
        self,
        *,
        project_id: UUID,
        payload: PromptRequest,
        session: AsyncSession,
        current_user: User,
        project: Project,
    ) -> None:
        self.project_id = project_id
        self.payload = payload
        self.session = session
        self.current_user = current_user
        self.project = project

    async def accept(self) -> PromptResponse:
        replay = await self.reserve_or_replay()
        if replay is not None:
            return replay
        await self.prepare_adaptation()
        await self.check_admission()
        await self.route_existing_project()
        await self.conduct_interview()
        await self.route_first_build()
        await self.select_intent_and_model()
        await self.persist_messages()
        await self.dispatch()
        return await self.present_response()

    async def reserve_or_replay(self) -> PromptResponse | None:
        idempotency_key = self.payload.idempotency_key or str(uuid4())
        self.generation_run, replayed = await reserve_generation_run(
            self.session,
            project_id=self.project_id,
            user_id=self.current_user.id,
            idempotency_key=idempotency_key,
            prompt=adaptation_reservation_prompt(
                self.payload.prompt, self.payload.restoration_adaptation
            ),
        )
        if replayed:
            if self.generation_run.response_payload is not None:
                response = PromptResponse.model_validate(self.generation_run.response_payload)
                return response.model_copy(
                    update={
                        "replayed": True,
                        "run_status": self.generation_run.status,
                    }
                )
            if self.generation_run.assistant_message_id is not None:
                return PromptResponse(
                    run_id=self.generation_run.id,
                    message_id=self.generation_run.assistant_message_id,
                    snapshot_id=None,
                    mode=cast(
                        Literal["build", "edit", "clarify"],
                        self.generation_run.response_mode or "build",
                    ),
                    replayed=True,
                    run_status=cast(
                        Literal[
                            "pending",
                            "running",
                            "cancel_requested",
                            "cancelled",
                            "completed",
                            "failed",
                        ],
                        self.generation_run.status,
                    ),
                )
            # The per-project advisory lock means a replay cannot observe the run
            # before its first transaction commits the assistant id and response.
            raise ApiError(
                "generation_active",
                "generation request is still being accepted",
                status.HTTP_409_CONFLICT,
                details={"active_run_id": str(self.generation_run.id)},
            )
        return None

    async def prepare_adaptation(self) -> None:
        if self.payload.restoration_adaptation is not None:
            adaptation = await prepare_adaptation(
                self.session,
                self.project,
                self.current_user.id,
                self.payload.restoration_adaptation,
                self.generation_run,
            )
            self.generation_run.agent_state = {
                **(self.generation_run.agent_state or {}),
                "restoration_adaptation": adaptation,
            }

        if self.payload.max_config_version is not None:
            from yleum_api.models.max_project_config import MaxProjectConfig

            config = await self.session.get(
                MaxProjectConfig, self.project_id, populate_existing=True
            )
            if (
                self.project.template != "max_miniapp"
                or config is None
                or config.owner_id != self.current_user.id
                or config.config_version != self.payload.max_config_version
            ):
                raise ApiError(
                    "source_changed",
                    "Данные приложения изменились. "
                    "Откройте настройки и примените актуальную версию.",
                    status.HTTP_409_CONFLICT,
                )

    async def check_admission(self) -> None:
        from yleum_api.services.secret_safety import contains_provider_secret

        self.credential_redirect = (
            self.project.template == "max_miniapp" and contains_provider_secret(self.payload.prompt)
        )

        # Snapshot absence alone cannot decide what the user wants after a failed
        # first build. Read the durable run state before billing/discovery/routing:
        # an explanation is a text turn, while explicit repair/new requirements keep
        # the normal build path. Query the immediately previous run (not any stale
        # historical failure) and exclude the pending run reserved above.
        _cur_snapshot = (
            await self.session.get(Snapshot, self.project.current_snapshot_id)
            if self.project.current_snapshot_id is not None
            else None
        )
        self.is_first_build = _cur_snapshot is None or _cur_snapshot.prompt_text is None
        if (
            self.payload.max_config_version is not None
            and self.is_first_build
            and _cur_snapshot is not None
        ):
            # Model-free config saves create technical snapshots. Follow this head's
            # ancestry, not unrelated historical branches, before choosing a rebuild.
            lineage = (
                select(Snapshot.id, Snapshot.parent_id, Snapshot.prompt_text)
                .where(
                    Snapshot.id == _cur_snapshot.id,
                    Snapshot.project_id == self.project_id,
                )
                .cte("max_config_lineage", recursive=True)
            )
            lineage = lineage.union(
                select(Snapshot.id, Snapshot.parent_id, Snapshot.prompt_text)
                .join(lineage, Snapshot.id == lineage.c.parent_id)
                .where(Snapshot.project_id == self.project_id)
            )
            generated_ancestor = await self.session.scalar(
                select(lineage.c.id)
                .where(
                    lineage.c.prompt_text.is_not(None),
                    func.length(func.trim(lineage.c.prompt_text)) > 0,
                )
                .limit(1)
            )
            self.is_first_build = generated_ancestor is None
        _previous_run = None
        if self.is_first_build:
            _previous_run = (
                await self.session.execute(
                    select(GenerationRun)
                    .where(
                        GenerationRun.project_id == self.project_id,
                        GenerationRun.user_id == self.current_user.id,
                        GenerationRun.id != self.generation_run.id,
                    )
                    .order_by(GenerationRun.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
        _failed_build_followup = (
            decide_failed_build_followup(self.payload.prompt)
            if _previous_run is not None
            and _previous_run.status == "failed"
            and _previous_run.response_mode == "build"
            else None
        )
        self.explain_failed_build = (
            not self.credential_redirect and _failed_build_followup == EXPLAIN_FAILED_BUILD
        )
        self.failed_build_reply = (
            _failed_build_explanation(_previous_run)
            if self.explain_failed_build and _previous_run is not None
            else None
        )

        # Free-tier gate: every project spends the owner's personal allowance.
        # `UNLIMITED_GENERATIONS=true` (testing escape hatch) forces every gen to be
        # free → skips this wallet-floor check AND the gateway debit (metadata.free).
        self.is_free = get_settings().unlimited_generations or (
            (self.current_user.free_generations_used or 0) < FREE_GENERATION_LIMIT
        )
        if not self.is_free and not self.credential_redirect and not self.explain_failed_build:
            account = await resolve_billing_account(self.session, self.current_user.id)
            wallet = (
                await self.session.execute(
                    select(Wallet).where(Wallet.billing_account_id == account.id)
                )
            ).scalar_one_or_none()
            if wallet is None or wallet.balance_rub < RESERVED_BALANCE:
                raise ApiError("wallet_empty", "insufficient balance", 402)

    async def route_existing_project(self) -> None:
        self.selected_dump = (
            [el.model_dump() for el in self.payload.selected_elements]
            if self.payload.selected_elements
            else None
        )

        # Smart triage — the server decides whether this prompt earns the full
        # Director(Opus)→Polish→Audit orchestration or a single cheap model. First
        # build, structural/backend/redesign change, or a batch of edits at once →
        # orchestrate; a lone cosmetic touch-up → cheap. Keeps Opus off "recolour
        # the login button" work so the client pays pennies for the long tail.
        # "First build" = the project has no real generation yet. A new project is
        # seeded with a STARTER snapshot (routers/projects.py — prompt_text=None), so
        # keying off `current_snapshot_id is None` mislabels EVERY first real prompt
        # as a follow-up and drops it to the cheap path. Treat "current snapshot is
        # the starter" (no prompt_text) as the first build instead.
        # Run/install intent (owner 2026-06-19): on a FOLLOW-UP, "как запустить / хочу
        # запустить / установщик / дай поиграть" → DON'T build; hand back a one-click
        # installer-download card (the .zip already ships a run.bat launcher), so the
        # user goes from ask → installer in one click. Gated to a project that already
        # has something built. Consumed in the turn-routing branch below.
        # A MAX Mini App has no installer — it is launched inside MAX — so there the
        # same words are an ordinary request for the builder.
        _has_built = (
            not self.is_first_build
            and self.project.current_snapshot_id is not None
            and self.project.template != "max_miniapp"
        )
        self.run_intent = _has_built and _infer_run_intent(self.payload.prompt)
        # «Спрашивай, если сомневаешься» (owner 2026-06-19): plausible-but-uncertain run
        # intent → ASK "собрать установщик?" with yes/no chips instead of guessing.
        # A decline ("нет, доработать") gets a short "what to change?" reply, not a
        # garbage build. Strong intent above always wins.
        self.run_decline = (
            _has_built and not self.run_intent and _is_run_decline(self.payload.prompt)
        )
        self.run_ask = (
            _has_built
            and not self.run_intent
            and not self.run_decline
            and _infer_run_intent_maybe(self.payload.prompt)
        )

        # Onboarding-survey palette pick (owner 2026-06-19): the popup submits the
        # chosen design preset here so the build uses it directly. Validated against
        # the known catalog (an unknown id is ignored); persisted with the project
        # changes the handler commits below. Additive — absent on normal prompts.
        if self.payload.design_preset_id:
            from yleum_api.services.design_presets import PRESETS as _PRESETS

            if self.payload.design_preset_id in _PRESETS:
                self.project.design_preset_id = self.payload.design_preset_id

        # ── Onboarding interview routing ──────────────────────────────────────
        # On a brand-new project we don't build straight away: we first run a short
        # discovery so the first generation works from a real brief, not a one-line
        # idea. Two regimes (progressive supersedes the legacy batch clarify):
        #   * progressive discovery (default) — ask ONE elementary question at a time,
        #     adapt, and build when the model decides it has enough (services/discovery).
        #   * legacy clarify — a single batch of 3–4 questions (kept as a flag fallback).
        # A select-mode pick, an explicit skip_clarify, or a non-first-build prompt all
        # bypass the interview entirely and go straight to generation.
        self.settings = get_settings()

        # Code→web pivot (owner 2026-06-19): a `code` project has no live preview. A
        # FOLLOW-UP asking to RUN it as a web page ("сделай веб-вид", "в браузере",
        # "запусти здесь") flips it onto the previewable `static` web template (instant
        # /p/<slug>, no container) so the next build is an openable page. Non-destructive
        # (existing source stays — the build PORTS it). Gated to a real follow-up on a
        # code project + explicit web intent → never touches first-build routing or a
        # normal code edit. Fail-soft (R-10). `pivoted_to_web` forces the full-build
        # path below (a fresh page is a build, not a surgical edit).
        self.pivoted_to_web = False
        if (
            not self.is_first_build
            and self.project.template == "code"
            and self.settings.use_auto_stack_routing
            and _infer_web_pivot(self.payload.prompt)
        ):
            try:
                self.pivoted_to_web = await stack_routing.pivot_code_to_web(
                    self.session, self.project
                )
            except Exception as _pv_exc:
                await self.session.rollback()
                # A failed commit leaves `project` expired; a later attribute access
                # would lazy-load on the poisoned session → MissingGreenlet → 500 for
                # the WHOLE request. Re-fetch a clean, attached instance so the build
                # proceeds normally (as the un-pivoted project).
                self.project = await self.session.get(Project, self.project_id) or self.project
                logging.getLogger(__name__).warning("code→web pivot failed: %r", _pv_exc)

        # Static→app escalation (P-H1, owner 2026-06-21): a FOLLOW-UP on a STATIC
        # project that clearly asks to become a real app ("переделай в полноценное
        # приложение: вход, кабинет, база записей") must escalate the stack
        # static→container, not surgical-edit the flat page — the H1 blind spot (until
        # now a follow-up could NEVER escalate, since `switch_to_stack` runs only inside
        # the first-build branch). Non-destructive, like the code→web pivot above:
        # `pivot_static_to_app` flips the template only — the static snapshot stays
        # rollback-able, the orchestrated build writes the app on top, and the container
        # scaffold comes from the orchestrator (nextjs_entities has no api-side scaffold
        # dir). Gated to a real follow-up on a static-class project (not a container,
        # not a `code` project — those have their own pivots) + a confident
        # app-ification ask, behind the default-OFF flag. `escalated_to_app` forces the
        # full-build path below (a freshly-templated app is a BUILD, not a surgical
        # edit). Fail-soft (R-10): a hiccup falls back to the un-escalated project.
        self.escalated_to_app = False
        if (
            not self.is_first_build
            and not self.selected_dump
            and not is_fullstack(self.project.template)
            and self.project.template != "code"
            and self.settings.use_followup_appification
            and self.settings.use_auto_stack_routing
            and detect_appification(self.payload.prompt)
        ):
            _esc_stack = _infer_stack_from_text(self.payload.prompt)  # → nextjs_entities
            if _esc_stack:
                try:
                    self.escalated_to_app = bool(
                        await stack_routing.pivot_static_to_app(
                            self.session, self.project, _esc_stack
                        )
                    )
                except Exception as _esc_exc:
                    await self.session.rollback()
                    self.project = await self.session.get(Project, self.project_id) or self.project
                    logging.getLogger(__name__).warning(
                        "static→app escalation failed: %r", _esc_exc
                    )

    async def conduct_interview(self) -> None:
        self.discovery_result: DiscoveryResult | None = None
        # Async onboarding: set when the slow first-turn plan is deferred out of the
        # request (delivered over WS). No build/ASK this turn — a placeholder streams
        # now, the survey arrives via `onboarding.survey` (see _run_async_onboarding).
        self.async_onboarding = False
        self.do_clarify = False
        self.effective_prompt = self.payload.prompt
        interview_eligible = (
            self.is_first_build
            and not self.payload.skip_clarify
            and not self.selected_dump
            and not self.credential_redirect
            and not self.explain_failed_build
        )
        if interview_eligible and self.settings.use_progressive_discovery:
            # Gather the prior conversation (questions already asked + answers) to
            # drive the next discovery turn. The newest message (payload.prompt) is
            # passed separately — it isn't persisted yet.
            _rows = list(
                (
                    await self.session.execute(
                        select(Message)
                        .where(Message.project_id == self.project_id)
                        .order_by(Message.created_at.asc())
                        # 40 so discovery sees the whole interview thread, not just the
                        # last ~20 rows, before it classifies/builds (owner 2026-06-18).
                        .limit(40)
                    )
                )
                .scalars()
                .all()
            )
            _history = [{"role": m.role, "content": m.content} for m in _rows if m.content]
            _asked = sum(1 for m in _rows if m.role == "assistant")
            # Only the FIRST batch-discovery turn pays the ~60-70s Opus plan call; every
            # later turn serves the stashed plan with no gateway hop. Defer just that
            # first turn out of the request (survey over WS) so POST returns inside the
            # 30s client budget. The predicate mirrors _batch_discovery_turn's own
            # plan-needed gate (asked_count==0, no stashed plan, not a code prompt, no
            # zero-question shortcut) so OFF is byte-identical to the legacy path.
            if (
                self.settings.use_async_onboarding
                and self.settings.use_batch_discovery
                and _asked == 0
                and not self.project.discovery_plan
                and not wants_build_now(self.payload.prompt)
                and not _infer_code_from_text(self.payload.prompt)
                and zero_question_build(_history, self.payload.prompt) is None
            ):
                self.async_onboarding = True
            elif self.settings.use_batch_discovery:
                # Plan all 3–4 product-tailored questions in ONE upfront pass, then
                # serve them with zero per-question wait (owner rule 13 #1). The plan
                # is stashed on ``project`` and persisted by the commit below.
                self.discovery_result = await _batch_discovery_turn(
                    self.project,
                    _history,
                    self.payload.prompt,
                    asked_count=_asked,
                    force_build=wants_build_now(self.payload.prompt),
                    language=self.project.language,
                )
            else:
                self.discovery_result = await run_discovery(
                    _history,
                    self.payload.prompt,
                    asked_count=_asked,
                    force_build=wants_build_now(self.payload.prompt),
                    language=self.project.language,
                )
            # `discovery_result is None` on the async-onboarding turn (the plan is
            # deferred to a background task) — no BUILD to compose this turn.
            if (
                self.discovery_result is not None
                and self.discovery_result.action == DISCOVERY_BUILD
            ):
                # Build now — the compiled brief (with the recommended stack folded in)
                # becomes the generator's prompt; the raw idea stays as the user turn.
                self.effective_prompt = _compose_build_prompt(self.discovery_result)
                # Auto stack-routing: if discovery picked a container stack for this
                # still-static project, flip the template + re-scaffold its git +
                # provision the dev container, so the build yields a real app instead
                # of a flat page. Fail-soft (R-10) — a hiccup falls back to a static
                # build rather than dead-ending onboarding.
                if self.settings.use_auto_stack_routing:
                    _build_stack: str | None = self.discovery_result.stack
                    # Real-time net on the FULL conversation intent, not just this turn's
                    # trigger. A skip-survey / "Постройте сейчас" build sends a trigger
                    # phrase with NO "мессенджер"/"чат" word, so discovery's stack pick
                    # (which weighs the current turn) loses the original intent and ships
                    # an spa/entities dashboard — the owner's "создай мессенджер → не чат"
                    # failure (live: prompt «создай мессенджер», trigger «Постройте
                    # сейчас» → spa dashboard). Re-check across EVERY user message so the
                    # original messenger intent still forces the realtime chat stack.
                    try:
                        _full_intent = (
                            " ".join(m["content"] for m in _history if m.get("role") == "user")
                            + " "
                            + self.payload.prompt
                        )
                        _resolved_stack = _resolve_messenger_stack(_build_stack, _full_intent)
                        if _resolved_stack != _build_stack:
                            logging.getLogger(__name__).info(
                                "discovery-build messenger override (full intent): '%s'→'%s'",
                                _build_stack,
                                _resolved_stack,
                            )
                            _build_stack = _resolved_stack
                    except Exception as _rt_exc:
                        logging.getLogger(__name__).warning(
                            "realtime full-intent inference failed: %r", _rt_exc
                        )
                    try:
                        await stack_routing.switch_to_stack(
                            self.session, self.project, _build_stack or ""
                        )
                    except Exception as _sr_exc:
                        await self.session.rollback()
                        logging.getLogger(__name__).warning(
                            "stack_routing switch failed (static fallback): %r", _sr_exc
                        )
                # Persist the chip→spec the user steered onboarding toward, so
                # downstream gates can check the live render against what was picked
                # (V2.5.0). Set AFTER stack-routing so a routing rollback can't wipe
                # it; committed with the message rows below. Fail-soft (R-10) — a
                # marshalling hiccup must never block the build.
                try:
                    _spec = spec_from_discovery(_history, self.payload.prompt)
                    self.project.discovery_spec = _spec.to_dict() if _spec else None
                except Exception as _ds_exc:
                    logging.getLogger(__name__).warning(
                        "discovery_spec marshal failed (skipping): %r", _ds_exc
                    )
            # else: ASK — we stream the question below, no generation this turn.
        elif interview_eligible and self.settings.use_clarify_interview:
            # Legacy batch clarify (fires only with zero prior messages so the
            # answers — the NEXT message — generate normally). Reply "генерируй" to skip.
            _has_prior_msg = (
                await self.session.execute(
                    select(Message.id).where(Message.project_id == self.project_id).limit(1)
                )
            ).first() is not None
            self.do_clarify = not _has_prior_msg

        self.discovery_ask = (
            self.discovery_result is not None and self.discovery_result.action != DISCOVERY_BUILD
        )

    async def route_first_build(self) -> None:
        if (
            self.is_first_build
            and not self.credential_redirect
            and not self.explain_failed_build
            and self.discovery_result is None
            and not self.discovery_ask
            and not self.do_clarify
            and not self.async_onboarding
            and not self.selected_dump
            and self.settings.use_auto_stack_routing
        ):
            # Routing intent across the WHOLE conversation, not just this turn. The
            # skip-survey build trigger is «Постройте сейчас» (no messenger/chat word),
            # so a realtime net on payload.prompt alone misses and ships an spa/entities
            # dashboard — the owner's "создай мессенджер → не чат" failure (live: prompt
            # «создай мессенджер», skip → trigger «Постройте сейчас» → spa). Pull the
            # project's prior user messages so the original intent still routes.
            _fb_intent = self.payload.prompt
            try:
                _prior_user = list(
                    (
                        await self.session.execute(
                            select(Message.content)
                            .where(
                                Message.project_id == self.project_id,
                                Message.role == "user",
                            )
                            .order_by(Message.created_at.asc())
                            .limit(40)
                        )
                    )
                    .scalars()
                    .all()
                )
                _fb_intent = " ".join([*(c for c in _prior_user if c), self.payload.prompt])
            except Exception as _fbi_exc:
                logging.getLogger(__name__).warning(
                    "first-build intent load failed (using current turn): %r", _fbi_exc
                )
            # Code intent (program/script, any language) takes priority over the
            # backend net — "напиши скрипт на python" must not be pulled into an
            # auth-backed web app (owner 2026-06-18). And static is opt-in: if nothing
            # specific fires, default to `spa` (interactive React) unless the user
            # EXPLICITLY asked for a plain static HTML page — same policy as discovery.
            if self.settings.use_result_type_router:
                # RT-1: decide the RESULT TYPE first (semantic LLM + keyword safety-net),
                # then map type→stack. Each sub-slice is independently gated so OFF = the
                # legacy net behaviour, byte-identical.
                _rt_llm, _rt_conf = await classify_result_type(
                    self.payload.prompt, language=self.project.language
                )
                _rt = resolve_result_type(self.payload.prompt, _rt_llm, _rt_conf)
                if _rt is None:
                    # Unsure → legacy nets (preserves today's behaviour for the un-typed tail).
                    _inferred_stack = (
                        _infer_code_from_text(self.payload.prompt)
                        or _infer_stack_from_text(self.payload.prompt)
                        or (None if _explicit_static(self.payload.prompt) else "spa")
                    )
                else:
                    # web_app from FRAMING only ships when the appify slice is on; without
                    # it (and without a real account ask) framing falls back to today's path.
                    if (
                        _rt == "web_app"
                        and not self.settings.result_type_firstbuild_appify
                        and not _has_account_intent(self.payload.prompt)
                        and _infer_stack_from_text(self.payload.prompt) != "nextjs_entities"
                    ):
                        # web_app from FRAMING alone (no real backend signal) demotes when
                        # the appify slice is off. But a genuine data/CRUD backend prompt
                        # (legacy net → nextjs_entities) keeps web_app regardless — never
                        # downgrade a real backend app to a no-backend spa.
                        _rt = "landing" if _has_conversion_intent(self.payload.prompt) else "site"
                    # landing→entities suppression (BS-7) ships only with the lead-sink slice.
                    if (
                        _rt == "landing"
                        and not self.settings.result_type_landing_lead_sink
                        and _infer_stack_from_text(self.payload.prompt) == "nextjs_entities"
                    ):
                        _inferred_stack = "nextjs_entities"  # keep today's escalation
                    else:
                        _inferred_stack = result_type_to_stack(_rt)
                        if _inferred_stack == "static" and not _explicit_static(
                            self.payload.prompt
                        ):
                            _inferred_stack = "spa"
            else:
                _inferred_stack = (
                    _infer_code_from_text(self.payload.prompt)
                    or _infer_stack_from_text(self.payload.prompt)
                    or (None if _explicit_static(self.payload.prompt) else "spa")
                )
            # Real-time override (G001): a messenger / chat / live-feed / collab prompt
            # is a REAL-TIME app, not a CRUD entities app — on nextjs_entities the
            # "messages" become a refresh-to-see TABLE, never a live chat (the #1
            # "опять эти entities" messenger failure). The result-type router has no
            # realtime type (web_app → nextjs_entities), so the realtime net is bypassed
            # on this path; force realtime here when the messenger/chat net fires (and
            # the user didn't ask for plain static or a script). Mirrors the realtime
            # net in discovery.plan_discovery.
            _resolved_stack = _resolve_messenger_stack(_inferred_stack, _fb_intent)
            if _resolved_stack != _inferred_stack:
                logging.getLogger(__name__).info(
                    "first-build messenger override: '%s'→'%s' (full intent)",
                    _inferred_stack,
                    _resolved_stack,
                )
                _inferred_stack = _resolved_stack
            if _inferred_stack:
                try:
                    await stack_routing.switch_to_stack(self.session, self.project, _inferred_stack)
                except Exception as _sr_exc:
                    await self.session.rollback()
                    logging.getLogger(__name__).warning(
                        "first-build stack_routing switch failed (static fallback): %r",
                        _sr_exc,
                    )

    async def select_intent_and_model(self) -> None:
        intent = decide_intent(
            self.effective_prompt,
            # A code→web pivot or a static→app escalation just re-templated the project;
            # the new page/app doesn't exist yet, so it's a full BUILD, never a surgical
            # edit — treat it like a first build for triage (owner 2026-06-19; P-H1).
            is_first_prompt=self.is_first_build or self.pivoted_to_web or self.escalated_to_app,
            selected_count=len(self.selected_dump or []),
            # App-ification triage rule (P-H1), flag-gated so it's a no-op until enabled.
            appify_enabled=self.settings.use_followup_appification,
        )
        self.orchestrate = (
            intent == ORCHESTRATE and not self.credential_redirect and not self.explain_failed_build
        )

        # Model choice is server-side — the user never picks. `force_model` is the
        # hidden admin override (env FORCE_MODEL). Otherwise the triage decides:
        # orchestrate → director (Opus) drives prompt-routing + Director→Polish;
        # cheap → a single reliable Haiku shot (role `edit`).
        self.force_model = self.settings.force_model or None
        self.routing_model = self.force_model or model_for_role(
            "director" if self.orchestrate else "edit"
        )

    async def persist_messages(self) -> None:
        _now = datetime.now(UTC)
        self.user_msg = Message(
            project_id=self.project_id,
            role="user",
            content=(
                redact_provider_secrets(self.payload.prompt)
                if self.credential_redirect
                else self.payload.prompt
            ),
            model_id=None,  # user turns have no model
            selected_elements=self.selected_dump,
            created_at=_now,
        )
        self.assistant_msg = Message(
            project_id=self.project_id,
            role="assistant",
            content="",
            # Orchestrated runs carry the mix label; cheap runs log the real model.
            model_id=self.force_model
            or (ORCHESTRATION_LABEL if self.orchestrate else self.routing_model),
            created_at=_now + timedelta(milliseconds=1),
        )
        self.session.add_all([self.user_msg, self.assistant_msg])

        # Detect & persist the project language ONCE, on the first build.  The
        # first prompt is the strongest language signal we have.  A connected
        # user's explicit `default_language` preference beats auto-detection.
        # Fail-soft (R-10): any hiccup must never block the build.
        if self.is_first_build and self.project.language == "ru":
            try:
                from yleum_api.services.lang_detect import detect_language

                detected = (
                    self.current_user.default_language
                    if self.current_user.default_language
                    else detect_language(self.payload.prompt)
                )
                if detected and detected != self.project.language:
                    self.project.language = detected
            except Exception as _ld_exc:
                import logging as _logging

                _logging.getLogger(__name__).warning(
                    "language detection failed (keeping default): %r", _ld_exc
                )

        # Persist the durable run identity in the same transaction as the two chat
        # rows. A retry can now replay this exact assistant id, while a different
        # request sees the active-run guard and cannot start in parallel.
        self.turn_mode: Literal["build", "edit", "clarify"] = (
            "clarify"
            if (
                self.credential_redirect
                or self.explain_failed_build
                or self.discovery_ask
                or self.async_onboarding
                or self.do_clarify
                or self.run_intent
                or self.run_ask
                or self.run_decline
            )
            else ("build" if self.orchestrate else "edit")
        )
        await self.session.flush()
        self.generation_run.assistant_message_id = self.assistant_msg.id
        self.generation_run.user_message_id = self.user_msg.id
        self.generation_run.response_mode = self.turn_mode
        from yleum_api.services.project_versions import ensure_generation_version

        await ensure_generation_version(self.session, self.generation_run, self.project)
        await self.session.commit()
        await self.session.refresh(self.user_msg)
        await self.session.refresh(self.assistant_msg)

    async def dispatch(self) -> None:
        if self.credential_redirect:
            _spawn_text_turn(
                self.project_id,
                self.assistant_msg.id,
                (
                    "Ключ не сохранён и не передан агенту. Подключите провайдера через "
                    "«Интеграции» — там секрет хранится зашифрованно и не попадает в код "
                    "или историю проекта. Если ключ уже был отправлен в чат, отзовите его "
                    "у провайдера и создайте новый."
                ),
                run_id=self.generation_run.id,
            )
        elif self.explain_failed_build:
            assert self.failed_build_reply is not None
            _spawn_text_turn(
                self.project_id,
                self.assistant_msg.id,
                self.failed_build_reply,
                run_id=self.generation_run.id,
            )
        elif self.async_onboarding:
            # Deferred first-turn onboarding: no gateway call ran in-request. Plan the
            # question batch out of band (Opus ~60-70s) and deliver the survey over WS
            # so POST already returned inside the client's 30s budget.
            _spawn_async_onboarding(
                self.project_id,
                self.assistant_msg.id,
                self.payload.prompt,
                self.project.language,
                run_id=self.generation_run.id,
            )
        elif self.discovery_ask:
            # Progressive discovery: stream the next short question, no build this
            # turn. The user's reply (next message) continues the discovery; the
            # generator only runs once discovery decides it has enough.
            assert self.discovery_result is not None  # discovery_ask ⇒ result exists
            _spawn_text_turn(
                self.project_id,
                self.assistant_msg.id,
                self.discovery_result.message,
                run_id=self.generation_run.id,
            )
        elif self.do_clarify:
            # Ask first — no generation this turn. The user's answers (next message)
            # flow into the real build via chat history.
            _spawn_clarify(
                self.project_id,
                self.assistant_msg.id,
                self.payload.prompt,
                run_id=self.generation_run.id,
                language=self.project.language,
            )
        elif self.run_intent:
            # Run/install intent (owner 2026-06-19): no build — stream a one-click
            # installer-download card. The user clicks «Скачать установщик», gets the
            # .zip (with run.bat), double-clicks → installed + running.
            _spawn_text_turn(
                self.project_id,
                self.assistant_msg.id,
                _INSTALL_CARD_TEXT,
                run_id=self.generation_run.id,
            )
        elif self.run_decline:
            # Declined the installer offer → don't build from a bare "нет"; ask what
            # to change so the next turn is a real edit.
            _spawn_text_turn(
                self.project_id,
                self.assistant_msg.id,
                _RUN_DECLINE_REPLY,
                run_id=self.generation_run.id,
            )
        elif self.run_ask:
            # Uncertain run intent → ASK "собрать установщик?" (yes/no chips set on the
            # response below); no build this turn.
            _spawn_text_turn(
                self.project_id,
                self.assistant_msg.id,
                _RUN_ASK_TEXT,
                run_id=self.generation_run.id,
            )
        else:
            store_generation_dispatch(
                self.generation_run,
                GenerationDispatch(
                    schema_version=1,
                    project_id=self.project_id,
                    user_id=self.current_user.id,
                    user_message_id=self.user_msg.id,
                    assistant_message_id=self.assistant_msg.id,
                    current_snapshot_id=self.project.current_snapshot_id,
                    prompt_text=self.effective_prompt,
                    model_id=self.routing_model,
                    force_model=self.force_model,
                    is_free=self.is_free,
                    orchestrate=self.orchestrate,
                    selected_elements=self.selected_dump,
                ),
            )
            capacity_dispatch_token = uuid4() if self.project.template == "max_miniapp" else None
            if self.project.template == "max_miniapp" and get_settings().use_generation_worker:
                self.generation_run.execution_backend = "worker"
            await self.session.commit()
            if self.generation_run.execution_backend == "api":
                _spawn_process_prompt(
                    run_id=self.generation_run.id,
                    capacity_dispatch_token=capacity_dispatch_token,
                    project_id=self.project_id,
                    user_id=self.current_user.id,
                    user_message_id=self.user_msg.id,
                    assistant_message_id=self.assistant_msg.id,
                    current_snapshot_id=self.project.current_snapshot_id,
                    # On a discovery BUILD this is the compiled brief; otherwise the raw
                    # prompt. The full Q&A still rides along via chat history.
                    prompt_text=self.effective_prompt,
                    model_id=self.routing_model,
                    force_model=self.force_model,
                    is_free=self.is_free,
                    orchestrate=self.orchestrate,
                    selected_elements=self.selected_dump,
                )

    async def present_response(self) -> PromptResponse:
        try:
            await get_redis().publish(f"activity:{self.project_id}", "")
        except Exception:
            pass

        # Quick-reply chips ride on the ASK turn only — the workspace renders them
        # under the streamed question so the user can tap an answer instead of typing.
        if self.discovery_ask:
            assert self.discovery_result is not None  # discovery_ask ⇒ result exists
            ask_choices = list(self.discovery_result.choices)
            allow_custom = self.discovery_result.allow_custom
            multi_select = self.discovery_result.multi_select
            # Onboarding-frame metadata (pillar 2): «Вопрос N из M» + niche banner.
            # 0 → None so the workspace hides the counter on the legacy per-question
            # path (no upfront plan → unknown total).
            question_index = self.discovery_result.question_index or None
            question_total = self.discovery_result.question_total or None
            niche = self.discovery_result.niche or None
            recap = list(self.discovery_result.recap)
            design_preview = self.discovery_result.design_preview
            # Owner 2026-06-19 — on the FIRST discovery turn return the WHOLE survey
            # (every planned question + a clickable palette question) so the workspace
            # renders ONE popup form, not a chat turn per question. Only the first turn
            # (index 1) with a stashed plan; follow-up turns keep the single-question
            # fields (back-compat for a client that ignores `survey`).
            survey = (
                _build_onboarding_survey(self.project.discovery_plan)
                if question_index == 1
                else None
            )
        else:
            # Uncertain run-intent question (owner 2026-06-19): tappable yes/no chips.
            # «Да…» re-enters as strong run-intent → installer card; «Нет…» is caught
            # as a decline → "what to change?" reply.
            ask_choices = (
                ["Да, собрать установщик", "Нет, доработать проект"] if self.run_ask else []
            )
            allow_custom = True
            multi_select = False
            question_index = None
            question_total = None
            niche = None
            recap = []
            design_preview = None
            survey = None
        response = PromptResponse(
            run_id=self.generation_run.id,
            message_id=self.assistant_msg.id,
            snapshot_id=None,
            mode=self.turn_mode,
            replayed=False,
            run_status=cast(
                Literal[
                    "pending",
                    "running",
                    "cancel_requested",
                    "cancelled",
                    "completed",
                    "failed",
                ],
                self.generation_run.status,
            ),
            choices=ask_choices,
            allow_custom=allow_custom,
            multi_select=multi_select,
            question_index=question_index,
            question_total=question_total,
            niche=niche,
            recap=recap,
            design_preview=design_preview,
            survey=survey,
            survey_pending=self.async_onboarding,
        )
        self.generation_run.response_payload = response.model_dump(mode="json")
        await self.session.commit()
        return response
