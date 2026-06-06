"""Managed style-override layer for direct in-preview edits.

A single ``<style id="omnia-overrides">`` block (placed last in ``<head>`` so its
``!important`` rules win) plus ``<link data-omnia-font>`` tags carry the user's
color/font edits. The SAME artifact is rendered live by the inspector and written
here for persistence, so a saved edit looks identical after reload (parity).

Rules are keyed by the inspector's CSS selector — generated from the committed
DOM, so it matches the current snapshot exactly. Pure string transforms (no I/O):
reused by both the style-patch endpoint and the regen carry-over.

Survives the generation guards by construction: ``palette_guard`` only rewrites
the FIRST ``:root{}`` and the ``body{}`` rule; our block is appended last with its
own ``:root`` (a later, separate block), so it's untouched. Banned hexes are
rejected at the endpoint boundary, so nothing here needs guard exemption.
"""

from __future__ import annotations

import re

OVERRIDES_STYLE_ID = "omnia-overrides"
FONT_LINK_ATTR = "data-omnia-font"

_STYLE_RE = re.compile(
    r'<style[^>]*\bid=["\']' + OVERRIDES_STYLE_ID + r'["\'][^>]*>.*?</style>\s*',
    re.IGNORECASE | re.DOTALL,
)
_FONT_LINK_RE = re.compile(
    r'<link[^>]*\b' + FONT_LINK_ATTR + r'=["\'][^"\']*["\'][^>]*>\s*',
    re.IGNORECASE,
)
_HEAD_CLOSE_RE = re.compile(r"</head\s*>", re.IGNORECASE)
_BODY_CLOSE_RE = re.compile(r"</body\s*>", re.IGNORECASE)
_BLOCK_INNER_RE = re.compile(
    r'<style[^>]*\bid=["\']' + OVERRIDES_STYLE_ID + r'["\'][^>]*>(.*?)</style>',
    re.IGNORECASE | re.DOTALL,
)
_FONT_LINK_PARSE_RE = re.compile(
    r"<link[^>]*\b" + FONT_LINK_ATTR + r'=["\']([^"\']*)["\'][^>]*'
    r'\bhref=["\']([^"\']*)["\'][^>]*>',
    re.IGNORECASE,
)
_RULE_RE = re.compile(r"([^{}]+)\{([^{}]*)\}", re.DOTALL)
_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _safe_val(v: str) -> str:
    """Strip anything that could break out of a declaration/block. Hex colors and
    quoted font stacks (commas + quotes) pass through unchanged."""
    return re.sub(r"[<>{};\n\r]", "", v or "").strip()


def _safe_selector(s: str) -> str:
    """Keep CSS selector chars (incl. the `>` combinator); drop block/markup
    breakers. Endpoint also clamps length."""
    return re.sub(r"[<{}\n\r]", "", s or "").strip()


def _attr_safe(s: str) -> str:
    return re.sub(r'["<>]', "", s or "").strip()


def render_overrides_block(
    tokens: list[tuple[str, str]],
    element_rules: list[tuple[str, dict[str, str]]],
) -> str:
    """Build the ``<style id="omnia-overrides">`` block (or "" if no rules).

    tokens: (css-var, value) → site-wide. element_rules: (selector, {prop: value}).
    """
    lines: list[str] = []
    token_decls = " ".join(
        f"{_safe_val(var)}: {_safe_val(val)} !important;"
        for var, val in tokens
        if var and val
    )
    if token_decls:
        lines.append(":root{ " + token_decls + " }")
    for selector, decls in element_rules:
        sel = _safe_selector(selector)
        body = " ".join(
            f"{prop}: {_safe_val(val)} !important;"
            for prop, val in decls.items()
            if val
        )
        if sel and body:
            lines.append(sel + "{ " + body + " }")
    if not lines:
        return ""
    css = "\n".join(lines)
    return (
        f'<style id="{OVERRIDES_STYLE_ID}">\n'
        "/* omnia-overrides — managed; direct in-preview edits */\n"
        f"{css}\n</style>"
    )


