/**
 * Omnia viral "Remix this" CTA for container-backed apps (V4.1b-UI-fullstack
 * render leg). Canonical source — a drift test keeps the copy in
 * nextjs-postgres-drizzle byte-identical (R-04 DRY of knowledge).
 *
 * WHY a separate script (the static /p/<slug> share page injects its CTA inline
 * in apps/api routers/public.py): flagship apps (nextjs_entities, fullstack)
 * don't stay on /p/<slug> — the API 302-redirects share visitors to the LIVE
 * dev/prod container, which lives on a DIFFERENT origin
 * (`<slug>[-dev].preview.<base>`). The static CTA forks via a SAME-ORIGIN
 * `fetch(POST /api/projects/<id>/fork, {credentials:include})` — physically
 * impossible cross-origin (SameSite=lax host-only session cookie never rides a
 * cross-site fetch). So the container button is instead a TOP-LEVEL
 * `<a href>` to the same-origin-on-the-API entry `GET /p/<slug>/remix`, which
 * forks server-side (minting an anon principal when the visitor has no session)
 * and 302→/projects/<fork.id>. Zero CORS, zero credentials dance, works with
 * JS disabled (it's a plain link).
 *
 * Shown ONLY to a top-level public viewer:
 *   - `window.self === window.top` → the owner-workspace embeds this app in an
 *     iframe (`/projects/<id>` preview, `?inspect=1`), so the owner never sees
 *     the remix button on their own editing surface.
 *   - a real container host (a `preview` DNS label, or an explicit
 *     `window.__omniaApiOrigin` override) → never renders a broken link on
 *     localhost / sslip dev where the API origin can't be derived.
 *
 * Self-contained (no CDN), idempotent, reduced-motion-safe. Brand styling is a
 * byte-for-byte mirror of the static CTA in apps/api routers/public.py so the
 * pill looks identical whichever surface served it.
 */
