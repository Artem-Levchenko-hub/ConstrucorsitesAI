"""Smart Defaults engine for PageIR (Phase L8, awwwards-plan item #15).

Pure-Python post-processor that fills "weak" / null fields on a validated
``PageIR`` *before* the renderer touches it. The model is allowed to
leave optional fields blank (schema permits) — this pass closes those
holes deterministically so every page ships with:

* working CTA hrefs (no `#` / no broken anchors),
* a featured pricing tier (so the "popular" badge always exists),
* a copyright line with the current year + brand,
* a favicon emoji that matches the industry,
* coherent dark-mode theme when the hero is on a dark background,
* unique anchor ids across all sections,
* an industry-matched primary/accent palette when the model returned
  the dull schema defaults.

Three invariants for every rule:

1. **Pure** — `apply_smart_defaults` returns a NEW `PageIR`; the
   original is untouched (id() check passes in tests).
2. **Short-circuit** — never overrides a non-default user value; only
   fills nulls, empty strings, schema-defaults, and provably-broken
   placeholders (`"#"`, `"javascript:..."`).
3. **Idempotent** — `f(f(ir)) == f(ir)`.

Call site (`routers/messages.py::_process_prompt`, both the L3 first-
pass branch and the L6 retry branch): immediately after
``PageIR.model_validate(json)`` succeeds and before
``render_to_files(ir)``.
"""

from __future__ import annotations

import datetime as _dt
import logging
from typing import Any

from omnia_api.sections.ir import PageIR

log = logging.getLogger(__name__)


# ─── Industry → palette / favicon mapping ────────────────────────────────
# Pinned values rather than extracted from `design_presets.PRESETS` so the
# defaults are explicit, testable, and decoupled from prompt-layer choices.
# Update both this table and the prompt's <industry_to_vibe> hints when
# you add a new preset_id.

_PRESET_PRIMARY: dict[str, str] = {
    "editorial-trust":       "#0A0A0A",
    "studio-showreel":       "#18181B",
    "saas-product":          "#2563EB",
    "editorial-publication": "#0C0A09",
    "festival-brutalist":    "#000000",
    "wellness-casual":       "#EC4899",
    "boutique-reel":         "#1C1917",
}

_PRESET_ACCENT: dict[str, str] = {
    "editorial-trust":       "#0369A1",
    "studio-showreel":       "#2563EB",
    "saas-product":          "#10B981",
    "editorial-publication": "#A16207",
    "festival-brutalist":    "#FF6B35",
    "wellness-casual":       "#8B5CF6",
    "boutique-reel":         "#A16207",
}

_PRESET_FAVICON: dict[str, str] = {
    "editorial-trust":       "●",
    "studio-showreel":       "◆",
    "saas-product":          "⚡",
    "editorial-publication": "◊",
    "festival-brutalist":    "▲",
    "wellness-casual":       "✦",
    "boutique-reel":         "◐",
}

# Schema defaults — values the model "didn't choose", so we may override.
_SCHEMA_DEFAULT_PRIMARY = "#6366f1"
_SCHEMA_DEFAULT_ACCENT = "#ec4899"
_SCHEMA_DEFAULT_FAVICON = "🚀"

# CTA hrefs we treat as "blank".
_DEAD_HREFS = frozenset({"", "#"})


# ─── Helpers ─────────────────────────────────────────────────────────────


def _is_dead_href(href: str | None) -> bool:
    if not href:
        return True
    h = href.strip().lower()
    return h in _DEAD_HREFS or h.startswith("javascript:")


def _derive_anchor(section: Any) -> str:
    """Anchor id the renderer would assign — used for redirect targets."""
    if getattr(section, "id", None):
        return str(section.id)
    return str(section.type_variant).replace(".", "-")