def _font_links_html(font_links: list[tuple[str, str]]) -> str:
    seen: set[str] = set()
    out: list[str] = []
    for family, href in font_links:
        if not family or not href or family in seen:
            continue
        seen.add(family)
        out.append(
            f'<link {FONT_LINK_ATTR}="{_attr_safe(family)}" '
            f'rel="stylesheet" href="{_attr_safe(href)}">'
        )
    return "\n".join(out)


def _strip_managed(html: str) -> str:
    return _FONT_LINK_RE.sub("", _STYLE_RE.sub("", html))


def _inject_before_head_close(html: str, inject: str) -> str:
    if not inject.strip():
        return html
    snippet = inject.rstrip() + "\n"
    for rx in (_HEAD_CLOSE_RE, _BODY_CLOSE_RE):
        m = rx.search(html)
        if m:
            return html[: m.start()] + snippet + html[m.start() :]
    return html + "\n" + snippet


def _extract_block_css(html: str) -> str:
    m = _BLOCK_INNER_RE.search(html or "")
    return m.group(1) if m else ""


def _extract_font_links(html: str) -> list[tuple[str, str]]:
    return [(m.group(1), m.group(2)) for m in _FONT_LINK_PARSE_RE.finditer(html or "")]


def _parse_overrides(css: str) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    """Parse a previously-rendered override block back into (tokens, elements) so a
    new patch can MERGE rather than clobber earlier edits."""
    tokens: dict[str, str] = {}
    elements: dict[str, dict[str, str]] = {}
    body_css = _COMMENT_RE.sub("", css or "")
    for m in _RULE_RE.finditer(body_css):
        sel = m.group(1).strip()
        decls: dict[str, str] = {}
        for part in m.group(2).split(";"):
            part = re.sub(r"\s*!important\s*$", "", part.strip(), flags=re.IGNORECASE)
            if ":" not in part:
                continue
            k, v = part.split(":", 1)
            decls[k.strip()] = v.strip()
        if not decls:
            continue
        if sel == ":root":
            tokens.update(decls)
        else:
            elements[sel] = decls
    return tokens, elements


def apply_overrides(
    index_html: str,
    *,
    tokens: list[tuple[str, str]],
    element_rules: list[tuple[str, dict[str, str]]],
    font_links: list[tuple[str, str]],
) -> str:
    """Merge the new edits into any already-committed override block, then rewrite
    it (idempotent). Merging makes patches cumulative: a later edit never drops an
    earlier one, so saved color/font tweaks survive across reloads + sessions."""
    old_tokens, old_elements = _parse_overrides(_extract_block_css(index_html))
    merged_tokens = dict(old_tokens)
    for var, val in tokens:
        merged_tokens[var] = val
    merged_elements = {sel: dict(d) for sel, d in old_elements.items()}
    for sel, decls in element_rules:
        merged_elements.setdefault(sel, {}).update(decls)
    merged_fonts: list[tuple[str, str]] = list(_extract_font_links(index_html))
    merged_fonts.extend(font_links)

    block = render_overrides_block(
        list(merged_tokens.items()), list(merged_elements.items())
    )
    links = _font_links_html(merged_fonts)
    cleaned = _strip_managed(index_html)
    inject = "\n".join(p for p in (links, block) if p)
    return _inject_before_head_close(cleaned, inject)


def carry_over_overrides(old_html: str, new_html: str) -> str:
    """Lift the override block + font links from a previous snapshot into freshly
    generated HTML, so manual edits survive an AI regeneration. Fail-soft: returns
    ``new_html`` unchanged when the old page carried no overrides."""
    block_m = _STYLE_RE.search(old_html or "")
    links = _FONT_LINK_RE.findall(old_html or "")
    if not block_m and not links:
        return new_html
    cleaned = _strip_managed(new_html)
    inject = "".join(links) + (block_m.group(0).strip() if block_m else "")
    return _inject_before_head_close(cleaned, inject)
