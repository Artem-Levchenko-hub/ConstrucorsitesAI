"""Deterministic Jinja2 renderer: ``PageIR → HTML string``.

The renderer is a pure function. Same IR → same HTML, byte-for-byte.
This is what lets us **skip** the model's expensive pass_assembly call
in multipass: the model just decides *what* (IR), we do *how* (HTML).

Conventions:
* Each ``type_variant`` maps to ``templates/{type}/{vN}.html.j2``. The
  template receives the validated Pydantic section as ``s`` and the
  page-level theme/meta as ``theme`` / ``meta``.
* The outer ``base.html.j2`` wraps every page: <!doctype>, fonts,
  Tailwind CDN, omnia-kit CSS/JS, theme CSS variables, and the
  rendered section bodies.
* ``autoescape=True`` for ``html`` extension — every user-string is
  HTML-escaped by default. Use ``{{ value | safe }}`` only where the
  template owns the markup (never on LLM-supplied strings).
* ``StrictUndefined`` — missing fields raise instead of silently
  producing blank HTML. Pairs with ``extra="forbid"`` in the IR.

Files emitted:
* ``index.html`` — the actual landing page (root path, matching the static
  scaffold and the ``/p/<slug>`` preview serving).

The omnia-kit (``assets/omnia-kit.{css,js}``) is scaffolded into the project
once and is Omnia-managed — it is left untouched on regeneration, never
re-emitted here.

The caller (``multipass_generator``) wraps these into ``<file>`` blocks
that ``routers/messages.py::_extract_files_and_edits`` already knows how
to commit.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from omnia_api.sections.catalog import TEMPLATE_FOR
from omnia_api.sections.ir import PageIR

log = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent / "templates"


@lru_cache(maxsize=1)
def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "htm", "j2"]),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    env.globals["slugify_id"] = _slugify_id
    return env


def _slugify_id(type_variant: str, *, suffix: str = "") -> str:
    """Stable anchor id derived from the variant (e.g. ``hero-v1`` /
    ``features-v2-2`` for the second occurrence in a page)."""

    base = type_variant.replace(".", "-")
    return f"{base}-{suffix}" if suffix else base


def _section_id(section: Any, seen: dict[str, int]) -> str:
    """Pick a unique anchor id for this section. Explicit ``s.id`` wins;
    otherwise we derive from ``type_variant`` and append an index suffix
    on collision so the page validates as having unique ids."""

    if section.id:
        return section.id
    base = section.type_variant.replace(".", "-")
    seen[base] = seen.get(base, 0) + 1
    count = seen[base]
    return base if count == 1 else f"{base}-{count}"


def _render_section(section: Any, theme: Any, meta: Any, anchor_id: str) -> str:
    """Render one section using its registered template."""

    template_path = TEMPLATE_FOR.get(section.type_variant)
    if template_path is None:
        raise ValueError(
            f"No template registered for variant {section.type_variant!r}. "
            f"Add it to sections.catalog.REGISTRY."
        )
    template = _env().get_template(template_path)
    return template.render(s=section, theme=theme, meta=meta, anchor_id=anchor_id)


def render_page(ir: PageIR) -> str:
    """Render the full ``index.html`` for a validated ``PageIR``.

    Raises ``ValueError`` if any section references a missing template
    (caller in multipass_generator should retry the IR generation in
    that case; missing-template means the catalog was edited but the
    LLM prompt is stale)."""

    env = _env()
    base = env.get_template("base.html.j2")

    seen: dict[str, int] = {}
    rendered_sections: list[str] = []
    for section in ir.sections:
        anchor_id = _section_id(section, seen)
        rendered_sections.append(_render_section(section, ir.theme, ir.meta, anchor_id))

    return base.render(
        meta=ir.meta,
        theme=ir.theme,
        sections_html="\n".join(rendered_sections),
    )


def render_to_files(ir: PageIR, *, kit_css: str = "", kit_js: str = "") -> dict[str, str]:
    """Return the files to commit for this page.

    Only ``index.html`` (root) is emitted — matching the static scaffold and the
    ``/p/<slug>`` serving. The omnia-kit is scaffolded into the project once and
    is Omnia-managed, so ``commit_files`` preserves it untouched (it only mutates
    the files it is given). ``kit_css`` / ``kit_js`` are accepted for back-compat
    and re-committed ONLY when a non-empty value is explicitly passed — emitting
    an empty kit previously deleted it / crashed the commit on a missing path.
    """

    files: dict[str, str] = {"index.html": render_page(ir)}
    if kit_css:
        files["assets/omnia-kit.css"] = kit_css
    if kit_js:
        files["assets/omnia-kit.js"] = kit_js
    return files


__all__ = ["render_page", "render_to_files"]