def _find_conversion_anchor(ir: PageIR) -> str:
    """Pick the best CTA-redirect target in order:
    contact.v1 → cta.v1 → cta.v2 → pricing.v1 → pricing.v2 → '#top'.
    """
    by_type: dict[str, Any] = {}
    for s in ir.sections:
        tv = s.type_variant
        by_type.setdefault(tv, s)
    for preferred in (
        "contact.v1", "cta.v1", "cta.v2", "pricing.v1", "pricing.v2",
    ):
        if preferred in by_type:
            return "#" + _derive_anchor(by_type[preferred])
    return "#top"


def _existing_anchors(ir: PageIR) -> set[str]:
    """Anchor ids that actually exist on the page (after section anchor
    derivation). Used to validate hrefs like '#features' aren't dead."""
    seen: set[str] = set()
    type_counts: dict[str, int] = {}
    for s in ir.sections:
        if getattr(s, "id", None):
            seen.add(str(s.id))
            continue
        base = str(s.type_variant).replace(".", "-")
        type_counts[base] = type_counts.get(base, 0) + 1
        count = type_counts[base]
        seen.add(base if count == 1 else f"{base}-{count}")
    return seen


# ─── Rule implementations ────────────────────────────────────────────────


def _rule_palette(ir_dict: dict, preset_id: str | None, log_buf: list[str]) -> None:
    if not preset_id:
        return
    theme = ir_dict.get("theme") or {}
    if (theme.get("primary") or "").lower() == _SCHEMA_DEFAULT_PRIMARY.lower():
        target = _PRESET_PRIMARY.get(preset_id)
        if target:
            theme["primary"] = target
            log_buf.append(f"palette_primary→{target}")
    if (theme.get("accent") or "").lower() == _SCHEMA_DEFAULT_ACCENT.lower():
        target = _PRESET_ACCENT.get(preset_id)
        if target:
            theme["accent"] = target
            log_buf.append(f"palette_accent→{target}")
    ir_dict["theme"] = theme


def _rule_favicon(ir_dict: dict, preset_id: str | None, log_buf: list[str]) -> None:
    if not preset_id:
        return
    meta = ir_dict.get("meta") or {}
    if (meta.get("favicon_emoji") or "") == _SCHEMA_DEFAULT_FAVICON:
        target = _PRESET_FAVICON.get(preset_id)
        if target:
            meta["favicon_emoji"] = target
            log_buf.append(f"favicon→{target}")
    ir_dict["meta"] = meta


def _rule_cta_anchoring(ir: PageIR, ir_dict: dict, log_buf: list[str]) -> None:
    fallback = _find_conversion_anchor(ir)
    valid_anchors = _existing_anchors(ir)
    sections = ir_dict.get("sections") or []

    def _fix_cta(cta_dict: dict, section_idx: int, cta_label: str) -> None:
        href = cta_dict.get("href") or ""
        if _is_dead_href(href):
            cta_dict["href"] = fallback
            log_buf.append(f"cta_href_dead→{fallback} (section[{section_idx}].{cta_label})")
            return
        # Anchor-style href to non-existent section
        if href.startswith("#") and len(href) > 1:
            anchor = href[1:]
            if anchor not in valid_anchors and anchor != "top":
                cta_dict["href"] = fallback
                log_buf.append(
                    f"cta_href_dangling({href})→{fallback} (section[{section_idx}].{cta_label})"
                )

    for idx, sec in enumerate(sections):
        for cta_key in ("primary_cta", "secondary_cta", "cta"):
            cta = sec.get(cta_key)
            if isinstance(cta, dict):
                _fix_cta(cta, idx, cta_key)
        # Pricing tiers have nested CTAs.
        for tier in sec.get("tiers") or []:
            tcta = tier.get("cta") if isinstance(tier, dict) else None
            if isinstance(tcta, dict):
                _fix_cta(tcta, idx, "tier.cta")


def _rule_pricing_featured(ir_dict: dict, log_buf: list[str]) -> None:
    for idx, sec in enumerate(ir_dict.get("sections") or []):
        if sec.get("type_variant") not in {"pricing.v1", "pricing.v2"}:
            continue
        tiers = sec.get("tiers") or []
        if len(tiers) < 2:
            continue
        if any(t.get("featured") for t in tiers):
            continue
        # Pick middle (or last for 2 tiers).
        target_idx = len(tiers) // 2 if len(tiers) >= 3 else len(tiers) - 1
        tiers[target_idx]["featured"] = True
        log_buf.append(f"pricing_featured→tier[{target_idx}] (section[{idx}])")


