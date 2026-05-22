/**
 * Omnia select-mode inspector (canonical source).
 *
 * Lives INSIDE the previewed page. The workspace shell (parent window) talks to
 * it via postMessage; on demand it lets the user hover-highlight and click-pick
 * elements, then reports each pick back so the chat can attach it as a commentable
 * chip. The model edits the HTML *source*, not the live DOM, so we send the
 * element's outerHTML + visible text (more useful for locating it than a CSS
 * selector alone) alongside a best-effort selector.
 *
 * Injected two ways, but this file is the single source of truth:
 *   - static `/p/<slug>?inspect=1` → inlined by apps/api routers/public.py
 *   - fullstack Next.js dev container → <script src> in the template layout
 * A drift test keeps the two copies identical (DRY of knowledge).
 *
 * Dormant until the parent sends `omnia:inspect:enable`, so shipping it in every
 * preview costs nothing until select-mode is turned on.
 *
 * Protocol:
 *   parent → iframe: omnia:inspect:enable | :disable | :clear | :remove {id}
 *   iframe → parent: omnia:inspect:ready  | omnia:pick {el:{id,selector,label,text,html,rect}}
 */
(function () {
  "use strict";

  // Idempotent: serve-time injection + a template <script> could both land on
  // one page; never wire listeners twice.
  if (window.__omniaInspector) return;
  window.__omniaInspector = true;

  var MAX_HTML = 1500;
  var MAX_TEXT = 120;
  var HL_COLOR = "#6366f1"; // indigo-500, matches Omnia accent
  var Z = 2147483600;

  var enabled = false;
  var counter = 0;
  // Picked elements we've outlined: {id, el, prevOutline, prevOffset}. Kept so
  // the parent can clear/remove marks and we can restore the site's own styles.
  var marks = [];
  var hoverBox = null;
  var rafId = 0;
  var pendingEvent = null;

  function ensureHoverBox() {
    if (hoverBox) return hoverBox;
    hoverBox = document.createElement("div");
    hoverBox.setAttribute("data-omnia-inspector", "hover");
    var s = hoverBox.style;
    s.position = "fixed";
    s.pointerEvents = "none"; // never becomes the event target itself
    s.zIndex = String(Z + 1);
    s.border = "2px solid " + HL_COLOR;
    s.background = "rgba(99,102,241,0.12)";
    s.borderRadius = "3px";
    s.transition = "all 60ms ease-out";
    s.display = "none";
    s.top = "0";
    s.left = "0";
    (document.body || document.documentElement).appendChild(hoverBox);
    return hoverBox;
  }

  function isOurs(el) {
    return el && el.getAttribute && el.getAttribute("data-omnia-inspector") !== null;
  }

  function positionHoverBox(rect) {
    var box = ensureHoverBox();
    box.style.display = "block";
    box.style.width = rect.width + "px";
    box.style.height = rect.height + "px";
    box.style.transform = "translate(" + rect.left + "px," + rect.top + "px)";
  }

  function onMouseMove(e) {
    pendingEvent = e;
    if (rafId) return;
    rafId = window.requestAnimationFrame(function () {
      rafId = 0;
      var e2 = pendingEvent;
      if (!e2 || !enabled) return;
      var el = e2.target;
      if (!el || el.nodeType !== 1 || isOurs(el)) return;
      positionHoverBox(el.getBoundingClientRect());
    });
  }

  // Best-effort, reasonably-stable CSS selector. Prefers #id; otherwise builds a
  // child-combinator path with :nth-of-type to disambiguate same-tag siblings,
  // stopping at the nearest id or <body>. The model mostly anchors on the HTML
  // snippet + text, so this is a hint, not a contract.
  function cssPath(el) {
    if (!el || el.nodeType !== 1) return "";
    if (el.id) return "#" + cssEscape(el.id);
    var parts = [];
    var node = el;
    while (node && node.nodeType === 1 && node !== document.body) {
      var tag = node.nodeName.toLowerCase();
      if (node.id) {
        parts.unshift("#" + cssEscape(node.id));
        break;
      }
      var cls = "";
      if (typeof node.className === "string" && node.className.trim()) {
        cls = node.className
          .trim()
          .split(/\s+/)
          .filter(Boolean)
          .slice(0, 2)
          .map(function (c) {
            return "." + cssEscape(c);
          })
          .join("");
      }
      var seg = tag + cls;
      var parent = node.parentNode;
      if (parent && parent.children) {
        var sameTag = Array.prototype.filter.call(parent.children, function (c) {
          return c.nodeName === node.nodeName;
        });
        if (sameTag.length > 1) {
          seg += ":nth-of-type(" + (Array.prototype.indexOf.call(sameTag, node) + 1) + ")";
        }
      }
      parts.unshift(seg);
      node = node.parentNode;
    }
    return parts.join(" > ");
  }

  function cssEscape(s) {
    if (window.CSS && typeof window.CSS.escape === "function") return window.CSS.escape(s);
    // Minimal fallback for ancient engines: escape non-word chars.
    return String(s).replace(/[^a-zA-Z0-9_-]/g, "\\$&");
  }

  function shortLabel(el) {
    var t = el.nodeName.toLowerCase();
    if (el.id) return t + "#" + el.id;
    if (typeof el.className === "string" && el.className.trim()) {
      return t + "." + el.className.trim().split(/\s+/).slice(0, 2).join(".");
    }
    return t;
  }

  function collapse(s, max) {
    var out = (s || "").replace(/\s+/g, " ").trim();
    return out.length > max ? out.slice(0, max) + "…" : out;
  }

  function markElement(id, el) {
    var prevOutline = el.style.outline;
    var prevOffset = el.style.outlineOffset;
    el.style.outline = "2px solid " + HL_COLOR;
    el.style.outlineOffset = "1px";
    marks.push({ id: id, el: el, prevOutline: prevOutline, prevOffset: prevOffset });
  }

  function restoreMark(m) {
    try {
      m.el.style.outline = m.prevOutline;
      m.el.style.outlineOffset = m.prevOffset;
    } catch (_) {
      /* element may have been removed from the DOM by an edit */
    }
  }

  function onClick(e) {
    if (!enabled) return;
    var el = e.target;
    if (!el || el.nodeType !== 1 || isOurs(el)) return;
    // Block the site's own navigation/handlers so picking never triggers a link
    // or button. Capture phase + stopImmediatePropagation = nothing downstream runs.
    e.preventDefault();
    e.stopPropagation();
    e.stopImmediatePropagation();

    // Already picked? Ignore (we still blocked the site's click above). Re-marking
    // the same element would corrupt outline restore — the 2nd mark would capture
    // the 1st mark's outline as "previous".
    for (var k = 0; k < marks.length; k++) {
      if (marks[k].el === el) return;
    }

    var id = String(++counter);
    var r = el.getBoundingClientRect();
    markElement(id, el);
    post({
      type: "omnia:pick",
      el: {
        id: id,
        selector: cssPath(el),
        label: shortLabel(el),
        text: collapse(el.textContent, MAX_TEXT),
        html: collapse(el.outerHTML, MAX_HTML),
        rect: {
          x: Math.round(r.left),
          y: Math.round(r.top),
          width: Math.round(r.width),
          height: Math.round(r.height),
        },
      },
    });
  }

  function enable() {
    if (enabled) return;
    enabled = true;
    document.documentElement.style.cursor = "crosshair";
    document.addEventListener("mousemove", onMouseMove, true);
    // Capture phase so we intercept before the site's own click handlers.
    document.addEventListener("click", onClick, true);
  }

  function disable() {
    // Stop interacting but KEEP existing marks: the user may toggle off, type a
    // comment, then send — selections live in the parent store across the toggle.
    enabled = false;
    document.documentElement.style.cursor = "";
    document.removeEventListener("mousemove", onMouseMove, true);
    document.removeEventListener("click", onClick, true);
    if (hoverBox) hoverBox.style.display = "none";
  }

  function clearAll() {
    marks.forEach(restoreMark);
    marks = [];
  }

  function removeOne(id) {
    var keep = [];
    for (var i = 0; i < marks.length; i++) {
      if (marks[i].id === String(id)) restoreMark(marks[i]);
      else keep.push(marks[i]);
    }
    marks = keep;
  }

  function post(msg) {
    // Target '*' (consistent with the streaming-preview bridge): the only data
    // we emit is the user's own generated markup, and the recipient is the
    // workspace parent by construction. Inbound is origin-guarded below.
    if (window.parent) window.parent.postMessage(msg, "*");
  }

  window.addEventListener("message", function (e) {
    // Only trust the workspace shell that embeds us — ignore any other frame.
    if (e.source !== window.parent) return;
    var d = e.data;
    if (!d || typeof d.type !== "string") return;
    switch (d.type) {
      case "omnia:inspect:enable":
        enable();
        break;
      case "omnia:inspect:disable":
        disable();
        break;
      case "omnia:inspect:clear":
        clearAll();
        break;
      case "omnia:inspect:remove":
        removeOne(d.id);
        break;
    }
  });

  // Tell the parent we're ready so it can (re)send enable after a reload while
  // select-mode is still on.
  if (window.parent) window.parent.postMessage({ type: "omnia:inspect:ready" }, "*");
})();
