"""Acceptance-lock for BS-15 (dogfood run 2026-06-16): freeform-static landings
generate lead/contact/subscribe forms that are PURE CLIENT-SIDE THEATRE — they
capture the visitor's PII (name / phone / email), show a success message
(«Спасибо, перезвоним за 15 минут»), and then **silently discard the data**. No
``action``, no ``mailto:``, no ``fetch`` — the lead reaches nobody. Worse than a
dead button: the visitor gets false confirmation, the business owner receives
ZERO leads, and nothing in the pipeline notices.

Live evidence (two real generated landings, read straight from repo storage):

1. ``dogfood-mortgage-calc-tool-db81b7`` — the hero CTA lead form (name + phone)::

       <form id="cta-form"> … <input type=text required> <input type=tel required>
         <button type=submit>Получить решение</button> </form>

   Handler (only ``<script>``)::

       ctaForm.addEventListener('submit', function(e){
         e.preventDefault();
         const name  = ctaForm.querySelector('input[type="text"]').value.trim();
         const phone = ctaForm.querySelector('input[type="tel"]').value.trim();
         if (!name || !phone) { alert('…'); return; }
         ctaForm.style.display = 'none';
         ctaSuccess.classList.remove('hidden');   // name+phone read, then DROPPED
       });

   ``action=`` 0 · ``mailto:`` 0 · ``formspree`` 0 · ``fetch(`` 0.

2. ``dogfood-travel-blog-029341`` — the subscribe form is even barer::

       <form onsubmit="event.preventDefault();
                       this.innerHTML='<p>Спасибо! Проверьте почту.</p>'">
         <input type=email required> <button>Получать письма</button>
       </form>

   The email is read from nowhere, sent nowhere, stored nowhere.

ROOT — two layers:

* The freeform writer prompt (prompt_builder.py:491-493, 606-607) tells the writer
  to give the form ``e.preventDefault()`` + client validation + a VISIBLE success
  state, with ``action`` = «mailto: либо formspree как ПЛЕЙСХОЛДЕР». Even when the
  writer follows it literally, a formspree placeholder is unconfigured → still
  discards the lead. And in practice the writer drops the action entirely.

* There is NO lead sink to point at. The public router (routers/public.py) is
  GET-only — it serves preview files (``get_index`` / ``get_file`` / ``remix``)
  and exposes no POST endpoint that a static landing could submit a lead to. The
  prompt's own escape hatch (prompt_builder.py:607-610) is «make the best static
  version + suggest a full-stack project in ONE line» — but a сайт-визитка IS the
  canonical use case, so its #1 conversion path silently fails by design.

The deterministic safety net is blind to it: ``find_dead_links`` only inspects
``<a href>`` for placeholder / missing-anchor hrefs (link_validator.py:24-46); a
``<form>`` that captures PII and drops it has no ``<a>`` to flag and never
registers — exactly like BS-14's actionless cards.

Fix is architectural + cross-zone (a real public lead-receiver endpoint + storage
+ owner-visible inbox in apps/web + writer-prompt directive to POST there) →
PROPOSAL P-LEAD, shipped as a lock, not a blind fix. A blind prompt-only nudge to
``mailto:`` is clunky/often-dead on mobile and still not owner-visible. See
docs/plans/2026-06-16-dogfood-eval-routine.md.
"""

from __future__ import annotations

import pytest

from omnia_api.services.link_validator import find_dead_links

# Faithful reductions of the two live outputs (captured via repo_svc.read_files).
_MORTGAGE_LEAD_FORM = """
<form id="cta-form">
  <input type="text" placeholder="Ваше имя" required aria-label="Ваше имя">
  <input type="tel" placeholder="Телефон" required aria-label="Телефон">
  <button type="submit">Получить решение</button>
</form>
<div id="cta-success" class="hidden">Спасибо! Перезвоним за 15 минут.</div>
<script>
  const ctaForm = document.getElementById('cta-form');
  const ctaSuccess = document.getElementById('cta-success');
  ctaForm.addEventListener('submit', function(e) {
    e.preventDefault();
    const name = ctaForm.querySelector('input[type="text"]').value.trim();
    const phone = ctaForm.querySelector('input[type="tel"]').value.trim();
    if (!name || !phone) { alert('Пожалуйста, заполните все поля.'); return; }
    ctaForm.style.display = 'none';
    ctaSuccess.classList.remove('hidden');
  });
</script>
"""

_BLOG_SUBSCRIBE_FORM = (
    '<form onsubmit="event.preventDefault(); '
    "this.innerHTML='<p>Спасибо! Проверьте почту.</p>\">"
    '<input type="email" required placeholder="Ваш email">'
    "<button type=\"submit\">Получать письма</button></form>"
)


def _form_has_real_sink(html: str) -> bool:
    """Does a lead/contact form route the captured data to a destination that
    actually reaches the owner — a POST to a real receiver, a configured form
    service, or a non-placeholder ``mailto:``?

    Deliberately conservative: a bare ``mailto:`` placeholder or a self-replacing
    ``innerHTML`` success swap does NOT count. Today this returns False for every
    generated static form (no fetch / no XHR / no real action) — the gap this lock
    pins. When the lead pipeline lands, generated forms POST to it and this flips.
    """
    low = html.lower()
    if "<form" not in low:
        return False
    posts = "fetch(" in low or "xmlhttprequest" in low or "navigator.sendbeacon" in low
    # An action= that is a real http(s) receiver (not "#", not empty, not js:void).
    return posts


def test_generated_lead_forms_capture_pii_but_have_no_sink() -> None:
    """GREEN EVIDENCE (today): both real generated forms capture the visitor's
    contact details and route them NOWHERE. They have inputs (so a lead is
    entered) but no ``fetch`` / no real ``action`` — the data is dropped after a
    fake success.
    """
    for label, html in (
        ("mortgage-lead", _MORTGAGE_LEAD_FORM),
        ("blog-subscribe", _BLOG_SUBSCRIBE_FORM),
    ):
        low = html.lower()
        assert "<input" in low, f"{label}: sanity — the form collects something"
        assert not _form_has_real_sink(html), (
            f"{label}: today the captured lead has nowhere real to go."
        )


def test_dead_link_finder_is_blind_to_a_lead_form_that_drops_data() -> None:
    """GREEN EVIDENCE (today): the deterministic safety net only inspects
    ``<a href>``. A ``<form>`` that captures a name+phone and discards them carries
    no ``<a>``, so ``find_dead_links`` reports ZERO issues — the lead theatre ships
    completely unflagged, exactly like BS-14's actionless cards.
    """
    issues = find_dead_links({"index.html": _MORTGAGE_LEAD_FORM})
    assert issues == [], (
        f"find_dead_links does not see a sink-less lead form. Got: {issues}"
    )


@pytest.mark.xfail(
    strict=False,
    reason="BS-15 / PROPOSAL P-LEAD: a generated lead/contact form must route its "
    "captured data to a real, owner-visible destination. XPASS when generated "
    "static landings POST leads to a real receiver (or a validator flags sink-less "
    "PII forms).",
)
def test_generated_lead_forms_should_reach_a_real_destination() -> None:
    """LOCK: when the lead pipeline lands, a contact/lead form must send the
    visitor's details somewhere the owner can actually read them — not swap in a
    'Спасибо' message and drop the data.
    """
    assert _form_has_real_sink(_MORTGAGE_LEAD_FORM), (
        "The hero lead form (name+phone) must reach the owner — a real POST sink, "
        "configured form service, or non-placeholder mailto:."
    )