def _rule_footer_copyright(ir_dict: dict, log_buf: list[str]) -> None:
    # `datetime.utcnow()` is deprecated in 3.12+; use timezone-aware UTC.
    year = _dt.datetime.now(_dt.UTC).year
    for idx, sec in enumerate(ir_dict.get("sections") or []):
        if sec.get("type_variant") != "footer.v1":
            continue
        copyright_val = (sec.get("copyright") or "").strip()
        brand = sec.get("brand") or "—"
        # Treat empty / "©" / missing year as a hole.
        if not copyright_val or (
            "©" in copyright_val
            and not any(str(y) in copyright_val for y in range(2020, year + 5))
        ):
            sec["copyright"] = f"© {year} {brand}. Все права защищены."
            log_buf.append(f"footer_copyright→filled (section[{idx}])")


def _rule_dark_mode_coercion(ir_dict: dict, log_buf: list[str]) -> None:
    sections = ir_dict.get("sections") or []
    theme = ir_dict.get("theme") or {}
    has_dark_hero = any(
        s.get("type_variant") == "hero.v3"
        and s.get("background") in {"mesh", "aurora", "dark"}
        for s in sections
    )
    if has_dark_hero and not theme.get("dark_mode"):
        theme["dark_mode"] = True
        log_buf.append("dark_mode_coerced→true")
    # Light-mode contradiction: dark_mode=True but background is white.
    if theme.get("dark_mode") and (theme.get("background", "").upper() == "#FFFFFF"):
        theme["background"] = "#0A0A0A"
        theme["text"] = "#F4F4F5"
        log_buf.append("dark_mode_background→#0A0A0A")
    ir_dict["theme"] = theme


def _rule_anchor_uniqueness(ir_dict: dict, log_buf: list[str]) -> None:
    sections = ir_dict.get("sections") or []
    seen: dict[str, int] = {}
    for idx, sec in enumerate(sections):
        sid = sec.get("id")
        if not sid:
            continue
        seen[sid] = seen.get(sid, 0) + 1
        if seen[sid] > 1:
            new_id = f"{sid}-{seen[sid]}"
            sec["id"] = new_id
            log_buf.append(f"anchor_dedup→{new_id} (section[{idx}])")


# ─── Public entrypoint ───────────────────────────────────────────────────


def apply_smart_defaults(
    ir: PageIR,
    *,
    preset_id: str | None = None,
) -> PageIR:
    """Return a new ``PageIR`` with smart defaults applied.

    ``preset_id`` comes from ``services.preset_classifier.classify_preset``
    (cached on the Project row). When ``None`` (e.g. classifier failed),
    palette/favicon rules are skipped; structural rules still run.

    Idempotent: passing the result back through this function returns an
    equal ``PageIR`` (no further changes).
    """
    ir_dict = ir.model_dump(mode="python", exclude_unset=False)
    log_buf: list[str] = []

    # Order: palette + favicon first (preset-driven), then structural
    # rules (CTA / pricing / footer), then theme coercion, then anchor
    # uniqueness (last so other rules can derive anchors from existing
    # ids).
    _rule_palette(ir_dict, preset_id, log_buf)
    _rule_favicon(ir_dict, preset_id, log_buf)
    _rule_cta_anchoring(ir, ir_dict, log_buf)
    _rule_pricing_featured(ir_dict, log_buf)
    _rule_footer_copyright(ir_dict, log_buf)
    _rule_dark_mode_coercion(ir_dict, log_buf)
    _rule_anchor_uniqueness(ir_dict, log_buf)

    if log_buf:
        log.info("smart_defaults applied: %s", "; ".join(log_buf))

    return PageIR.model_validate(ir_dict)


__all__ = ["apply_smart_defaults"]