(function () {
  "use strict";

  // The Omnia control plane (API + /p + workspace) is served from this
  // subdomain of the registrable base. Temporary host today (constructor.*);
  // an explicit `window.__omniaApiOrigin` overrides it without a rebuild.
  var API_SUBDOMAIN = "constructor";

  // Only a top-level visitor is a "stranger on a share link"; inside the owner
  // workspace iframe (self !== top) the button stays hidden.
  function isTopLevel() {
    try {
      return window.self === window.top;
    } catch (_) {
      return false; // cross-origin framing throws → treat as embedded, hide.
    }
  }

  // Project slug from the container hostname: `<slug>[-dev].<suffix>` → strip
  // the leading label's `-dev` dev-preview marker.
  function slugFromHost() {
    var first = (location.hostname || "").split(".")[0] || "";
    return first.replace(/-dev$/, "");
  }

  // Absolute origin of the Omnia control plane, where `GET /p/<slug>/remix`
  // lives. Explicit override wins; otherwise derive `https://constructor.<base>`
  // from the container host by dropping the leading container label and the
  // `preview` infix, leaving the registrable base (e.g. lead-generator.ru).
  // Returns "" when it can't be derived (no `preview` label, no override) so the
  // caller renders nothing rather than a broken link.
  function apiOrigin() {
    if (window.__omniaApiOrigin) {
      return String(window.__omniaApiOrigin).replace(/\/+$/, "");
    }
    var labels = (location.hostname || "").split(".");
    if (labels.indexOf("preview") === -1) return ""; // not a real preview host.
    var rest = labels.slice(1);
    if (rest[0] === "preview") rest = rest.slice(1);
    var base = rest.join(".");
    if (!base) return "";
    return location.protocol + "//" + API_SUBDOMAIN + "." + base;
  }

  function remixUrl() {
    var origin = apiOrigin();
    var slug = slugFromHost();
    if (!origin || !slug) return "";
    return origin + "/p/" + encodeURIComponent(slug) + "/remix";
  }

  function mount() {
    // Idempotent: serve-time + script could both land once; never double-mount.
    if (document.getElementById("omnia-remix-cta")) return;
    if (!isTopLevel()) return;
    var href = remixUrl();
    if (!href) return;

    var style = document.createElement("style");
    style.id = "omnia-remix-style";
    style.textContent =
      "#omnia-remix-cta{position:fixed;right:20px;bottom:20px;z-index:2147483000;" +
      "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}" +
      "#omnia-remix-btn{display:inline-flex;align-items:center;gap:9px;border:0;" +
      "cursor:pointer;padding:14px 21px;border-radius:9999px;font-size:15px;" +
      "font-weight:600;letter-spacing:-.01em;color:#fff;text-decoration:none;" +
      "white-space:nowrap;" +
      "background:linear-gradient(90deg,#818cf8,#c084fc);" +
      "box-shadow:0 8px 28px rgba(99,102,241,.42),0 2px 8px rgba(0,0,0,.18);" +
      "transition:transform .18s ease,box-shadow .18s ease;" +
      "animation:omnia-remix-in .5s cubic-bezier(.16,1,.3,1) both}" +
      "#omnia-remix-btn:hover{transform:translateY(-2px) scale(1.02);" +
      "box-shadow:0 12px 34px rgba(99,102,241,.5),0 3px 10px rgba(0,0,0,.2)}" +
      "#omnia-remix-btn:active{transform:translateY(0) scale(.99)}" +
      "#omnia-remix-btn .omnia-remix-spark{font-size:16px;line-height:1;" +
      "animation:omnia-remix-spin 4s linear infinite}" +
      "@keyframes omnia-remix-in{from{opacity:0;transform:translateY(14px) scale(.96)}" +
      "to{opacity:1;transform:none}}" +
      "@keyframes omnia-remix-spin{to{transform:rotate(360deg)}}" +
      "@media (prefers-reduced-motion:reduce){#omnia-remix-btn," +
      "#omnia-remix-btn .omnia-remix-spark{animation:none}" +
      "#omnia-remix-btn:hover{transform:none}}";

    var wrap = document.createElement("div");
    wrap.id = "omnia-remix-cta";

    var a = document.createElement("a");
    a.id = "omnia-remix-btn";
    a.href = href;
    // Plain top-level navigation: the GET /remix forks server-side and 302s the
    // visitor into the workspace. `rel=nofollow` keeps crawlers from minting
    // stray anon forks on prefetch; `target=_top` is belt-and-braces for the
    // (already top-level) page.
    a.setAttribute("rel", "nofollow");
    a.setAttribute("target", "_top");
    a.setAttribute(
      "aria-label",
      "Сделать свою версию этого приложения на Omnia.AI"
    );

    var spark = document.createElement("span");
    spark.className = "omnia-remix-spark";
    spark.setAttribute("aria-hidden", "true");
    spark.textContent = "✦";

    var label = document.createElement("span");
    label.className = "omnia-remix-label";
    label.textContent = "Сделать свою версию";

    a.appendChild(spark);
    a.appendChild(label);
    wrap.appendChild(a);

    (document.head || document.documentElement).appendChild(style);
    (document.body || document.documentElement).appendChild(wrap);

    mountWatermark();
  }

  // Viral seed badge (#VIRAL-WATERMARK, pillar 4). A subtle "Сделано на Omnia.AI"
  // credit pinned bottom-LEFT (opposite the loud remix pill). Click opens a small
  // branded popover that (a) replays the "design being born" reveal on demand via
  // window.__omniaReplayBrief (omnia-brief-narration.js) and (b) offers a fresh
  // "Создать свой сайт" CTA → the Omnia landing. This turns every shared app into
  // a self-explaining seed: a colleague sees the app, learns where it came from,
  // and can make their own — the watermark is a living invite, not a dead label.
  // Same gates as the pill (top-level visitor + derivable control-plane origin),
  // so dev hosts never render a broken link. Self-contained, reduced-motion-safe.
  function mountWatermark() {
    if (document.getElementById("omnia-wm")) return;
    if (!isTopLevel()) return;
    var origin = apiOrigin();
    if (!origin) return; // can't offer "make your own" without the landing origin.
    var homeHref = origin + "/";

    var style = document.createElement("style");
    style.id = "omnia-wm-style";
    style.textContent =
      "#omnia-wm{position:fixed;left:18px;bottom:18px;z-index:2147482999;" +
      "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}" +
      "#omnia-wm-badge{display:inline-flex;align-items:center;gap:7px;cursor:pointer;" +
      "border:1px solid rgba(255,255,255,.16);background:rgba(15,16,28,.62);" +
      "-webkit-backdrop-filter:blur(10px) saturate(1.25);backdrop-filter:blur(10px) saturate(1.25);" +
      "color:rgba(255,255,255,.88);padding:9px 14px 9px 12px;border-radius:9999px;" +
      "font-size:12.5px;font-weight:600;letter-spacing:-.01em;line-height:1;" +
      "box-shadow:0 6px 22px rgba(0,0,0,.3);" +
      "transition:transform .18s ease,box-shadow .18s ease,border-color .18s ease;" +
      "animation:omnia-wm-in .6s cubic-bezier(.16,1,.3,1) both}" +
      "#omnia-wm-badge:hover{transform:translateY(-2px);border-color:rgba(192,132,252,.55);" +
      "box-shadow:0 10px 30px rgba(99,102,241,.34)}" +
      "#omnia-wm-spark{font-size:13px;line-height:1;" +
      "background:linear-gradient(90deg,#818cf8,#c084fc);-webkit-background-clip:text;" +
      "background-clip:text;color:transparent}" +
      "#omnia-wm-name{background:linear-gradient(90deg,#a5b4fc,#e9d5ff);" +
      "-webkit-background-clip:text;background-clip:text;color:transparent}" +
      "#omnia-wm-pop{position:absolute;left:0;bottom:calc(100% + 10px);width:288px;" +
      "max-width:calc(100vw - 36px);padding:18px;border-radius:18px;" +
      "border:1px solid rgba(255,255,255,.12);background:rgba(17,18,30,.92);" +
      "-webkit-backdrop-filter:blur(16px) saturate(1.3);backdrop-filter:blur(16px) saturate(1.3);" +
      "box-shadow:0 24px 64px rgba(0,0,0,.5),0 2px 10px rgba(0,0,0,.3);" +
      "color:#e9eaf3;opacity:0;transform:translateY(8px) scale(.97);transform-origin:bottom left;" +
      "pointer-events:none;transition:opacity .2s ease,transform .24s cubic-bezier(.16,1,.3,1)}" +
      "#omnia-wm-pop::before{content:'';position:absolute;inset:0;border-radius:18px;padding:1px;" +
      "background:linear-gradient(135deg,rgba(129,140,248,.5),rgba(192,132,252,.18) 55%,transparent);" +
      "-webkit-mask:linear-gradient(#000 0 0) content-box,linear-gradient(#000 0 0);" +
      "-webkit-mask-composite:xor;mask-composite:exclude;pointer-events:none}" +
      "#omnia-wm.open #omnia-wm-pop{opacity:1;transform:none;pointer-events:auto}" +
      "#omnia-wm.open #omnia-wm-badge{border-color:rgba(192,132,252,.55)}" +
      "#omnia-wm-pop h4{margin:0 0 6px;font-size:14px;font-weight:700;letter-spacing:-.01em;" +
      "display:flex;align-items:center;gap:7px;color:#fff}" +
      "#omnia-wm-pop h4 b{background:linear-gradient(90deg,#a5b4fc,#e9d5ff);" +
      "-webkit-background-clip:text;background-clip:text;color:transparent;font-weight:700}" +
      "#omnia-wm-pop p{margin:0 0 14px;font-size:12.5px;line-height:1.5;color:rgba(233,234,243,.72)}" +
      "#omnia-wm-replay{display:inline-flex;align-items:center;gap:7px;width:100%;" +
      "justify-content:center;cursor:pointer;border:1px solid rgba(255,255,255,.14);" +
      "background:rgba(255,255,255,.04);color:#e9eaf3;padding:11px 14px;border-radius:11px;" +
      "font-size:12.5px;font-weight:600;margin-bottom:8px;" +
      "transition:background .16s ease,border-color .16s ease}" +
      "#omnia-wm-replay:hover{background:rgba(255,255,255,.09);border-color:rgba(192,132,252,.45)}" +
      "#omnia-wm-make{display:flex;align-items:center;justify-content:center;gap:7px;width:100%;" +
      "box-sizing:border-box;cursor:pointer;border:0;text-decoration:none;color:#fff;" +
      "padding:13px 16px;border-radius:11px;font-size:13.5px;font-weight:700;letter-spacing:-.01em;" +
      "background:linear-gradient(90deg,#818cf8,#c084fc);" +
      "box-shadow:0 8px 24px rgba(99,102,241,.4);" +
      "transition:transform .16s ease,box-shadow .16s ease}" +
      "#omnia-wm-make:hover{transform:translateY(-1px);box-shadow:0 12px 30px rgba(99,102,241,.5)}" +
      "@keyframes omnia-wm-in{from{opacity:0;transform:translateY(12px) scale(.94)}to{opacity:1;transform:none}}" +
      "@media (max-width:479px){#omnia-wm-made{display:none}}" +
      "@media (prefers-reduced-motion:reduce){#omnia-wm-badge,#omnia-wm-pop,#omnia-wm-make{" +
      "animation:none!important;transition:none!important}#omnia-wm-badge:hover{transform:none}}";

    var wrap = document.createElement("div");
    wrap.id = "omnia-wm";

    var badge = document.createElement("button");
    badge.type = "button";
    badge.id = "omnia-wm-badge";
    badge.setAttribute("aria-haspopup", "dialog");
    badge.setAttribute("aria-expanded", "false");
    badge.setAttribute("aria-label", "Сделано на Omnia.AI — создать свой сайт");
    var spark = document.createElement("span");
    spark.id = "omnia-wm-spark";
    spark.setAttribute("aria-hidden", "true");
    spark.textContent = "✦";
    var made = document.createElement("span");
    made.id = "omnia-wm-made";
    made.textContent = "Сделано на ";
    var name = document.createElement("span");
    name.id = "omnia-wm-name";
    name.textContent = "Omnia.AI";
    badge.appendChild(spark);
    badge.appendChild(made);
    badge.appendChild(name);

    var pop = document.createElement("div");
    pop.id = "omnia-wm-pop";
    pop.setAttribute("role", "dialog");
    pop.setAttribute("aria-label", "Создано на Omnia.AI");
    var h4 = document.createElement("h4");
    var h4spark = document.createElement("span");
    h4spark.setAttribute("aria-hidden", "true");
    h4spark.textContent = "✦";
    var h4name = document.createElement("b");
    h4name.textContent = "Omnia.AI";
    h4.appendChild(h4spark);
    h4.appendChild(h4name);
    var p = document.createElement("p");
    p.textContent =
      "Этот сайт собран искусственным интеллектом из одного промпта — за пару минут.";
    pop.appendChild(h4);
    pop.appendChild(p);

    function open() {
      wrap.classList.add("open");
      badge.setAttribute("aria-expanded", "true");
      document.addEventListener("click", onDoc, true);
      document.addEventListener("keydown", onKey);
    }
    function close() {
      wrap.classList.remove("open");
      badge.setAttribute("aria-expanded", "false");
      document.removeEventListener("click", onDoc, true);
      document.removeEventListener("keydown", onKey);
    }
    function onDoc(e) {
      if (!wrap.contains(e.target)) close();
    }
    function onKey(e) {
      if (e.key === "Escape" || e.keyCode === 27) close();
    }

    // Replay the live "design being born" reveal — only when the narration
    // module exposed its hook (baked/forwarded brief present).
    if (typeof window.__omniaReplayBrief === "function") {
      var replay = document.createElement("button");
      replay.type = "button";
      replay.id = "omnia-wm-replay";
      var rIcon = document.createElement("span");
      rIcon.setAttribute("aria-hidden", "true");
      rIcon.textContent = "▶";
      var rTxt = document.createElement("span");
      rTxt.textContent = "Посмотреть, как он родился";
      replay.appendChild(rIcon);
      replay.appendChild(rTxt);
      replay.addEventListener("click", function () {
        close();
        try {
          window.__omniaReplayBrief();
        } catch (_) {}
      });
      pop.appendChild(replay);
    }

    var make = document.createElement("a");
    make.id = "omnia-wm-make";
    make.href = homeHref;
    make.setAttribute("target", "_blank");
    make.setAttribute("rel", "noopener");
    var mTxt = document.createElement("span");
    mTxt.textContent = "Создать свой сайт";
    var mArrow = document.createElement("span");
    mArrow.setAttribute("aria-hidden", "true");
    mArrow.textContent = "→";
    make.appendChild(mTxt);
    make.appendChild(mArrow);
    pop.appendChild(make);

    wrap.appendChild(badge);
    wrap.appendChild(pop);

    badge.addEventListener("click", function (e) {
      e.stopPropagation();
      if (wrap.classList.contains("open")) close();
      else open();
    });

    (document.head || document.documentElement).appendChild(style);
    (document.body || document.documentElement).appendChild(wrap);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mount);
  } else {
    mount();
  }
})();
