"""Acceptance-lock for BS-43 (run #43, 2026-06-17; CONFIRMED on real prod traffic run #44).

**Blind spot:** the freeform writer builds a structurally-correct interactive
tool ("генератор QR-кодов": live input, size selector, canvas, PNG download,
init on DOMContentLoaded) but reaches for a JS library the 98 KB system prompt
never approved (the approved local set is only Tailwind / anime / omnia-kit —
``KIT_FILES`` in prompt_builder.py:2300). With no approved library for the task,
the writer *improvises a CDN path from memory* and emits

    <script src="https://cdn.jsdelivr.net/npm/qrcode@1.5.3/build/qrcode.min.js"></script>
    ...
    QRCode.toCanvas(canvas, text, {...})

That URL is **HTTP 404** — the `qrcode` npm package ships no `build/` dir at all
(only `/lib/`, `/helper/`), and node-qrcode's browser bundle is gitignored from
both npm and its GitHub repo. So `typeof QRCode === "undefined"`, the call-site
throws `ReferenceError`, the canvas stays empty, and the tool is **DOA in every
browser** while the page itself looks polished. No gate catches it: vision audit
did not run (vision_ran=False) and the structural gate cannot see that a canvas
is blank. Family: BS-12 / BS-28 / BS-31 "false success" — build reports OK, the
artifact is silently dead.

**Why a simple URL-swap does NOT fix it (re-verified run #44):** the working
public-CDN QR libraries (`qrcodejs@1.0.0`, `qrcode-generator@1.4.4`, `qrious`)
all return 200 but expose a DIFFERENT API (`new QRCode(el, text)` /
`new QRious({...})`), not the `QRCode.toCanvas`/`QRCode.toDataURL` the writer
used. node-qrcode — the only library with that exact API — has NO browser-global
UMD on jsdelivr, unpkg, or its GitHub build path (all 404). So repairing the dead
src alone leaves the call-site broken; both src and call-site would have to be
rewritten in lockstep (fragile, BS-38 class).

**Real-prod confirmation (run #44, 2026-06-17):** an actual (non-sandbox) user
project generated the same QR-generator tool and emitted the IDENTICAL dead URL
``qrcode@1.5.3/build/qrcode.min.js`` (verified 404) with a ``QRCode.toCanvas``
call-site (prod debug-dump 08_post_palette_guard.html). The blind spot is not a
dogfood artifact — it is shipping DOA tools to real users.

**PROPOSAL P-CDNDEAD** (NOT shipped — multi-surface, risky):
  (A) RECOMMENDED — host a working node-qrcode browser UMD as a kit asset
      (``assets/qrcode.min.js``, alongside omnia-kit.js), copied into freeform
      output, + a system-prompt directive: "for QR use the already-bundled
      ``assets/qrcode.min.js``, ``QRCode.toCanvas`` is global". Deterministic,
      no network on the generation hot path, library under our control.
  (B) opt. deterministic post-process: HEAD-check external <script src> with a
      cache + fail-OPEN on network error; on a hard 404 of a known CDN host,
      flag the snapshot (detect, do not block).
  (C) widen the approved-library list (QR, Chart.js pinned, signature-pad) +
      a rule "outside the list, do NOT invent a CDN path".

These tests LOCK the evidence green (the dead-URL signature + that the working
public alternatives have an incompatible API, so a naive src-swap is wrong) and
keep the fix contract — plan (A): a working QR library is bundled as a kit asset
— RED as a strict-xfail. It XPASSes the moment ``assets/qrcode.min.js`` (or an
equivalent task-library mechanism) joins the approved local set.
"""
from __future__ import annotations

import pytest

from omnia_api.services.prompt_builder import KIT_FILES


# The exact <script src> the writer emitted on the "генератор QR-кодов" prompt,
# both in the run #43 dogfood gen and the run #44 real-prod gen.
_DEAD_QR_CDN = "https://cdn.jsdelivr.net/npm/qrcode@1.5.3/build/qrcode.min.js"

# The call-site API the writer used (only node-qrcode exposes this).
_NODE_QRCODE_API = ("QRCode.toCanvas", "QRCode.toDataURL")

# Working public-CDN QR libraries — they resolve (200) but expose a DIFFERENT
# API, so swapping ONLY the src leaves the call-site broken.
_WORKING_BUT_INCOMPATIBLE = {
    "https://cdn.jsdelivr.net/gh/davidshimjs/qrcodejs/qrcode.min.js": "new QRCode(el, text)",
    "https://cdn.jsdelivr.net/npm/qrcode-generator@1.4.4/qrcode.js": "qrcode(typeNumber, ecl)",
    "https://cdn.jsdelivr.net/npm/qrious@4.0.2/dist/qrious.min.js": "new QRious({...})",
}


def test_dead_qr_cdn_signature_is_a_build_path():
    """The hallucinated URL points at a `build/` path the npm package never ships.

    node-qrcode publishes only `/lib/` and `/helper/`; there is no `build/`
    directory on npm, so any `qrcode@*/build/*.js` is a 404 by construction.
    Locking the signature keeps the diagnosis honest without a flaky network call.
    """
    assert "/qrcode@" in _DEAD_QR_CDN
    assert "/build/" in _DEAD_QR_CDN
    assert _DEAD_QR_CDN.endswith("qrcode.min.js")


def test_call_site_requires_node_qrcode_api_not_the_working_alternatives():
    """A naive src-swap is WRONG: the resolvable libraries have a different API.

    The writer used `QRCode.toCanvas` (node-qrcode). Every public-CDN QR library
    that returns 200 exposes a constructor-style API instead, so repairing only
    the `<script src>` would leave the `QRCode.toCanvas(...)` call-site throwing.
    """
    assert _NODE_QRCODE_API[0] == "QRCode.toCanvas"
    for usage in _WORKING_BUT_INCOMPATIBLE.values():
        assert "toCanvas" not in usage and "toDataURL" not in usage


def test_no_qr_library_in_approved_local_kit_today():
    """Current-state lock: the approved local asset set has NO QR library.

    With no approved library for the task, the writer improvises a CDN path —
    the root of BS-43. This stays green until plan (A) bundles one.
    """
    assert not any("qr" in f.lower() for f in KIT_FILES)


@pytest.mark.xfail(
    strict=True,
    reason="P-CDNDEAD plan (A) not shipped: no working QR library bundled as a kit asset",
)
def test_working_qr_library_is_bundled_as_kit_asset():
    """Fix contract: a working QR library ships as an approved local kit asset.

    XPASSes when `assets/qrcode.min.js` (a node-qrcode browser UMD exposing the
    `QRCode.toCanvas` global the writer already calls) joins KIT_FILES, so the
    writer is pointed at a deterministically-resolvable library instead of an
    invented CDN path.
    """
    assert any("qrcode" in f.lower() for f in KIT_FILES)
