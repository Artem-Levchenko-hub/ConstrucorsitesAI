from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from omnia_api.core.config import get_settings
from omnia_api.core.redis import publish_event
from omnia_api.models.project import Project
from omnia_api.services import pipeline_debug
from omnia_api.services.generation.contracts import (
    GenerationIds,
    ProjectGenerationFacts,
)
from omnia_api.services.generation.stream_attempt import ModelStream
from omnia_api.services.image_resolver import resolve_images

_log = logging.getLogger("omnia_api.routers.messages")


async def resolve_assets_and_app_composition(
    *,
    factory: async_sessionmaker[AsyncSession],
    files: dict[str, str],
    ids: GenerationIds,
    project_info: ProjectGenerationFacts,
    prompt_text: str,
    stream: ModelStream,
    surgical: bool,
) -> tuple[dict[str, str]]:
    if files and project_info.image_gen_enabled:
        await stream.emit_stage("images", "start")
        try:
            # Hard cap: a broken image upstream (flux 501 / pexels timeout)
            # must NEVER hang the build — on timeout we ship the page as-is so
            # it still reaches commit (no lost work). 75s over the resolver's
            # own per-image deadline.
            # Live drop-in: emit a per-image event as each picture resolves so
            # the streaming preview swaps it into its frame in real time. Gated
            # by use_live_image_events; off → images still land on the snapshot.
            async def _emit_img(idx: int, url: str) -> None:
                await publish_event(
                    ids.project_id,
                    "image.resolved",
                    {
                        "message_id": str(ids.assistant_message_id),
                        "idx": idx,
                        "url": url,
                    },
                )

            _on_img = _emit_img if get_settings().use_live_image_events else None
            files, resolved, total = await asyncio.wait_for(
                resolve_images(files, str(ids.project_id), on_image=_on_img),
                timeout=75,
            )
            print(
                f"[PP] image_resolver resolved={resolved} total={total}",
                flush=True,
            )
            if total > 0 and resolved < total:
                await publish_event(
                    ids.project_id,
                    "llm.chunk",
                    {
                        "message_id": str(ids.assistant_message_id),
                        "delta": (
                            f"\n\n*Сгенерировано картинок: {resolved} из {total}. "
                            f"Часть промптов не удалась — попробуй переключить toggle "
                            f"«🎨 Картинки» в шапке или перегенерировать промпт.*\n\n"
                        ),
                    },
                )
        except Exception as img_exc:
            print(f"[PP] image_resolver failed: {img_exc!r}", flush=True)
        await stream.emit_stage("images", "end")

    # Belt-and-suspenders: if image-gen failed (budget exhausted / timeout),
    # the resolver leaves the data-omnia-gen tags in place → a BROKEN <img>
    # box ships (the dark "alt text" rectangle the owner saw). Strip any
    # leftover unresolved tags so the section's drawn/graphic background shows
    # instead. No-op when everything resolved. Runs for builds AND edits.
    if files:
        try:
            from omnia_api.services.image_resolver import strip_unresolved_tags

            files, _stripped = strip_unresolved_tags(files)
            if _stripped:
                print(f"[PP] stripped_unresolved_img_tags={_stripped}", flush=True)
        except Exception as _strip_exc:
            print(f"[PP] strip_unresolved skipped: {_strip_exc!r}", flush=True)

    # ── Entity theme-token guard — deterministic, BEFORE the audit ────────
    # Cheap writer models leak raw neutral utilities (text-gray-800,
    # bg-gray-100, bg-white) instead of theme tokens, so the app freezes grey
    # and ignores the art-director's --primary/--foreground. Rewrite them to
    # tokens here (the .tsx analogue of palette_guard) so the shipped app
    # actually re-themes and the audit's hardcoded-colour class clears.
    # Semantic status colours (green/yellow/red) are left untouched.
    if files and not surgical and project_info.template in ("nextjs_entities", "fullstack"):
        try:
            from omnia_api.services.entity_theme import tokenize_neutrals

            files, _tok_n = tokenize_neutrals(files)
            if _tok_n:
                print(f"[PP] entity_theme tokenized={_tok_n} neutral utils", flush=True)
        except Exception as _tok_exc:
            print(f"[PP] entity_theme skipped: {_tok_exc!r}", flush=True)

    # ── Missing-component shim guard — deterministic, BEFORE commit ────────
    # Writer models import standard shadcn components the template doesn't
    # ship (radio-group, switch, …) → Next.js "Module not found" → the WHOLE
    # app renders a build-error page (owner hit this). Inject a dependency-
    # free self-contained shim for any imported-but-missing @/components/ui/*
    # so the app always builds.
    if files and not surgical and project_info.template in ("nextjs_entities", "fullstack"):
        try:
            from omnia_api.services.ui_shims import ensure_ui_shims

            files, _shim_added, _shim_missing = ensure_ui_shims(files)
            if _shim_added:
                print(f"[PP] ui_shims injected={_shim_added}", flush=True)
            if _shim_missing:
                print(
                    f"[PP] ui_shims MISSING no-shim (build may fail)={_shim_missing}",
                    flush=True,
                )
        except Exception as _shim_exc:
            print(f"[PP] ui_shims skipped: {_shim_exc!r}", flush=True)

    # ── Branded share-card (P2, pillar 4) — deterministic, BEFORE commit ──
    # The entity template's <head> is a static «Omnia project», so every
    # shared /p/<slug> link unfurls brand-less. Derive a {title, tagline,
    # accent} card from the project name + prompt + palette and inject it as
    # src/app/omnia-share.ts, which the template's generateMetadata +
    # opengraph-image route consume → a branded unfurl per niche, 0 model
    # cost. Fail-soft: any error leaves the template's neutral default card.
    if files and not surgical and project_info.template in ("nextjs_entities", "fullstack"):
        try:
            from omnia_api.services.design_tokens import tokens_for_project
            from omnia_api.services.share_meta import (
                build_share_card,
                inject_share_module,
            )

            _accent = tokens_for_project(
                str(ids.project_id), industry_hint=project_info.design_preset_id
            ).palette.primary
            async with factory() as _share_session:
                _proj = await _share_session.get(Project, ids.project_id)
                _proj_name = _proj.name if _proj else None
            _card = build_share_card(_proj_name, prompt_text, _accent)
            files = inject_share_module(files, _card)
            print(f"[PP] share_meta card title={_card.title!r}", flush=True)
        except Exception as _share_exc:
            print(f"[PP] share_meta skipped: {_share_exc!r}", flush=True)

    # ── Baked brief → public surface (v2.21 #1A, pillar 3+4) — BEFORE commit ──
    # The freeform static /p/<slug> narrates its own birth (baked into
    # index.html further below). The ENTITY hot-path (≈80% of apps) did NOT:
    # its /p/<slug> 302-redirects to the LIVE app on another origin, whose
    # public/omnia-brief-narration.js only ever received the brief via the
    # workspace iframe's postMessage — so a stranger opening the shared (or
    # forked) app saw a finished UI, SILENT. Bake the art-director brief onto
    # window.__omniaBrief (src/app/omnia-brief.ts → layout.tsx, the .tsx
    # analogue of share_meta's omnia-share.ts) so the SAME reveal plays for a
    # stranger. Fail-soft: no/empty brief leaves the template's `null` default
    # and the reveal stays inert. Side-effect-free; idempotent on the file.
    _bm_brief = stream.brief
    if (
        files
        and not surgical
        and project_info.template in ("nextjs_entities", "fullstack")
        and isinstance(_bm_brief, dict)
    ):
        try:
            from omnia_api.services.brief_narration import inject_brief_module

            files = inject_brief_module(files, _bm_brief)
            print("[PP] brief_module baked into omnia-brief.ts", flush=True)
        except Exception as _bm_exc:
            print(f"[PP] brief_module skipped err={_bm_exc!r}", flush=True)

    # ── Structure audit (entity/app builds) — non-blocking smoke detector ─
    # Entity/fullstack apps skip the acceptance gate (container-backed), so we
    # at least LOG drift from the app-UI doctrine (hardcoded colours, fixed-px
    # widths, raw <table>/<aside>, missing app-shell). Never blocks the build.
    if files and not surgical and project_info.template in ("nextjs_entities", "fullstack"):
        try:
            from omnia_api.services.structure_audit import audit_entity_app

            _struct_warnings = audit_entity_app(files)
            if _struct_warnings:
                print(
                    f"[PP] structure_audit ({len(_struct_warnings)}): "
                    + " | ".join(_struct_warnings[:12]),
                    flush=True,
                )
                pipeline_debug.dump(
                    ids.project_id,
                    ids.assistant_message_id,
                    "04_structure_audit.md",
                    "\n".join(f"- {w}" for w in _struct_warnings),
                )
        except Exception as _audit_exc:
            print(f"[PP] structure_audit skipped: {_audit_exc!r}", flush=True)

    return (files,)
