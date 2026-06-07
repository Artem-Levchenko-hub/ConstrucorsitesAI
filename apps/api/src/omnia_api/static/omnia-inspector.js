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
 *   parent → iframe (style 1.5): omnia:style:enable | :disable |
 *       omnia:style:set {target:'element'|'token', selector, prop, value} |
 *       omnia:font:link {family, href} | omnia:style:reset {selector?}
 *   iframe → parent: omnia:inspect:ready |
 *       omnia:pick {el:{id,selector,label,text,html,rect,tag,color,backgroundColor,borderColor,fontFamily}}
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

  // Direct style-edit (1.5): when the parent turns on styleMode, clicks select a
  // single element and the parent sends omnia:style:set / omnia:font:link to
  // mutate it LIVE. We render into a TRANSIENT <style id="omnia-overrides-live">
  // (kept last in <head> so its !important rules win) + <link data-omnia-font>
  // tags. We deliberately do NOT reuse the committed "omnia-overrides" id so a
  // new edit session never wipes already-saved overrides baked into the page; on
  // Save the backend merges these edits into the committed block, and a reload
  // collapses both into one — same look (parity).
  var styleMode = false;
  var hoverLabel = null;
  var overrideModel = { tokens: {}, elements: {}, fonts: {} };
  var overrideStyleEl = null;

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
    // A small badge that rides the top-left of the box — used to surface an
    // affordance hint (e.g. "Заменить фото" when hovering an image) so the
    // feature is discoverable instead of hidden behind the mode.
    hoverLabel = document.createElement("div");
    hoverLabel.setAttribute("data-omnia-inspector", "hover-label");
    var ls = hoverLabel.style;
    ls.position = "absolute";
    ls.top = "0";
    ls.left = "0";
    ls.transform = "translateY(-100%)";
    ls.background = HL_COLOR;
    ls.color = "#fff";
    ls.font = "600 11px system-ui, -apple-system, sans-serif";
    ls.padding = "2px 6px";
    ls.borderRadius = "4px";
    ls.whiteSpace = "nowrap";
    ls.pointerEvents = "none";
    ls.display = "none";
    hoverBox.appendChild(hoverLabel);
    (document.body || document.documentElement).appendChild(hoverBox);
    return hoverBox;
  }

  function isOurs(el) {
    return el && el.getAttribute && el.getAttribute("data-omnia-inspector") !== null;
  }

  function positionHoverBox(rect, labelText) {
    var box = ensureHoverBox();
    box.style.display = "block";
    box.style.width = rect.width + "px";
    box.style.height = rect.height + "px";
    box.style.transform = "translate(" + rect.left + "px," + rect.top + "px)";
    if (hoverLabel) {
      if (labelText) {
        hoverLabel.textContent = labelText;
        hoverLabel.style.display = "block";
      } else {
        hoverLabel.style.display = "none";
      }
    }
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
      // In style mode, hint what a click does (replace image / edit text) so the
      // affordances are discoverable instead of buried in the panel.
      var hint = "";
      if (styleMode) {
        if (pickedImg(el, e2.clientX, e2.clientY)) hint = "Заменить фото";
        else if (isPlainTextEl(el)) hint = "Изменить текст";
      }
      positionHoverBox(el.getBoundingClientRect(), hint);
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

  // The <img> the user actually meant: the clicked element itself, the image
  // stacked under the cursor (full-bleed background photo behind overlay
  // content), or a descendant image of the clicked container. null = no image.
  function pickedImg(el, x, y) {
    if (el.nodeName === "IMG") return el;
    if (document.elementsFromPoint) {
      var stack = document.elementsFromPoint(x, y);
      for (var i = 0; i < stack.length; i++) {
        if (stack[i].nodeName === "IMG") return stack[i];
      }
    }
    if (el.querySelector) {
      var inner = el.querySelector("img");
      if (inner) return inner;
    }
    return null;
  }

  // ALL distinct image sources at this point — a carousel/slider stacks several
  // <img> on top of each other, so a single pick can't reach the lower ones.
  // Returns the stack under the cursor (topmost first); falls back to images
  // inside the clicked container when none sit exactly under the point.
  function pickedImgs(el, x, y) {
    var out = [];
    var seen = {};
    function add(im) {
      if (im && im.nodeName === "IMG") {
        var s = im.getAttribute("src") || im.src || "";
        if (s && !seen[s]) {
          seen[s] = 1;
          out.push(s);
        }
      }
    }
    if (el.nodeName === "IMG") add(el);
    if (document.elementsFromPoint) {
      var st = document.elementsFromPoint(x, y);
      for (var i = 0; i < st.length; i++) add(st[i]);
    }
    if (out.length === 0 && el.querySelectorAll) {
      var inn = el.querySelectorAll("img");
      for (var j = 0; j < inn.length; j++) add(inn[j]);
    }
    return out;
  }

  // Text the user can edit in place: an element with NO child elements (pure
  // text) and visible content. Returns its trimmed text + the occurrence index
  // among identical pure-text elements (document order) so the server patches
  // the right one when a label repeats. null when it isn't plain editable text.
  // Light check (no index scan) — pure-text element with visible content. Used
  // by the hover hint on every mousemove, so it must stay cheap.
  function isPlainTextEl(el) {
    if (!el.children || el.children.length !== 0) return false;
    var nn = el.nodeName;
    if (nn === "INPUT" || nn === "TEXTAREA" || nn === "SELECT" ||
        nn === "SCRIPT" || nn === "STYLE" || nn === "IMG" || nn === "SVG") {
      return false;
    }
    return !!(el.textContent || "").trim();
  }

  function textInfo(el) {
    if (!isPlainTextEl(el)) return null;
    var t = (el.textContent || "").trim();
    if (t.length > 5000) return null;
    var all = document.querySelectorAll("*");
    var idx = 0;
    for (var i = 0; i < all.length; i++) {
      var n = all[i];
      if (n === el) break;
      if (n.children && n.children.length === 0 &&
          (n.textContent || "").trim() === t) {
        idx++;
      }
    }
    return { text: t, index: idx };
  }

  // An element's exact source HTML + occurrence index among identical outerHTML
  // blocks (document order) — for hard delete and move (sibling swap).
  function outerHtmlIndex(node) {
    var h = node && node.outerHTML ? node.outerHTML : "";
    if (!h || h.length > 20000) return { html: "", index: 0 };
    var all = document.querySelectorAll("*");
    var idx = 0;
    for (var i = 0; i < all.length && i < 2000; i++) {
      if (all[i] === node) break;
      if (all[i].outerHTML === h) idx++;
    }
    return { html: h, index: idx };
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

    // Style mode selects ONE element at a time — drop the previous outline so the
    // user isn't left with a trail of highlights while recolouring.
    if (styleMode) clearAll();

    // Already picked? Ignore (we still blocked the site's click above). Re-marking
    // the same element would corrupt outline restore — the 2nd mark would capture
    // the 1st mark's outline as "previous".
    for (var k = 0; k < marks.length; k++) {
      if (marks[k].el === el) return;
    }

    var id = String(++counter);
    var r = el.getBoundingClientRect();
    markElement(id, el);
    // Computed color/font so the style panel can show the element's CURRENT
    // values (additive fields — the AI-edit compose path ignores them).
    var cs = window.getComputedStyle(el);
    var imgs = pickedImgs(el, e.clientX, e.clientY);
    var ti = textInfo(el);
    // Exact source HTML + occurrence index for HARD delete and MOVE (swap with a
    // sibling) — for the element and its prev/next sibling.
    var ohInfo = outerHtmlIndex(el);
    var prevS = el.previousElementSibling;
    var nextS = el.nextElementSibling;
    var prevInfo = prevS ? outerHtmlIndex(prevS) : { html: "", index: 0 };
    var nextInfo = nextS ? outerHtmlIndex(nextS) : { html: "", index: 0 };
    post({
      type: "omnia:pick",
      el: {
        id: id,
        selector: cssPath(el),
        label: shortLabel(el),
        text: collapse(el.textContent, MAX_TEXT),
        html: collapse(el.outerHTML, MAX_HTML),
        tag: el.nodeName.toLowerCase(),
        color: cs.color,
        backgroundColor: cs.backgroundColor,
        borderColor: cs.borderTopColor,
        fontFamily: cs.fontFamily,
        // Image sources at the click — usually one, but a carousel stacks
        // several; the panel lets the user choose which to replace. `src` keeps
        // the topmost for back-compat.
        src: imgs[0] || "",
        srcs: imgs,
        editableText: ti ? true : false,
        editText: ti ? ti.text : "",
        textIndex: ti ? ti.index : 0,
        outerHTML: ohInfo.html,
        htmlIndex: ohInfo.index,
        prevHTML: prevInfo.html,
        prevIndex: prevInfo.index,
        nextHTML: nextInfo.html,
        nextIndex: nextInfo.index,
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

  // Mirror of services/overrides.py sanitizers, so live CSS == persisted CSS.
  function escVal(v) {
    return String(v == null ? "" : v).replace(/[<>{};\n\r]/g, "").trim();
  }
  function escSel(s) {
    return String(s == null ? "" : s).replace(/[<{}\n\r]/g, "").trim();
  }

  function ensureFontLink(family, href) {
    if (!family || !href) return;
    overrideModel.fonts[family] = href;
    var head = document.head || document.documentElement;
    var existing = head.querySelector(
      'link[data-omnia-font="' + String(family).replace(/"/g, "") + '"]'
    );
    if (!existing) {
      var l = document.createElement("link");
      l.setAttribute("data-omnia-font", family);
      l.rel = "stylesheet";
      l.href = href;
      head.appendChild(l);
    }
  }

  function renderLiveOverrides() {
    var css = "";
    var tvars = Object.keys(overrideModel.tokens);
    if (tvars.length) {
      css += ":root{";
      for (var i = 0; i < tvars.length; i++) {
        css += escVal(tvars[i]) + ":" + escVal(overrideModel.tokens[tvars[i]]) + " !important;";
      }
      css += "}\n";
    }
    var sels = Object.keys(overrideModel.elements);
    for (var j = 0; j < sels.length; j++) {
      var decls = overrideModel.elements[sels[j]];
      var body = "";
      for (var p in decls) {
        if (Object.prototype.hasOwnProperty.call(decls, p)) {
          body += p + ":" + escVal(decls[p]) + " !important;";
        }
      }
      if (body) css += escSel(sels[j]) + "{" + body + "}\n";
    }
    if (!overrideStyleEl) {
      overrideStyleEl =
        document.getElementById("omnia-overrides-live") ||
        document.createElement("style");
      overrideStyleEl.id = "omnia-overrides-live";
    }
    overrideStyleEl.textContent = css;
    // Re-append so the block stays LAST in <head> and its !important rules win.
    (document.head || document.documentElement).appendChild(overrideStyleEl);
  }

  function setStyle(d) {
    if (d.target === "token") {
      if (!d.prop) return;
      if (d.value == null || d.value === "") delete overrideModel.tokens[d.prop];
      else overrideModel.tokens[d.prop] = d.value;
    } else {
      var sel = d.selector;
      if (!sel || !d.prop) return;
      var e = overrideModel.elements[sel] || (overrideModel.elements[sel] = {});
      if (d.value == null || d.value === "") delete e[d.prop];
      else e[d.prop] = d.value;
      if (!Object.keys(e).length) delete overrideModel.elements[sel];
    }
    renderLiveOverrides();
  }

  function resetStyle(d) {
    if (d && d.selector) delete overrideModel.elements[d.selector];
    else {
      overrideModel.tokens = {};
      overrideModel.elements = {};
    }
    renderLiveOverrides();
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
      case "omnia:style:enable":
        styleMode = true;
        enable();
        break;
      case "omnia:style:disable":
        styleMode = false;
        disable();
        break;
      case "omnia:style:set":
        setStyle(d);
        break;
      case "omnia:font:link":
        ensureFontLink(d.family, d.href);
        break;
      case "omnia:style:reset":
        resetStyle(d);
        break;
    }
  });

  // Tell the parent we're ready so it can (re)send enable after a reload while
  // select-mode is still on.
  if (window.parent) window.parent.postMessage({ type: "omnia:inspect:ready" }, "*");
})();
