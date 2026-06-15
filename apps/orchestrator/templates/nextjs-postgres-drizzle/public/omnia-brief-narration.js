/**
 * Omnia brief-narration — "the app narrates its own birth" (ONE BRIEF, EVERY
 * SURFACE). Canonical source — a drift test keeps the copy in
 * nextjs-postgres-drizzle byte-identical (R-04 DRY of knowledge).
 *
 * WHY this script exists: the art-director brief (palette / fonts / sections /
 * motion) is the single source of design truth, but until now it reached ONLY
 * the workspace chat — entity dashboards and the public «/» were born SILENT.
 * The workspace forwards the brief into the live container iframe over
 * `postMessage({type:'omnia:brief', brief})` (PreviewFrame); a future generator
 * step can also bake it onto `window.__omniaBrief` at build time so a SHARED
 * link plays the same birth for a stranger. Either way this script turns the
 * brief into a short, hypnotic "AI is designing" reveal: brand-tinted swatches
 * pop in, then human-readable lines (each literally carrying a brief value —
 * HEX / font name / section names / motion) cadence one by one, then the
 * overlay fades and the finished app lands. Mirrors apps/web brief-narration.ts
 * (same line logic, same role order) so the workspace narration and the
 * on-surface narration tell the IDENTICAL story.
 *
 * Fail-soft: no brief (or a brief that yields zero lines) → renders nothing,
 * the app appears normally. Self-contained (no CDN), idempotent (replays only
 * on a genuinely new brief), reduced-motion-safe.
 */
