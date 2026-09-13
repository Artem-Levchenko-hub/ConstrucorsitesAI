from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from omnia_api.core.config import (
    get_settings,
    model_for_role,
    tier_for_model,
)
from omnia_api.core.redis import (
    publish_event,
    set_stream_state,
)
from omnia_api.schemas.project import CONTAINER_BROWSER_TEMPLATES as CONTAINER_NEXT
from omnia_api.services.art_director_writer import (
    art_director_writer_generate,
    supports_app_brief,
)
from omnia_api.services.director_polish import director_polish_generate
from omnia_api.services.generation.contracts import (
    GenerationIds,
    ProjectGenerationFacts,
)
from omnia_api.services.generation.file_transforms import _with_vendor_directive
from omnia_api.services.generation.progress import MessageStream
from omnia_api.services.llm_client import stream_chat_completion
from omnia_api.services.multipass_generator import multipass_generate
from omnia_api.services.prompt_builder import build_art_director_system

_log = logging.getLogger("omnia_api.routers.messages")


@dataclass
class ModelPassResult:
    text: str = ""
    usage: dict[str, Any] | None = None
    error: Any = None


@dataclass
class ModelStream:
    ids: GenerationIds
    project_info: ProjectGenerationFacts
    prompt_text: str
    model_id: str
    force_model: str | None
    orchestrate: bool
    generation_mode: str
    messages: list[dict[str, str]]
    multipass_set: frozenset[str]
    pub: MessageStream
    last_attempt: ModelPassResult = field(default_factory=ModelPassResult)
    freeform_started: bool = False
    brief: Any = None

    async def emit_stage(self, pass_name: str, stage: str) -> None:
        if not self.freeform_started:
            return
        try:
            await publish_event(
                self.ids.project_id,
                "llm.pass",
                {
                    "message_id": str(self.ids.assistant_message_id),
                    "pass": pass_name,
                    "stage": stage,
                },
            )
        except Exception:
            pass

    async def run(
        self,
        use_model: str,
        *,
        force_multipass: bool = False,
        force_single_shot: bool = False,
        force_all: str | None = None,
        allow_art_director: bool = True,
    ) -> None:
        """Drain one stream from the gateway into `state`.

        For models in the multipass set (or when the caller explicitly
        passes `force_multipass=True`), runs through the multi-pass
        generator (skeleton → assembly) instead of a single shot. Both
        paths yield the same event shape downstream, so the file
        extractor and fallback loop don't care which one ran.

        `force_multipass=True` is the A.5 escape hatch — when a single
        shot returns junk we retry the *same* model via multipass before
        switching models, because the failure mode is usually
        prompt-overwhelm (cheap model loses focus over 28+ KB) rather
        than the model being incapable.

        `force_single_shot=True` pins the plain single-shot path even for
        a cheap model that would otherwise be in the multipass set. Used
        by targeted fix passes (dead-link repair) that must edit the
        existing files, not regenerate the whole page via multipass.
        """
        self.last_attempt.text = ""
        self.last_attempt.usage = None
        self.last_attempt.error = None

        # Phase L7 — Director→Polish 2-pass branch. Wins when the
        # operator has opted into both `USE_SECTION_CATALOG` AND
        # `USE_DIRECTOR_POLISH`, AND the model is in the premium
        # tier. Catalog already gives us JSON IR; Director→Polish
        # splits structural choice and content polish across two
        # calls of the same model for higher final quality at the
        # cost of latency × 2 / tokens × 2.
        _settings = get_settings()
        # Director→Polish is the standard catalog path. `force_all` is None on
        # the orchestrated first pass (each pass uses its role model — Opus
        # director, DeepSeek polish) and is set to a single model id when an
        # admin override or an empty-response fallback forces ONE model across
        # every pass. The premium gate still holds: the routing model is the
        # director (Opus).
        # `orchestrate` is the triage verdict (closure from _process_prompt).
        # When False (a cheap targeted edit) we skip BOTH Director→Polish AND
        # the 4-pass multipass and run a single reliable shot — so "recolour
        # the button" never spins up the premium pipeline and burns budget.
        # FIXED build orchestration (owner 2026-06-01): Art-Director (Opus)
        # writes the ultra-detailed brief, Writer (DeepSeek) executes it into
        # HTML. Wins for EVERY orchestrated build when the flag is on —
        # independent of model tier, so the design pipeline is never silently
        # downgraded to plain/catalog. A forced single model (admin override /
        # empty-response fallback → force_all) or a cheap targeted edit
        # (orchestrate=False) skips it and keeps its own single model.
        _adw_active = (
            allow_art_director
            and not force_single_shot
            and not force_all
            and self.orchestrate
            and _settings.use_art_director_freeform
            # The writer emits freeform HTML, so only run when the pipeline
            # parses HTML (freeform). On prod freeform = 100%, so this is
            # always-on for builds; it just avoids feeding HTML to the
            # catalog/plain JSON parser if freeform is ever switched off.
            and self.generation_mode == "freeform"
            # Container-backed stacks WITHOUT a .tsx writer variant
            # (fullstack / spa) stay off this path: the art-director writer's
            # default pass emits ONE static index.html, the wrong artifact for
            # a React app. But app stacks that DO have a dedicated .tsx writer
            # (nextjs_entities — art_director_writer._APP_TEMPLATES /
            # _WRITER_INSTRUCTION_TEMPLATE_APP) get the SAME 2-pass art-
            # direction: an APP brief (oklch theme tokens + IA) → a .tsx writer
            # that honours it → the omnia:brief event. Without this the
            # flagship entity apps fell through to a bare single-shot .tsx —
            # no brief, hardcoded colours, dead narration/swatches. Flag =
            # instant rollback to that single-shot path.
            and (
                self.project_info.template not in CONTAINER_NEXT
                or (
                    _settings.use_art_director_entities
                    and supports_app_brief(self.project_info.template)
                )
            )
        )
        _dp_active = (
            not force_single_shot
            and self.orchestrate
            and _settings.use_director_polish
            and _settings.use_section_catalog
            and tier_for_model(use_model) == "premium"
            # Freeform mode writes HTML directly — no Director→Polish 2-pass
            # (that path is a catalog/IR enhancement). The acceptance gate
            # is freeform's quality mechanism instead.
            and self.generation_mode != "freeform"
            # Catalog/IR (Director→Polish) renders static HTML — never for a
            # container-backed Next.js app, which needs .tsx files.
            and self.project_info.template not in CONTAINER_NEXT
        )
        if _adw_active:
            # Mark the freeform path so the post-writer image-resolve and
            # design-judge stages emit their llm.pass progress (4-segment bar).
            self.freeform_started = True
            # Infra cost (2026-06-16): pass 1 (Art-Director, prose brief) gets
            # a brief-lean system that drops the code-implementation blocks it
            # never uses; pass 2 (the writer) keeps the FULL `messages` system,
            # so the final HTML is unchanged. Fail-soft → full prompt on any
            # error or when the flag is off.
            _ad_system: str | None = None
            if _settings.use_lean_art_director_prompt:
                try:
                    _ad_system = build_art_director_system(
                        self.project_info.template,
                        self.project_info.design_preset_id,
                        self.project_info.image_gen_enabled,
                        model_id=self.model_id,
                        project_id=str(self.ids.project_id),
                        user_prompt=self.prompt_text,
                        discovery_spec=self.project_info.discovery_spec,
                    )
                except Exception as _ad_exc:
                    _log.warning("lean art-director prompt failed, using full: %r", _ad_exc)
                    _ad_system = None
            source = art_director_writer_generate(
                base_messages=self.messages,
                user_prompt=self.prompt_text,
                art_director_model=model_for_role("art_director", override=self.force_model),
                writer_model=model_for_role("freeform_writer", override=self.force_model),
                user_id=self.ids.user_id,
                project_id=self.ids.project_id,
                message_id=self.ids.assistant_message_id,
                template=self.project_info.template,
                art_director_system=_ad_system,
                language=self.project_info.language,
            )
        elif _dp_active:
            source = director_polish_generate(
                base_messages=self.messages,
                user_prompt=self.prompt_text,
                director_model=force_all,
                polish_model=force_all,
                user_id=self.ids.user_id,
                project_id=self.ids.project_id,
                message_id=self.ids.assistant_message_id,
                language=self.project_info.language,
            )
        elif (
            not force_single_shot
            and self.orchestrate
            and (force_multipass or use_model in self.multipass_set)
        ):
            source = multipass_generate(
                base_messages=self.messages,
                user_prompt=self.prompt_text,
                model=force_all,
                user_id=self.ids.user_id,
                project_id=self.ids.project_id,
                message_id=self.ids.assistant_message_id,
            )
        else:
            # B2 — the `single_shot` role (Opus) owns the non-catalog
            # freeform fallback: with catalog/IR OFF an orchestrated build
            # still needs one strong model. With catalog ON (prod default)
            # this branch only runs for cheap targeted edits / forced
            # single-shot fixes, which keep their own model — single_shot
            # must never drag a "recolour the button" tweak onto Opus.
            if (
                not force_all
                and not force_single_shot
                and self.orchestrate
                and not _settings.use_section_catalog
            ):
                _ss_model = model_for_role("single_shot", override=self.force_model)
            elif (
                not force_all
                and not force_single_shot
                and self.orchestrate
                and self.generation_mode == "freeform"
            ):
                # The leap: premium freeform writes the whole page as bespoke
                # HTML — the design-critical pass — so it runs on the strongest
                # model via the `freeform_writer` role (Opus), not whatever the
                # route resolved. Same guards as single_shot above: a cheap
                # targeted edit (force_* / not orchestrate) never lands here, so
                # a small tweak can't drag onto Opus.
                _ss_model = model_for_role("freeform_writer", override=self.force_model)
            else:
                _ss_model = force_all or use_model
            # Per-vendor directive on the freeform/single-shot path. IR JSON
            # is expected ONLY on premium + catalog mode; otherwise the model
            # emits freeform HTML, so json_strict must stay False (a "JSON
            # only" nudge would corrupt an HTML response).
            # IR JSON is expected ONLY on premium + catalog mode. Freeform
            # emits HTML, so a "JSON only" vendor nudge would corrupt the
            # response — never set json_strict in freeform mode.
            _expects_ir = (
                _settings.use_section_catalog
                and self.generation_mode != "freeform"
                and tier_for_model(_ss_model) == "premium"
                # Fullstack/entity apps emit .tsx <file> blocks, never PageIR
                # JSON — a "JSON only" vendor nudge would corrupt the response.
                and self.project_info.template not in CONTAINER_NEXT
            )
            source = stream_chat_completion(
                _with_vendor_directive(self.messages, _ss_model, json_strict=_expects_ir),
                _ss_model,
                str(self.ids.user_id),
                str(self.ids.project_id),
                str(self.ids.assistant_message_id),
            )

        async for event in source:
            if "delta" in event:
                self.last_attempt.text = str(self.last_attempt.text) + event["delta"]
                self.pub.seq = int(self.pub.seq) + 1
                self.pub.content = str(self.pub.content) + event["delta"]
                await publish_event(
                    self.ids.project_id,
                    "llm.chunk",
                    {
                        "message_id": str(self.ids.assistant_message_id),
                        "delta": event["delta"],
                        # Monotonic per-message counter — lets a reconnecting
                        # client dedup buffered vs live deltas and detect gaps.
                        "seq": int(self.pub.seq),
                    },
                )
                # Mirror the cumulative client-visible content into Redis on
                # every delta so a reconnect (F5) resyncs to the exact current
                # state — no hole between the buffer and live deltas. Same-host
                # Redis: a ≤tens-of-KB SET at stream rate is cheap. Best-effort:
                # the buffer is only a reconnect aid, so a write hiccup must
                # never abort a stream that's otherwise delivering fine.
                try:
                    await set_stream_state(
                        self.ids.project_id,
                        self.ids.assistant_message_id,
                        str(self.pub.content),
                        int(self.pub.seq),
                    )
                except Exception:
                    pass
            elif "usage" in event:
                self.last_attempt.usage = event["usage"]
            elif "error" in event:
                self.last_attempt.error = event["error"]
                return
            elif "pass" in event:
                # B.3 — pass-progress events. Fan out via WS so the
                # frontend can show "Шаг 1/2: Структура" / "Шаг 2/2:
                # Сборка" indicators. Frontend wiring deferred — this
                # publishes the channel today so the UI patch is a
                # one-file change later.
                await publish_event(
                    self.ids.project_id,
                    "llm.pass",
                    {
                        "message_id": str(self.ids.assistant_message_id),
                        "pass": event["pass"],
                        "stage": event["stage"],
                        **{k: v for k, v in event.items() if k not in ("pass", "stage")},
                    },
                )
            elif "brief" in event:
                # V3.10a — surface the art-director brief (palette / fonts /
                # motion / sections) to the client on the same channel as
                # llm.chunk/llm.pass, so the live render can narrate the
                # design reasoning as it builds (Pillar 3 → V3.10). Debug-
                # only before; now flows as an event. Lands before llm.done.
                # v2.21 #1(A): also stash it so the deterministic pre-commit
                # step can BAKE it into the shared static page → a stranger
                # on /p/<slug> sees the same birth reveal (ONE BRIEF, EVERY
                # SURFACE). See services/brief_narration.inject_brief_narration.
                self.brief = event["brief"]
                await publish_event(
                    self.ids.project_id,
                    "omnia:brief",
                    {
                        "message_id": str(self.ids.assistant_message_id),
                        **event["brief"],
                    },
                )
