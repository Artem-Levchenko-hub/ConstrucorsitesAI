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
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mount);
  } else {
    mount();
  }
})();
