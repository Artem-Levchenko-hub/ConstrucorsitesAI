/**
 * Omnia baked brief-narration — "the shared page narrates its own birth"
 * (ONE BRIEF, EVERY SURFACE — pillar 3 + 4). Inlined into a freeform static
 * /p/<slug> by services/brief_narration.inject_brief_narration alongside a
 * `window.__omniaBrief = {…}` payload baked at commit time.
 *
 * Recomputes the narration lines FROM the brief (falsifiable proof the brief
 * surfaced — a hardcoded list would not change with the brief), renders a
 * brand-tinted swatch row + cadenced lines, then fades. Plays once per browser
 * session (sessionStorage keyed by brief signature) so returning visitors
 * aren't nagged. Self-contained (no CDN), reduced-motion-safe, click-to-skip.
 *
 * Mirrors apps/web/src/lib/brief-narration.ts and the entity template's
 * public/omnia-brief-narration.js (same line copy, same role order). The copy
 * is pinned by apps/api tests/test_brief_narration.py (brief_lines).
 */
(function () {
  "use strict";

  var ID = "omnia-bn-overlay";
  var KEY = "omnia-bn-seen";
  var HEX = /^#[0-9a-fA-F]{3,8}$/;

  var brief = window.__omniaBrief;
  if (!brief) return;

  // Role priority: accent → primary → background → rest.
  function role(k) {
    k = String(k || "").toUpperCase();
    if (k.indexOf("АКЦЕНТ") !== -1 || k.indexOf("ACCENT") !== -1) return 0;
    if (k.indexOf("PRIMARY") !== -1) return 1;
    if (k.indexOf("ФОН") !== -1 || k.indexOf("BACKGROUND") !== -1) return 2;
    return 3;
  }

  function hexes(p, n) {
    var e = Object.keys(p || {})
      .map(function (k) {
        return [k, p[k]];
      })
      .filter(function (x) {
        return typeof x[1] === "string" && HEX.test(String(x[1]).trim());
      })
      .sort(function (a, b) {
        return role(a[0]) - role(b[0]);
      });
    var o = [];
    for (var i = 0; i < e.length; i++) {
      var h = String(e[i][1]).trim();
      if (o.indexOf(h) === -1) o.push(h);
      if (o.length >= n) break;
    }
    return o;
  }

  function shortM(m) {
    m = String(m || "").trim();
    if (m.length <= 48) return m;
    var c = m.slice(0, 48);
    var s = c.lastIndexOf(" ");
    return (s > 24 ? c.slice(0, s) : c) + "…";
  }

  // Ordered narration lines — palette → font → sections → motion. A line is
  // included only when its field is non-empty; the list is de-duplicated.
  function lines(b) {
    if (!b) return [];
    var L = [];
    var hx = hexes(b.palette || {}, 2);
    if (hx.length) L.push("Подбираю палитру — " + hx.join(" и "));
    var f = b.fonts || {};
    var d = (f.display || "").trim();
    var t = (f.text || "").trim();
    if (d) L.push("Беру шрифт «" + d + "» для заголовков");
    else if (t) L.push("Беру шрифт «" + t + "» для текста");
    var nm = (b.sections || [])
      .map(function (s) {
        return ((s && s.name) || "").trim();
      })
      .filter(Boolean);
    if (nm.length) {
      var sh = nm.slice(0, 4);
      L.push("Компоную секции: " + sh.join(" → ") + (nm.length > sh.length ? " …" : ""));
    }
    var mo = (b.motion || "").trim();
    if (mo) L.push("Оживляю движением — " + shortM(mo));
    var seen = {};
    return L.filter(function (l) {
      if (seen[l]) return false;
      seen[l] = true;
      return true;
    });
  }

  function reduced() {
    try {
      return (
        window.matchMedia &&
        window.matchMedia("(prefers-reduced-motion:reduce)").matches
      );
    } catch (_) {
      return false;
    }
  }

  function sig(b) {
    try {
      return JSON.stringify(b);
    } catch (_) {
      return "";
    }
  }

  var L = lines(brief);
  if (!L.length) return;

  // Public replay hook for the viral watermark badge ("Сделано на Omnia.AI",
  // injected on the static share page by routers/public.py): a visitor clicks
  // the badge to watch the design be born again on demand. Set BEFORE the
  // once-per-session guard so returning visitors can still replay. Drops any
  // live overlay + timer first, then re-runs play() from the baked brief.
  window.__omniaReplayBrief = function () {
    try {
      sessionStorage.removeItem(KEY);
    } catch (_) {}
    if (timer) {
      clearTimeout(timer);
      timer = null;
    }
    var ex = document.getElementById(ID);
    if (ex && ex.parentNode) ex.parentNode.removeChild(ex);
    play();
    return true;
  };

  try {
    if (sessionStorage.getItem(KEY) === sig(brief)) return;
  } catch (_) {}

  function style() {
    if (document.getElementById("omnia-bn-style")) return;
    var s = document.createElement("style");
    s.id = "omnia-bn-style";
    // Hypnotic birth reveal (pillar 3): living brand-tinted aura, a radar-ping
    // "thinking" orb, a palette that weaves in on a drawn thread with a sheen
    // sweep, a progress bar that fills over the reveal, and accent underline
    // wipes per line. Mirrors the entity template's omnia-brief-narration.js
    // (short class names here; same motion + brief-keyed --bn accent).
    s.textContent =
      "#" + ID + "{position:fixed;inset:0;z-index:2147482000;overflow:hidden;display:flex;align-items:center;justify-content:center;" +
      "background:radial-gradient(120% 120% at 50% 0%,rgba(15,18,28,.82),rgba(8,10,16,.94));" +
      "backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);" +
      "font-family:var(--font-sans,-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif);" +
      "opacity:0;transition:opacity .5s ease}" +
      "#" + ID + ".in{opacity:1}#" + ID + ".out{opacity:0}" +
      "#" + ID + " .au{position:absolute;inset:-25%;pointer-events:none;" +
      "background:radial-gradient(38% 38% at 30% 32%,var(--bn,#818cf8) 0,transparent 60%)," +
      "radial-gradient(34% 34% at 72% 66%,var(--bn,#818cf8) 0,transparent 62%);" +
      "opacity:.20;filter:blur(48px);animation:bndrift 9s ease-in-out infinite alternate}" +
      "#" + ID + " .c{position:relative;width:min(440px,86vw);padding:28px 30px 30px;border-radius:22px;color:#f3f5fb;" +
      "background:linear-gradient(180deg,rgba(255,255,255,.08),rgba(255,255,255,.03));" +
      "border:1px solid rgba(255,255,255,.12);box-shadow:0 30px 80px rgba(0,0,0,.45),inset 0 1px 0 rgba(255,255,255,.08)}" +
      "#" + ID + " .c::before{content:'';position:absolute;left:24px;right:24px;top:0;height:1px;" +
      "background:linear-gradient(90deg,transparent,var(--bn,#818cf8),transparent);opacity:.6}" +
      "#" + ID + " .h{display:flex;align-items:center;gap:11px;font-size:13px;font-weight:600;color:#aab2c8;margin-bottom:16px}" +
      "#" + ID + " .dt{position:relative;width:9px;height:9px;border-radius:9999px;background:var(--bn,#818cf8);" +
      "box-shadow:0 0 14px var(--bn,#818cf8);animation:bnp 1.4s ease-in-out infinite}" +
      "#" + ID + " .dt::before,#" + ID + " .dt::after{content:'';position:absolute;inset:0;border-radius:9999px;" +
      "border:1px solid var(--bn,#818cf8);animation:bnping 2s cubic-bezier(.2,.6,.3,1) infinite}" +
      "#" + ID + " .dt::after{animation-delay:1s}" +
      "#" + ID + " .pg{position:relative;height:2px;border-radius:2px;margin-bottom:18px;background:rgba(255,255,255,.08);overflow:hidden}" +
      "#" + ID + " .pg>i{display:block;height:100%;width:100%;transform-origin:left;transform:scaleX(0);" +
      "background:linear-gradient(90deg,transparent,var(--bn,#818cf8));animation:bnfill linear forwards}" +
      "#" + ID + " .sw{position:relative;display:flex;gap:8px;margin-bottom:18px}" +
      "#" + ID + " .sw::before{content:'';position:absolute;left:4px;right:4px;top:50%;height:1px;" +
      "transform:translateY(-50%) scaleX(0);transform-origin:left;opacity:.5;" +
      "background:linear-gradient(90deg,var(--bn,#818cf8),transparent);animation:bnthread .7s ease-out .1s forwards}" +
      "#" + ID + " .sw>i{position:relative;width:34px;height:34px;border-radius:10px;overflow:hidden;border:1px solid rgba(255,255,255,.18);" +
      "box-shadow:0 4px 14px rgba(0,0,0,.3);opacity:0;transform:scale(.5) translateY(6px);" +
      "animation:bnpop .5s cubic-bezier(.16,1,.3,1) var(--omnia-d,0ms) forwards}" +
      "#" + ID + " .sw>i::after{content:'';position:absolute;inset:0;" +
      "background:linear-gradient(115deg,transparent 32%,rgba(255,255,255,.5) 50%,transparent 68%);" +
      "transform:translateX(-130%);animation:bnsheen 1.5s ease-in-out calc(var(--omnia-d,0ms) + 280ms) 1}" +
      "#" + ID + " .ln{position:relative;display:flex;align-items:flex-start;gap:10px;font-size:15px;line-height:1.5;color:#e7eaf3;margin-top:11px;padding-bottom:3px;" +
      "opacity:0;transform:translateY(8px);animation:bnr .55s cubic-bezier(.16,1,.3,1) var(--omnia-d,0ms) forwards}" +
      "#" + ID + " .ln::before{content:'';flex:0 0 auto;margin-top:8px;width:6px;height:6px;border-radius:9999px;background:var(--bn,#818cf8);box-shadow:0 0 8px var(--bn,#818cf8)}" +
      "#" + ID + " .ln::after{content:'';position:absolute;left:16px;bottom:0;height:1px;width:calc(100% - 16px);" +
      "transform:scaleX(0);transform-origin:left;opacity:.42;background:linear-gradient(90deg,var(--bn,#818cf8),transparent);" +
      "animation:bnund .5s ease-out calc(var(--omnia-d,0ms) + 200ms) forwards}" +
      "@keyframes bnp{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.45;transform:scale(.82)}}" +
      "@keyframes bnpop{to{opacity:1;transform:none}}@keyframes bnr{to{opacity:1;transform:none}}" +
      "@keyframes bndrift{0%{transform:translate(-3%,-2%) scale(1)}100%{transform:translate(4%,3%) scale(1.12)}}" +
      "@keyframes bnping{0%{opacity:.6;transform:scale(1)}100%{opacity:0;transform:scale(3.4)}}" +
      "@keyframes bnfill{to{transform:scaleX(1)}}@keyframes bnthread{to{transform:translateY(-50%) scaleX(1)}}" +
      "@keyframes bnsheen{0%{transform:translateX(-130%)}60%,100%{transform:translateX(130%)}}" +
      "@keyframes bnund{to{transform:scaleX(1)}}" +
      "@media (prefers-reduced-motion:reduce){#" + ID + "{transition:none}" +
      "#" + ID + " .au{animation:none;opacity:.16}#" + ID + " .dt{animation:none}" +
      "#" + ID + " .dt::before,#" + ID + " .dt::after{animation:none;opacity:0}" +
      "#" + ID + " .pg>i{animation:none;transform:scaleX(1)}" +
      "#" + ID + " .sw::before{animation:none;transform:translateY(-50%) scaleX(1)}" +
      "#" + ID + " .sw>i{animation:none;opacity:1;transform:none}#" + ID + " .sw>i::after{animation:none;opacity:0}" +
      "#" + ID + " .ln{animation:none;opacity:1;transform:none}#" + ID + " .ln::after{animation:none;transform:scaleX(1)}}";
    (document.head || document.documentElement).appendChild(s);
  }

  // ── Birth conductor (pillar 3): the overlay hands off to the PAGE itself,
  // which assembles section-by-section in the brand accent — the SAME born
  // swipe the workspace plays on the live stream (#2a), now on the SHARED
  // /p/<slug> surface so a colleague opening the link WATCHES it come alive
  // instead of the card fading to a static page. Self-contained: a brand-accent
  // bar wipes down each top-level section's left edge, cascaded top→bottom, and
  // every kit .reveal is settled so nothing stays hidden after the birth. No
  // dependency on the per-project kit.js version — works on any baked page.
  var birthDone = false;

  function birthStyle() {
    if (document.getElementById("omnia-birth-style")) return;
    var s = document.createElement("style");
    s.id = "omnia-birth-style";
    s.textContent =
      // Left rail wipes top→bottom; a top sweep draws left→right — together a
      // brand-accent corner bracket that frames each section as it is "born".
      ".omnia-born-bar{position:absolute;left:0;top:0;bottom:0;width:4px;border-radius:4px;" +
      "pointer-events:none;z-index:6;transform-origin:top;transform:scaleY(0);opacity:0;" +
      "background:linear-gradient(180deg,var(--obn,#818cf8) 0,var(--obn,#818cf8) 55%,transparent);" +
      "box-shadow:0 0 22px var(--obn,#818cf8);" +
      "animation:obnwipe 1.1s cubic-bezier(.16,1,.3,1) forwards}" +
      ".omnia-born-bar::before{content:'';position:absolute;left:0;top:0;height:4px;" +
      "width:min(160px,42%);border-radius:4px;transform-origin:left;transform:scaleX(0);" +
      "background:linear-gradient(90deg,var(--obn,#818cf8),transparent);" +
      "box-shadow:0 0 16px var(--obn,#818cf8);" +
      "animation:obnsweep 1.1s cubic-bezier(.16,1,.3,1) forwards}" +
      "@keyframes obnwipe{0%{transform:scaleY(0);opacity:0}14%{opacity:1}" +
      "50%{transform:scaleY(1);opacity:1}100%{transform:scaleY(1);opacity:0}}" +
      "@keyframes obnsweep{0%{transform:scaleX(0);opacity:0}20%{opacity:1}" +
      "55%{transform:scaleX(1);opacity:1}100%{transform:scaleX(1);opacity:0}}" +
      "@media (prefers-reduced-motion:reduce){.omnia-born-bar{animation:none;opacity:0}" +
      ".omnia-born-bar::before{animation:none;opacity:0}}";
    (document.head || document.documentElement).appendChild(s);
  }

  // Top-level structural blocks, document order, de-nested (a candidate inside
  // an already-collected candidate is dropped) and skipping tiny/empty nodes.
  function birthTargets() {
    var out = [];
    var all = [].slice.call(
      document.querySelectorAll("section, article, header, footer")
    );
    for (var i = 0; i < all.length; i++) {
      var el = all[i];
      if (el.offsetHeight < 48) continue;
      var nested = false;
      for (var j = 0; j < out.length; j++) {
        if (out[j].contains(el)) {
          nested = true;
          break;
        }
      }
      if (!nested) out.push(el);
      if (out.length >= 12) break;
    }
    return out;
  }

  function birthWave() {
    if (birthDone) return;
    birthDone = true;
    // Settle every kit reveal so the page is fully shown after the birth,
    // independent of the kit's scroll observer (idempotent with it).
    [].slice
      .call(document.querySelectorAll(".reveal,.line-rise"))
      .forEach(function (el) {
        el.classList.add("is-visible");
      });
    if (reduced()) return;
    var targets = birthTargets();
    if (!targets.length) return;
    birthStyle();
    var ac = hexes(brief.palette || {}, 1)[0] || "#818cf8";
    targets.forEach(function (el, i) {
      setTimeout(function () {
        if (getComputedStyle(el).position === "static") el.style.position = "relative";
        var bar = document.createElement("div");
        bar.className = "omnia-born-bar";
        bar.setAttribute("aria-hidden", "true");
        bar.style.setProperty("--obn", ac);
        el.appendChild(bar);
        setTimeout(function () {
          if (bar.parentNode) bar.parentNode.removeChild(bar);
        }, 1300);
      }, i * 130);
    });
  }

  var timer = null;
  function remove() {
    if (timer) {
      clearTimeout(timer);
      timer = null;
    }
    // Hand off to the page birth (guarded → fires once even if the overlay is
    // click-skipped before the scheduled handoff).
    birthWave();
    var el = document.getElementById(ID);
    if (!el) return;
    el.classList.add("out");
    setTimeout(function () {
      if (el && el.parentNode) el.parentNode.removeChild(el);
    }, 520);
  }

  function play() {
    if (!document.body) return;
    style();
    var sw = hexes(brief.palette || {}, 5);
    var ac = sw.length ? sw[0] : "#818cf8";
    var rm = reduced();
    var step = 850;
    var base = sw.length ? 360 : 60;
    var total = rm ? 1500 : base + L.length * step + 1100;
    var o = document.createElement("div");
    o.id = ID;
    o.setAttribute("role", "status");
    o.setAttribute("aria-live", "polite");
    o.style.setProperty("--bn", ac);
    o.addEventListener("click", remove);
    // Living brand-tinted aura behind the card — the "alive, being-born" depth.
    var au = document.createElement("div");
    au.className = "au";
    au.setAttribute("aria-hidden", "true");
    o.appendChild(au);
    var c = document.createElement("div");
    c.className = "c";
    var h = document.createElement("div");
    h.className = "h";
    var dt = document.createElement("span");
    dt.className = "dt";
    dt.setAttribute("aria-hidden", "true");
    var ht = document.createElement("span");
    ht.textContent = "Omnia · собираю дизайн";
    h.appendChild(dt);
    h.appendChild(ht);
    c.appendChild(h);
    // Progress bar fills over the whole reveal — forward momentum toward "done".
    var pg = document.createElement("div");
    pg.className = "pg";
    pg.setAttribute("aria-hidden", "true");
    var pgi = document.createElement("i");
    if (!rm) pgi.style.animationDuration = total + "ms";
    pg.appendChild(pgi);
    c.appendChild(pg);
    if (sw.length) {
      var r = document.createElement("div");
      r.className = "sw";
      sw.forEach(function (hx, i) {
        var b = document.createElement("i");
        b.style.background = hx;
        if (!rm) b.style.setProperty("--omnia-d", 120 + i * 90 + "ms");
        r.appendChild(b);
      });
      c.appendChild(r);
    }
    L.forEach(function (tx, i) {
      var ln = document.createElement("div");
      ln.className = "ln";
      var sp = document.createElement("span");
      sp.textContent = tx;
      ln.appendChild(sp);
      if (!rm) ln.style.setProperty("--omnia-d", base + i * step + "ms");
      c.appendChild(ln);
    });
    o.appendChild(c);
    document.body.appendChild(o);
    void o.offsetWidth;
    o.classList.add("in");
    try {
      sessionStorage.setItem(KEY, sig(brief));
    } catch (_) {}
    timer = setTimeout(remove, total);
    // Start the section-birth cascade just before the card fades, so the page
    // assembles in the brand accent as the overlay concludes — one continuous
    // "AI is designing this → and here it is being built" motion.
    if (!rm) setTimeout(birthWave, Math.max(0, total - 650));
  }

  if (document.readyState === "loading")
    document.addEventListener("DOMContentLoaded", play);
  else play();
})();
