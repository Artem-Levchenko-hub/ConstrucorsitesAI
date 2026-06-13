"""Surface classification for the composition gates (V1.6 16/5d).

The taste (7/5) and hierarchy (9/5) gates score a *landing / dashboard* richness
rubric: a real type scale, a multi-width rhythm, a hero image, one dominant
focal element. That rubric is correct for the WOW content surface — and the
WRONG rubric for a LOGIN page. An auth-gated entity app serves a centred login
card at ``/``, and the entity composition gate (16/5) scores
``resolve_live_url`` = ``/``. A good login legitimately has no hero image, no
multi-column rhythm and sparse type, so the landing rubric *false-fails* it.

Measured on the live ``crm-ab7e1d`` login (1440px): 8 above-fold text nodes, a
password field, taste 3/5 ``[layout-variety, hero-imagery]`` and hierarchy 1/3
``[type-dominance, focal-dominance]`` — both below their floors though the page
is a perfectly good centred auth card. That is the wrong SURFACE for the rubric,
not a defective page. The genuinely-WOW surface (the dashboard) sits behind auth
and is unreachable without logging in (a future slice). So a detected login
surface is WAIVED by the composition gates rather than scored as a landing.

The detector is deliberately strict so it cannot launder a broken / blank page
into a pass:

  * it requires a real **password input** — the intentional-auth tell. A blank
    or broken page has none, so it is never waived and still fails normally.
  * it requires a **sparse above-the-fold** — a login card is a handful of text
    nodes (heading + a couple of labels + a button). A content landing that
    merely embeds a signup form stays rich above the fold (40+ nodes) and is
    still fully gated.

Pure function (R-01 / R-04): both composition gates call the one detector with
the raw observation their ``_AUDIT_JS`` already emits — no duplicated heuristic,
no browser here.
"""

from __future__ import annotations

from typing import Any

Obs = dict[str, Any]

#: A login card is sparse above the fold. The live crm login measured 8 nodes;
#: a real content landing measures 40+. This threshold sits well between, so a
#: landing that embeds a signup form is not mistaken for a bare login surface.
_LOGIN_MAX_AFOLD_TEXTS = 24

#: Fallback viewport height when the observation omits it (matches the gates).
_DEFAULT_VH = 900.0


def above_fold_text_count(obs: Obs) -> int:
    """Number of painted text nodes whose box intersects the first viewport.

    Mirrors the above-fold test the composition gates use (``-size <= top <
    vh``) so the detector reads the same nodes the rubric scores.
    """
    vh = float(obs.get("viewportHeight") or _DEFAULT_VH)
    count = 0
    for t in obs.get("texts", ()):
        size = float(t.get("size") or 0)
        top = float(t.get("top", 0))
        if size > 0 and -size <= top < vh:
            count += 1
    return count


def is_login_surface(obs: Obs) -> bool:
    """True when the observation is a sparse, password-bearing auth surface.

    Strict by design (see module docstring): a password input AND a sparse
    above-the-fold. Either alone is not enough — a rich page that embeds a
    password field stays gated, and a sparse page with no password field (a
    broken / blank render) still fails the rubric.
    """
    if not obs.get("hasPassword"):
        return False
    return above_fold_text_count(obs) <= _LOGIN_MAX_AFOLD_TEXTS


__all__ = ["above_fold_text_count", "is_login_surface"]