(function () {
  "use strict";

  var OVERLAY_ID = "omnia-brief-narration";
  var HEX_RE = /^#[0-9a-fA-F]{3,8}$/;

  // Role priority: accent → primary → background → rest (mirror of
  // brief-narration.ts paletteRole, R-04 — one notion of "lead colour").
  function paletteRole(key) {
    var u = String(key || "").toUpperCase();
    if (u.indexOf("АКЦЕНТ") !== -1 || u.indexOf("ACCENT") !== -1) return 0;
    if (u.indexOf("PRIMARY") !== -1) return 1;
    if (u.indexOf("ФОН") !== -1 || u.indexOf("BACKGROUND") !== -1) return 2;
    return 3;
  }

  // Distinct valid HEX from the palette, ordered by role. `limit` caps how many
  // we keep (2 for the prose line, more for the swatch row).
  function pickHexes(palette, limit) {
    var entries = Object.keys(palette || {})
      .map(function (k) {
        return [k, palette[k]];
      })
      .filter(function (e) {
        return typeof e[1] === "string" && HEX_RE.test(String(e[1]).trim());
      })
      .sort(function (a, b) {
        return paletteRole(a[0]) - paletteRole(b[0]);
      });
    var out = [];
    for (var i = 0; i < entries.length; i++) {
      var hex = String(entries[i][1]).trim();
      if (out.indexOf(hex) === -1) out.push(hex);
      if (out.length >= limit) break;
    }
    return out;
  }

  // Motion specs can be long — keep the first meaningful fragment on a word
  // boundary (mirror of brief-narration.ts shortMotion).
  function shortMotion(motion) {
    var m = String(motion || "").trim();
    if (m.length <= 48) return m;
    var cut = m.slice(0, 48);
    var sp = cut.lastIndexOf(" ");
    return (sp > 24 ? cut.slice(0, sp) : cut) + "…";
  }

  // Ordered narration lines from the brief — the art-director's train of
  // thought: palette → font → section frame → motion. A line is included only
  // when its field is non-empty; the final list is de-duplicated. Byte-for-byte
  // the same copy as apps/web brief-narration.ts.
  function briefLines(brief) {
    if (!brief) return [];
    var lines = [];

    var hexes = pickHexes(brief.palette || {}, 2);
    if (hexes.length) lines.push("Подбираю палитру — " + hexes.join(" и "));

    var display = ((brief.fonts && brief.fonts.display) || "").trim();
    var text = ((brief.fonts && brief.fonts.text) || "").trim();
    if (display) lines.push("Беру шрифт «" + display + "» для заголовков");
    else if (text) lines.push("Беру шрифт «" + text + "» для текста");

    var names = (brief.sections || [])
      .map(function (s) {
        return ((s && s.name) || "").trim();
      })
      .filter(Boolean);
    if (names.length) {
      var shown = names.slice(0, 4);
      var suffix = names.length > shown.length ? " …" : "";
      lines.push("Компоную секции: " + shown.join(" → ") + suffix);
    }

    var motion = (brief.motion || "").trim();
    if (motion) lines.push("Оживляю движением — " + shortMotion(motion));

    var seen = {};
    return lines.filter(function (l) {
      if (seen[l]) return false;
      seen[l] = true;
      return true;
    });
  }

  // Compact, stable signature so the same brief never replays, but a genuinely
  // new generation does.
  function sig(brief) {
    try {
      return JSON.stringify(brief);
    } catch (_) {
      return "";
    }
  }

  function reducedMotion() {
    try {
      return (
        window.matchMedia &&
        window.matchMedia("(prefers-reduced-motion:reduce)").matches
      );
    } catch (_) {
      return false;
    }
  }

  function injectStyle() {
    if (document.getElementById("omnia-bn-style")) return;
    var style = document.createElement("style");
    style.id = "omnia-bn-style";
    // Hypnotic birth reveal (pillar 3): a living brand-tinted aura drifts behind
    // a glass card; a radar-ping orb "thinks", the palette weaves in on a drawn
    // thread with a sheen sweep, a progress bar fills over the reveal, and each
    // cadenced line wipes an accent underline. Every motion is keyed off the
    // brief's --omnia-bn-accent and force-settles under reduced-motion.
    style.textContent =
      "#" + OVERLAY_ID + "{position:fixed;inset:0;z-index:2147482000;overflow:hidden;" +
      "display:flex;align-items:center;justify-content:center;" +
      "background:radial-gradient(120% 120% at 50% 0%,rgba(15,18,28,.82),rgba(8,10,16,.94));" +
      "backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);" +
      "font-family:var(--font-sans,-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif);" +
      "opacity:0;transition:opacity .5s ease}" +
      "#" + OVERLAY_ID + ".omnia-bn-in{opacity:1}" +
      "#" + OVERLAY_ID + ".omnia-bn-out{opacity:0}" +
      "#" + OVERLAY_ID + " .omnia-bn-aura{position:absolute;inset:-25%;pointer-events:none;" +
      "background:radial-gradient(38% 38% at 30% 32%,var(--omnia-bn-accent,#818cf8) 0,transparent 60%)," +
      "radial-gradient(34% 34% at 72% 66%,var(--omnia-bn-accent,#818cf8) 0,transparent 62%);" +
      "opacity:.20;filter:blur(48px);animation:omnia-bn-drift 9s ease-in-out infinite alternate}" +
      "#" + OVERLAY_ID + " .omnia-bn-card{position:relative;width:min(440px,86vw);" +
      "padding:28px 30px 30px;border-radius:22px;color:#f3f5fb;" +
      "background:linear-gradient(180deg,rgba(255,255,255,.08),rgba(255,255,255,.03));" +
      "border:1px solid rgba(255,255,255,.12);" +
      "box-shadow:0 30px 80px rgba(0,0,0,.45),inset 0 1px 0 rgba(255,255,255,.08)}" +
      "#" + OVERLAY_ID + " .omnia-bn-card::before{content:'';position:absolute;left:24px;right:24px;top:0;" +
      "height:1px;background:linear-gradient(90deg,transparent,var(--omnia-bn-accent,#818cf8),transparent);opacity:.6}" +
      "#" + OVERLAY_ID + " .omnia-bn-head{display:flex;align-items:center;gap:11px;" +
      "font-size:13px;font-weight:600;letter-spacing:.01em;color:#aab2c8;margin-bottom:16px}" +
      "#" + OVERLAY_ID + " .omnia-bn-dot{position:relative;width:9px;height:9px;border-radius:9999px;" +
      "background:var(--omnia-bn-accent,#818cf8);box-shadow:0 0 14px var(--omnia-bn-accent,#818cf8);" +
      "animation:omnia-bn-pulse 1.4s ease-in-out infinite}" +
      "#" + OVERLAY_ID + " .omnia-bn-dot::before,#" + OVERLAY_ID + " .omnia-bn-dot::after{content:'';" +
      "position:absolute;inset:0;border-radius:9999px;border:1px solid var(--omnia-bn-accent,#818cf8);" +
      "animation:omnia-bn-ping 2s cubic-bezier(.2,.6,.3,1) infinite}" +
      "#" + OVERLAY_ID + " .omnia-bn-dot::after{animation-delay:1s}" +
      "#" + OVERLAY_ID + " .omnia-bn-prog{position:relative;height:2px;border-radius:2px;margin-bottom:18px;" +
      "background:rgba(255,255,255,.08);overflow:hidden}" +
      "#" + OVERLAY_ID + " .omnia-bn-prog>i{display:block;height:100%;width:100%;transform-origin:left;transform:scaleX(0);" +
      "background:linear-gradient(90deg,transparent,var(--omnia-bn-accent,#818cf8));" +
      "animation:omnia-bn-fill linear forwards}" +
      "#" + OVERLAY_ID + " .omnia-bn-swatches{position:relative;display:flex;gap:8px;margin-bottom:18px}" +
      "#" + OVERLAY_ID + " .omnia-bn-swatches::before{content:'';position:absolute;left:4px;right:4px;top:50%;height:1px;" +
      "transform:translateY(-50%) scaleX(0);transform-origin:left;opacity:.5;" +
      "background:linear-gradient(90deg,var(--omnia-bn-accent,#818cf8),transparent);" +
      "animation:omnia-bn-thread .7s ease-out .1s forwards}" +
      "#" + OVERLAY_ID + " .omnia-bn-sw{position:relative;width:34px;height:34px;border-radius:10px;overflow:hidden;" +
      "border:1px solid rgba(255,255,255,.18);box-shadow:0 4px 14px rgba(0,0,0,.3);" +
      "opacity:0;transform:scale(.5) translateY(6px);" +
      "animation:omnia-bn-pop .5s cubic-bezier(.16,1,.3,1) var(--omnia-d,0ms) forwards}" +
      "#" + OVERLAY_ID + " .omnia-bn-sw::after{content:'';position:absolute;inset:0;" +
      "background:linear-gradient(115deg,transparent 32%,rgba(255,255,255,.5) 50%,transparent 68%);" +
      "transform:translateX(-130%);animation:omnia-bn-sheen 1.5s ease-in-out calc(var(--omnia-d,0ms) + 280ms) 1}" +
      "#" + OVERLAY_ID + " .omnia-bn-line{position:relative;display:flex;align-items:flex-start;gap:10px;" +
      "font-size:15px;line-height:1.5;color:#e7eaf3;margin-top:11px;padding-bottom:3px;" +
      "opacity:0;transform:translateY(8px);" +
      "animation:omnia-bn-rise .55s cubic-bezier(.16,1,.3,1) var(--omnia-d,0ms) forwards}" +
      "#" + OVERLAY_ID + " .omnia-bn-line::before{content:'';flex:0 0 auto;margin-top:8px;" +
      "width:6px;height:6px;border-radius:9999px;background:var(--omnia-bn-accent,#818cf8);" +
      "box-shadow:0 0 8px var(--omnia-bn-accent,#818cf8)}" +
      "#" + OVERLAY_ID + " .omnia-bn-line::after{content:'';position:absolute;left:16px;bottom:0;height:1px;" +
      "width:calc(100% - 16px);transform:scaleX(0);transform-origin:left;opacity:.42;" +
      "background:linear-gradient(90deg,var(--omnia-bn-accent,#818cf8),transparent);" +
      "animation:omnia-bn-underline .5s ease-out calc(var(--omnia-d,0ms) + 200ms) forwards}" +
      "@keyframes omnia-bn-pulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.45;transform:scale(.82)}}" +
      "@keyframes omnia-bn-pop{to{opacity:1;transform:none}}" +
      "@keyframes omnia-bn-rise{to{opacity:1;transform:none}}" +
      "@keyframes omnia-bn-drift{0%{transform:translate(-3%,-2%) scale(1)}100%{transform:translate(4%,3%) scale(1.12)}}" +
      "@keyframes omnia-bn-ping{0%{opacity:.6;transform:scale(1)}100%{opacity:0;transform:scale(3.4)}}" +
      "@keyframes omnia-bn-fill{to{transform:scaleX(1)}}" +
      "@keyframes omnia-bn-thread{to{transform:translateY(-50%) scaleX(1)}}" +
      "@keyframes omnia-bn-sheen{0%{transform:translateX(-130%)}60%,100%{transform:translateX(130%)}}" +
      "@keyframes omnia-bn-underline{to{transform:scaleX(1)}}" +
      "@media (prefers-reduced-motion:reduce){#" + OVERLAY_ID + "{transition:none}" +
      "#" + OVERLAY_ID + " .omnia-bn-aura{animation:none;opacity:.16}" +
      "#" + OVERLAY_ID + " .omnia-bn-dot{animation:none}" +
      "#" + OVERLAY_ID + " .omnia-bn-dot::before,#" + OVERLAY_ID + " .omnia-bn-dot::after{animation:none;opacity:0}" +
      "#" + OVERLAY_ID + " .omnia-bn-prog>i{animation:none;transform:scaleX(1)}" +
      "#" + OVERLAY_ID + " .omnia-bn-swatches::before{animation:none;transform:translateY(-50%) scaleX(1)}" +
      "#" + OVERLAY_ID + " .omnia-bn-sw{animation:none;opacity:1;transform:none}" +
      "#" + OVERLAY_ID + " .omnia-bn-sw::after{animation:none;opacity:0}" +
      "#" + OVERLAY_ID + " .omnia-bn-line{animation:none;opacity:1;transform:none}" +
      "#" + OVERLAY_ID + " .omnia-bn-line::after{animation:none;transform:scaleX(1)}}" +
      // Born-cascade (pillar 3): as the overlay lifts, the real app's top-level
      // bands rise + sharpen into existence one after another — the reveal hands
      // off to a LIVING app instead of a hard cut. `both` fill-mode holds each
      // band hidden through its stagger delay, then settles it visible; no JS is
      // needed after the class lands, so the app can never get stuck invisible.
      ".omnia-born{animation:omnia-born-rise .62s cubic-bezier(.16,1,.3,1) var(--omnia-born-d,0ms) both}" +
      "@keyframes omnia-born-rise{0%{opacity:0;transform:translateY(18px) scale(.985);filter:blur(7px)}" +
      "55%{filter:blur(0)}100%{opacity:1;transform:none;filter:blur(0)}}" +
      "@media (prefers-reduced-motion:reduce){.omnia-born{animation:none}}";
    (document.head || document.documentElement).appendChild(style);
  }

  var dismissTimer = null;

  // The structural bands we let "rise into existence" — works for both surfaces
  // the script ships on: entity dashboards (app-shell → header / sidebar / the
  // main region's content rows) and the public «/» landing (section bands). We
  // pick a generous candidate set, drop the narration overlay, keep only the
  // OUTERMOST band per subtree (so a parent and its child never both animate),
  // order them top-to-bottom, and cap the count so the cadence stays tight.
  function collectBornTargets() {
    var overlay = document.getElementById(OVERLAY_ID);
    var seen = [];
    function add(nodes) {
      for (var i = 0; i < nodes.length; i++) {
        if (seen.indexOf(nodes[i]) === -1) seen.push(nodes[i]);
      }
    }
    add(
      document.querySelectorAll(
        "header,aside,nav[class],[class*='sidebar'],[class*='Sidebar']"
      )
    );
    var main = document.querySelector("main");
    if (main) {
      add(main.children); // the content bands beneath the app chrome
    } else {
      var root = document.getElementById("__next") || document.body;
      add(root.querySelectorAll("section,[class*='hero'],[class*='Hero']"));
      if (!seen.length && root) add(root.children); // last resort: top bands
    }
    var out = seen.filter(function (el) {
      if (!el || el.nodeType !== 1) return false;
      if (overlay && (el === overlay || overlay.contains(el) || el.contains(overlay)))
        return false;
      for (var j = 0; j < seen.length; j++) {
        if (seen[j] !== el && seen[j].contains(el)) return false; // not outermost
      }
      return true;
    });
    out.sort(function (a, b) {
      var p = a.compareDocumentPosition(b);
      if (p & 4 /* FOLLOWING */) return -1;
      if (p & 2 /* PRECEDING */) return 1;
      return 0;
    });
    return out.slice(0, 12);
  }

  // Hand the reveal off to the live app: stagger a brief rise-and-sharpen across
  // its top-level bands. Run-once (window.__omniaBorn), reduced-motion-safe (the
  // CSS force-settles, and we skip entirely), fail-soft (no targets → the app
  // just appears). Never touches the overlay itself.
  function startBornCascade() {
    if (window.__omniaBorn) return;
    window.__omniaBorn = true;
    if (reducedMotion() || !document.body) return;
    var targets = collectBornTargets();
    var BASE = 30;
    var STEP = 64;
    for (var i = 0; i < targets.length; i++) {
      try {
        targets[i].style.setProperty("--omnia-born-d", BASE + i * STEP + "ms");
        targets[i].classList.add("omnia-born");
      } catch (_) {}
    }
  }

  function remove(born) {
    if (dismissTimer) {
      window.clearTimeout(dismissTimer);
      dismissTimer = null;
    }
    var el = document.getElementById(OVERLAY_ID);
    if (!el) return;
    // As the overlay dissolves, the app underneath is born — a smooth handoff,
    // not a hard cut. Only on a genuine dismiss (auto/skip), not a brief restart.
    if (born) startBornCascade();
    el.classList.add("omnia-bn-out");
    window.setTimeout(function () {
      if (el && el.parentNode) el.parentNode.removeChild(el);
    }, 520);
  }

  function play(brief) {
    var lines = briefLines(brief);
    if (!lines.length) return; // fail-soft: nothing to narrate.
    if (!document.body) return;

    var signature = sig(brief);
    var existing = document.getElementById(OVERLAY_ID);
    if (existing) {
      // Same brief already playing → leave it; a new brief → restart clean.
      if (existing.getAttribute("data-omnia-sig") === signature) return;
      remove();
    }

    injectStyle();
    var swatches = pickHexes(brief.palette || {}, 5);
    var accent = swatches.length ? swatches[0] : "#818cf8";
    var reduced = reducedMotion();
    var step = 850;
    var base = swatches.length ? 360 : 60;
    var total = reduced ? 1500 : base + lines.length * step + 1100;

    var overlay = document.createElement("div");
    overlay.id = OVERLAY_ID;
    overlay.setAttribute("data-omnia-sig", signature);
    overlay.setAttribute("role", "status");
    overlay.setAttribute("aria-live", "polite");
    overlay.style.setProperty("--omnia-bn-accent", accent);
    // Click anywhere to skip the reveal (still births the app underneath).
    overlay.addEventListener("click", function () {
      remove(true);
    });

    // Living brand-tinted aura behind the card — the "alive, being-born" depth.
    var aura = document.createElement("div");
    aura.className = "omnia-bn-aura";
    aura.setAttribute("aria-hidden", "true");
    overlay.appendChild(aura);

    var card = document.createElement("div");
    card.className = "omnia-bn-card";

    var head = document.createElement("div");
    head.className = "omnia-bn-head";
    var dot = document.createElement("span");
    dot.className = "omnia-bn-dot";
    dot.setAttribute("aria-hidden", "true");
    var headText = document.createElement("span");
    headText.textContent = "Omnia · собираю дизайн";
    head.appendChild(dot);
    head.appendChild(headText);
    card.appendChild(head);

    // Progress bar fills over the whole reveal — forward momentum toward "done".
    var prog = document.createElement("div");
    prog.className = "omnia-bn-prog";
    prog.setAttribute("aria-hidden", "true");
    var progFill = document.createElement("i");
    if (!reduced) progFill.style.animationDuration = total + "ms";
    prog.appendChild(progFill);
    card.appendChild(prog);

    if (swatches.length) {
      var row = document.createElement("div");
      row.className = "omnia-bn-swatches";
      swatches.forEach(function (hex, i) {
        var sw = document.createElement("span");
        sw.className = "omnia-bn-sw";
        sw.style.background = hex;
        if (!reduced) sw.style.setProperty("--omnia-d", 120 + i * 90 + "ms");
        row.appendChild(sw);
      });
      card.appendChild(row);
    }

    lines.forEach(function (text, i) {
      var line = document.createElement("div");
      line.className = "omnia-bn-line";
      var span = document.createElement("span");
      span.textContent = text;
      line.appendChild(span);
      if (!reduced) line.style.setProperty("--omnia-d", base + i * step + "ms");
      card.appendChild(line);
    });

    overlay.appendChild(card);
    document.body.appendChild(overlay);
    // Force reflow before adding the fade-in class so the transition runs.
    void overlay.offsetWidth;
    overlay.classList.add("omnia-bn-in");

    dismissTimer = window.setTimeout(function () {
      remove(true);
    }, total);
  }

  // Workspace forwards the brief into the live container over postMessage; a
  // baked `window.__omniaBrief` (shared public surface) plays on load.
  window.addEventListener("message", function (e) {
    var d = e && e.data;
    if (!d || d.type !== "omnia:brief") return;
    try {
      window.__omniaBrief = d.brief || null;
    } catch (_) {}
    play(d.brief);
  });

  function bootFromBaked() {
    if (window.__omniaBrief) play(window.__omniaBrief);
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bootFromBaked);
  } else {
    bootFromBaked();
  }
})();
