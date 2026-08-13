/**
 * Omnia select-mode inspector (canonical source).
 *
 * Lives INSIDE the previewed page. The workspace shell (parent window) talks to
 * it via postMessage; on demand it lets the user hover-highlight and click-pick
 * elements, then reports one pick and immediately releases click interception so
 * the generated app stays interactive. The model edits the HTML *source*, not the live DOM, so we send the
 * element's outerHTML + visible text (more useful for locating it than a CSS
 * selector alone) alongside a best-effort selector.
 *
 * Delivered by the platform preview boundary, with legacy fallbacks:
 *   - static `/p/<slug>?inspect=1` → inlined by apps/api routers/public.py
 *   - every live `*-dev.preview.*` HTML response → nginx injects a same-origin
 *     script tag pointing back to apps/api (works for already-created projects)
 *   - fullstack template <script src> copies remain as an offline/local fallback
 * A drift test keeps the fallback copies identical (DRY of knowledge).
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

  var loaderScript = document.currentScript;
  var trustedParentOrigin =
    loaderScript && loaderScript.getAttribute
      ? loaderScript.getAttribute("data-omnia-parent-origin") || ""
      : "";
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
  var hoveredEl = null;
  var hoveredLabel = "";

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
  var hoverLabelText = null;
  var overrideModel = { tokens: {}, elements: {}, fonts: {} };
  var overrideStyleEl = null;
  var previewChromeStyleEl = null;

  // The device mock keeps scrolling available while hiding the desktop browser
  // scrollbar that would otherwise appear inside the phone bezel. This is
  // opt-in from the embedding shell, so standalone/public apps are untouched.
  function setPreviewChrome(options) {
    var hideScrollbar = options && options.hideScrollbar === true;
    if (!hideScrollbar) {
      if (previewChromeStyleEl) previewChromeStyleEl.remove();
      previewChromeStyleEl = null;
      return;
    }
    if (!previewChromeStyleEl) {
      previewChromeStyleEl = document.createElement("style");
      previewChromeStyleEl.id = "omnia-preview-chrome";
      previewChromeStyleEl.setAttribute("data-omnia-inspector", "preview-chrome");
      previewChromeStyleEl.textContent =
        "html{scrollbar-width:none!important;-ms-overflow-style:none!important}" +
        "html::-webkit-scrollbar,body::-webkit-scrollbar{display:none!important;width:0!important;height:0!important}";
    }
    (document.head || document.documentElement).appendChild(previewChromeStyleEl);
  }

  // One-time CSS for the targeting-reticle hover box: a soft corner-bracket pulse
  // (camera-focus vibe), reduced-motion-safe. Injected into the previewed page.
  function injectReticleCSS() {
    if (document.getElementById("omnia-reticle-css")) return;
    var st = document.createElement("style");
    st.id = "omnia-reticle-css";
    st.setAttribute("data-omnia-inspector", "css");
    st.textContent =
      "@keyframes omnia-reticle-pulse{0%,100%{opacity:1}50%{opacity:.5}}" +
      "[data-omnia-corner]{animation:omnia-reticle-pulse 1.4s ease-in-out infinite}" +
      "@media (prefers-reduced-motion:reduce){[data-omnia-corner]{animation:none}}";
    (document.head || document.documentElement).appendChild(st);
  }

  // An L-shaped bracket pinned to one corner of the hover box.
  function makeCorner(pos) {
    var c = document.createElement("div");
    c.setAttribute("data-omnia-inspector", "corner");
    c.setAttribute("data-omnia-corner", pos);
    var s = c.style;
    s.position = "absolute";
    s.width = "11px";
    s.height = "11px";
    s.pointerEvents = "none";
    var b = "2.5px solid " + HL_COLOR;
    if (pos === "tl") { s.top = "-1px"; s.left = "-1px"; s.borderTop = b; s.borderLeft = b; s.borderTopLeftRadius = "5px"; }
    else if (pos === "tr") { s.top = "-1px"; s.right = "-1px"; s.borderTop = b; s.borderRight = b; s.borderTopRightRadius = "5px"; }
    else if (pos === "bl") { s.bottom = "-1px"; s.left = "-1px"; s.borderBottom = b; s.borderLeft = b; s.borderBottomLeftRadius = "5px"; }
    else { s.bottom = "-1px"; s.right = "-1px"; s.borderBottom = b; s.borderRight = b; s.borderBottomRightRadius = "5px"; }
    return c;
  }

  function ensureHoverBox() {
    if (hoverBox) return hoverBox;
    injectReticleCSS();
    hoverBox = document.createElement("div");
    hoverBox.setAttribute("data-omnia-inspector", "hover");
    var s = hoverBox.style;
    s.position = "fixed";
    s.pointerEvents = "none"; // never becomes the event target itself
    s.zIndex = String(Z + 1);
    // Thin outline + soft brand glow + a faint diagonal wash — a "targeting"
    // frame rather than a flat box. The 4 corner brackets below sell the look.
    s.border = "1px solid rgba(99,102,241,0.55)";
    s.background =
      "linear-gradient(135deg, rgba(99,102,241,0.10), rgba(139,92,246,0.05))";
    s.borderRadius = "6px";
    s.boxShadow =
      "0 0 0 1px rgba(99,102,241,0.15), 0 6px 22px -4px rgba(99,102,241,0.5)";
    // Smooth "snap" from element to element instead of a jumpy jump.
    s.transition =
      "transform 130ms cubic-bezier(.22,1,.36,1)," +
      "width 130ms cubic-bezier(.22,1,.36,1)," +
      "height 130ms cubic-bezier(.22,1,.36,1)";
    s.display = "none";
    s.top = "0";
    s.left = "0";
    hoverBox.appendChild(makeCorner("tl"));
    hoverBox.appendChild(makeCorner("tr"));
    hoverBox.appendChild(makeCorner("bl"));
    hoverBox.appendChild(makeCorner("br"));
    // A badge riding the top-left: a pulsing dot + the element tag (or an
    // affordance hint like "Заменить фото") — so you always know what you're on.
    hoverLabel = document.createElement("div");
    hoverLabel.setAttribute("data-omnia-inspector", "hover-label");
    var ls = hoverLabel.style;
    ls.position = "absolute";
    ls.top = "-3px";
    ls.left = "-1px";
    ls.transform = "translateY(-100%)";
    ls.display = "none";
    ls.alignItems = "center";
    ls.gap = "5px";
    ls.background = "rgba(30,27,66,0.94)";
    ls.backdropFilter = "blur(4px)";
    ls.webkitBackdropFilter = "blur(4px)";
    ls.color = "#fff";
    ls.font = "600 11px system-ui, -apple-system, sans-serif";
    ls.padding = "3px 8px";
    ls.borderRadius = "7px";
    ls.whiteSpace = "nowrap";
    ls.pointerEvents = "none";
    ls.boxShadow = "0 3px 12px -2px rgba(0,0,0,0.55)";
    var dot = document.createElement("span");
    dot.setAttribute("data-omnia-inspector", "dot");
    dot.setAttribute("data-omnia-corner", "dot");
    var dstyle = dot.style;
    dstyle.width = "6px";
    dstyle.height = "6px";
    dstyle.borderRadius = "50%";
    dstyle.background = HL_COLOR;
    dstyle.flex = "none";
    dstyle.boxShadow = "0 0 6px " + HL_COLOR;
    hoverLabelText = document.createElement("span");
    hoverLabelText.setAttribute("data-omnia-inspector", "hover-label-text");
    hoverLabel.appendChild(dot);
    hoverLabel.appendChild(hoverLabelText);
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
    if (hoverLabel && hoverLabelText) {
      if (labelText) {
        hoverLabelText.textContent = labelText;
        hoverLabel.style.display = "inline-flex";
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
      var el = eventElement(e2);
      if (!el || el.nodeType !== 1 || isOurs(el)) return;
      // In style mode, hint what a click does (replace image / edit text) so the
      // affordances are discoverable instead of buried in the panel.
      var hint = "";
      if (styleMode) {
        if (pickedImg(el, e2.clientX, e2.clientY)) hint = "Заменить фото";
        else if (isPlainTextEl(el)) hint = "Изменить текст";
      }
      // Always label what you're hovering — the affordance hint when there is
      // one, otherwise just the element tag. Makes every pick legible.
      var label = hint || "<" + el.nodeName.toLowerCase() + ">";
      hoveredEl = el;
      hoveredLabel = label;
      positionHoverBox(el.getBoundingClientRect(), label);
    });
  }

  function eventElement(e) {
    // `composedPath()` sees through an open shadow root. A document selector
    // cannot cross that boundary, so persistently target the owning host.
    var path = e && typeof e.composedPath === "function" ? e.composedPath() : [];
    var el = path && path.length ? path[0] : e && e.target;
    if (!el || el.nodeType !== 1) return null;
    var root = el.getRootNode ? el.getRootNode() : document;
    if (root && root.host) el = root.host;
    // Raw SVG paths/circles are implementation details; the owning <svg> is the
    // stable source element and has the useful visible geometry.
    if (el.ownerSVGElement) el = el.ownerSVGElement;
    return el;
  }

  function selectorMatchesOnly(selector, el) {
    try {
      var all = document.querySelectorAll(selector);
      return all.length === 1 && all[0] === el;
    } catch (_) {
      return false;
    }
  }

  function attrEscape(s) {
    return String(s).replace(/\\/g, "\\\\").replace(/"/g, '\\"');
  }

  function attrCandidate(el, name) {
    var value = el.getAttribute && el.getAttribute(name);
    if (!value || value.length > 160) return "";
    return (
      el.nodeName.toLowerCase() +
      "[" +
      name +
      '="' +
      attrEscape(value) +
      '"]'
    );
  }

  function stableClasses(el) {
    if (typeof el.className !== "string") return [];
    return el.className
      .trim()
      .split(/\s+/)
      .filter(function (c) {
        if (!c || c.length > 80) return false;
        if (/^(active|selected|focus|hover|open|closed|enter|leave)$/i.test(c))
          return false;
        if (/^(css|jsx|sc|emotion)-?[a-z0-9_-]*[0-9a-f]{6,}$/i.test(c))
          return false;
        return !/^[a-z0-9_-]*[0-9a-f]{10,}$/i.test(c);
      })
      .slice(0, 2);
  }

  // Deterministic, unique selector intended to survive a React re-render:
  // stable authored identity first, semantic attributes/classes next, and a
  // guaranteed-unique structural path last. Every short candidate is verified
  // against the live DOM before it is returned.
  function cssPath(el) {
    if (!el || el.nodeType !== 1) return "";
    if (el === document.documentElement) return "html";
    if (el === document.body) return "body";
    if (el.id) {
      var byId = "#" + cssEscape(el.id);
      if (selectorMatchesOnly(byId, el)) return byId;
    }
    var attrs = [
      "data-omnia-id",
      "data-testid",
      "data-test",
      "data-cy",
      "name",
      "aria-label",
    ];
    for (var ai = 0; ai < attrs.length; ai++) {
      var byAttr = attrCandidate(el, attrs[ai]);
      if (byAttr && selectorMatchesOnly(byAttr, el)) return byAttr;
    }
    var ownClasses = stableClasses(el);
    if (ownClasses.length) {
      var byClass =
        el.nodeName.toLowerCase() +
        ownClasses
          .map(function (c) {
            return "." + cssEscape(c);
          })
          .join("");
      if (selectorMatchesOnly(byClass, el)) return byClass;
    }
    var parts = [];
    var node = el;
    while (node && node.nodeType === 1 && node !== document.body) {
      var tag = node.nodeName.toLowerCase();
      if (node.id) {
        var anchor = "#" + cssEscape(node.id);
        if (selectorMatchesOnly(anchor, node)) {
          parts.unshift(anchor);
          break;
        }
      }
      var cls = stableClasses(node)
        .map(function (c) {
          return "." + cssEscape(c);
        })
        .join("");
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
      var candidate = parts.join(" > ");
      if (selectorMatchesOnly(candidate, el)) return candidate;
      node = node.parentNode;
    }
    var anchoredPath = parts.join(" > ");
    if (selectorMatchesOnly(anchoredPath, el)) return anchoredPath;
    var fullPath = "body > " + parts.join(" > ");
    return selectorMatchesOnly(fullPath, el) ? fullPath : "";
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
    var el = eventElement(e);
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
    // Picking is single-shot. Release capture listeners synchronously inside
    // the iframe as well as notifying the parent, so the very next interaction
    // reaches the generated app even across a delayed postMessage round-trip.
    // `disable()` deliberately keeps the selected outline in place.
    setEditorMode("off");
  }

  function blockEarlyInteraction(e) {
    if (!enabled) return;
    var el = eventElement(e);
    if (!el || isOurs(el)) return;
    // Generated controls often mutate on pointerdown, before click. Stop that
    // phase too; onClick below remains the single element-pick path.
    e.preventDefault();
    e.stopPropagation();
    e.stopImmediatePropagation();
  }

  function refreshHover() {
    if (!enabled || !hoveredEl || !hoveredEl.isConnected) {
      if (hoverBox) hoverBox.style.display = "none";
      return;
    }
    positionHoverBox(hoveredEl.getBoundingClientRect(), hoveredLabel);
  }

  function enable() {
    if (enabled) return;
    enabled = true;
    document.documentElement.style.cursor = "crosshair";
    document.addEventListener("mousemove", onMouseMove, true);
    document.addEventListener("pointerdown", blockEarlyInteraction, true);
    document.addEventListener("click", recordClickBreadcrumb, true);
    document.addEventListener("change", recordChangeBreadcrumb, true);
    // Capture phase so we intercept before the site's own click handlers.
    document.addEventListener("click", onClick, true);
    window.addEventListener("scroll", refreshHover, true);
    window.addEventListener("resize", refreshHover, true);
  }

  function disable() {
    // Stop interacting but KEEP existing marks: the user may toggle off, type a
    // comment, then send — selections live in the parent store across the toggle.
    enabled = false;
    document.documentElement.style.cursor = "";
    document.removeEventListener("mousemove", onMouseMove, true);
    document.removeEventListener("pointerdown", blockEarlyInteraction, true);
    document.removeEventListener("click", recordClickBreadcrumb, true);
    document.removeEventListener("change", recordChangeBreadcrumb, true);
    document.removeEventListener("click", onClick, true);
    window.removeEventListener("scroll", refreshHover, true);
    window.removeEventListener("resize", refreshHover, true);
    hoveredEl = null;
    pendingEvent = null;
    if (rafId) {
      window.cancelAnimationFrame(rafId);
      rafId = 0;
    }
    if (hoverBox) hoverBox.style.display = "none";
  }

  // Atomic mode transition used by the workspace shell. A single command avoids
  // the old inspect-enable/style-disable race when switching Manual → AI.
  function setEditorMode(mode) {
    if (mode !== "inspect" && mode !== "style") mode = "off";
    styleMode = mode === "style";
    if (mode === "off") disable();
    else enable();
    post({ type: "omnia:editor:state", mode: mode });
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
    if (window.parent && window.parent !== window)
      window.parent.postMessage(msg, trustedParentOrigin || "*");
  }

  window.addEventListener("message", function (e) {
    // Only trust the workspace shell that embeds us — ignore any other frame.
    if (e.source !== window.parent) return;
    if (trustedParentOrigin && e.origin !== trustedParentOrigin) return;
    var d = e.data;
    if (!d || typeof d.type !== "string") return;
    switch (d.type) {
      case "omnia:inspect:ping":
        post({ type: "omnia:inspect:ready", version: 5 });
        break;
      case "omnia:editor:set-mode":
        setEditorMode(d.mode);
        break;
      case "omnia:inspect:enable":
        setEditorMode("inspect");
        break;
      case "omnia:inspect:disable":
        if (!styleMode) setEditorMode("off");
        break;
      case "omnia:inspect:clear":
        clearAll();
        break;
      case "omnia:inspect:remove":
        removeOne(d.id);
        break;
      case "omnia:style:enable":
        setEditorMode("style");
        break;
      case "omnia:style:disable":
        if (styleMode) setEditorMode("off");
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
      case "omnia:preview:chrome":
        setPreviewChrome(d);
        break;
    }
  });

  // ── Runtime-error reporter (always on, independent of select-mode) ────────
  // Forwards UNCAUGHT JS errors + unhandled promise rejections from the previewed
  // page up to the workspace shell, which turns them into a chat card. Strict
  // gating so a healthy app never spams the chat (R-10):
  //   * only genuine uncaught exceptions — resource 404s (img/script/link) and
  //     console.warn/log are ignored;
  //   * dedup by signature + a hard cap per page load;
  //   * silent unless a workspace parent embeds us — standalone opens and the
  //     public /p/<slug> share link (no parent listener) report nothing.
  var ERR_CAP = 5;
  var errSeen = {};
  var errCount = 0;

  // Interaction breadcrumbs: a tiny ring of the last few user actions, attached
  // to every error report so the chat card can say "what the user did right
  // before it broke" (and the «Починить» prompt gets that context for free).
  // PRIVACY: we record element IDENTITY (selector + visible label) and the
  // action TYPE only — NEVER a typed value, so a password/email can't leak into
  // the chat. Capture-phase + try/guarded so tracking can never break the page.
  var CRUMB_CAP = 6;
  var crumbs = [];
  function pushCrumb(text) {
    var s = collapse(text, 80);
    if (!s) return;
    if (crumbs.length && crumbs[crumbs.length - 1] === s) return; // dedup repeats
    crumbs.push(s);
    if (crumbs.length > CRUMB_CAP) crumbs.shift();
  }

  function describeTarget(el) {
    if (!el || el.nodeType !== 1) return "";
    var nn = el.nodeName;
    var sel = shortLabel(el);
    var label;
    if (nn === "INPUT" || nn === "TEXTAREA" || nn === "SELECT") {
      // Form fields: static identity only — the typed value never leaves the page.
      label = collapse(
        el.getAttribute("aria-label") ||
          el.getAttribute("placeholder") ||
          el.getAttribute("name") ||
          "",
        40
      );
    } else {
      label = collapse(el.textContent || el.getAttribute("aria-label") || "", 40);
    }
    return label ? sel + " «" + label + "»" : sel;
  }

  // These handlers are attached only by enable() and removed by disable().
  // Normal preview mode therefore has zero inspector click/change listeners.
  function recordClickBreadcrumb(e) {
    try {
      if (e && e.target && e.target.nodeType === 1)
        pushCrumb("клик: " + describeTarget(e.target));
    } catch (_) {}
  }

  function recordChangeBreadcrumb(e) {
    try {
      if (e && e.target && e.target.nodeType === 1)
        pushCrumb("ввод: " + describeTarget(e.target));
    } catch (_) {}
  }

  function reportError(sig, payload) {
    if (errCount >= ERR_CAP || errSeen[sig]) return;
    if (!window.parent || window.parent === window) return; // no workspace shell
    errSeen[sig] = 1;
    errCount++;
    // Breadcrumbs + route injected centrally so both call sites carry them.
    payload.route = (location.pathname || "").slice(0, 300);
    payload.crumbs = crumbs.slice(0, CRUMB_CAP);
    post({ type: "omnia:preview:error", err: payload });
  }

  window.addEventListener(
    "error",
    function (e) {
      // Resource-load failures fire "error" too, but target the failing element
      // (not window) and carry no message — skip them; only script exceptions
      // reach window with a message/Error object.
      if (!e || e.target !== window) return;
      var msg = (e.message || (e.error && e.error.message) || "").toString();
      if (!msg) return;
      var src = (e.filename || "").toString();
      var line = e.lineno || 0;
      var stack = e.error && e.error.stack ? String(e.error.stack).slice(0, 1000) : "";
      reportError(msg + "@" + src + ":" + line, {
        message: msg.slice(0, 300),
        source: src.slice(0, 300),
        line: line,
        col: e.colno || 0,
        stack: stack,
      });
    },
    true
  );

  window.addEventListener("unhandledrejection", function (e) {
    var reason = e && e.reason;
    var msg = "";
    var stack = "";
    if (reason && typeof reason === "object") {
      msg = (reason.message || "").toString();
      stack = reason.stack ? String(reason.stack).slice(0, 1000) : "";
    }
    if (!msg) msg = "Unhandled promise rejection: " + String(reason);
    reportError("reject:" + msg, {
      message: msg.slice(0, 300),
      source: "",
      line: 0,
      col: 0,
      stack: stack,
    });
  });

  // Tell the parent we're ready so it can (re)send enable after a reload while
  // select-mode is still on.
  post({ type: "omnia:inspect:ready", version: 5 });
})();
