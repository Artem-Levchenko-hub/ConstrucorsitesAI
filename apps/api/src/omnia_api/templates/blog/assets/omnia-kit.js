/*! omnia-kit.js — built-in interactivity for Omnia.AI generated sites.
 *  Managed by Omnia. Do NOT edit per-project: the generator never rewrites it.
 *  Vanilla JS, deferred, idempotent, reduced-motion aware. Every behaviour
 *  silently no-ops when its hook element is absent, so the kit never errors on
 *  a minimal page. Hooks: .reveal[data-reveal-delay], #menu-toggle/#mobile-menu,
 *  .faq-item/.faq-question/.faq-answer, a[href^="#"], #back-to-top,
 *  [data-parallax], [data-count-to], .magnetic, .tilt.
 */
(function () {
  "use strict";
  if (window.__omniaKit) return; // idempotent — never bind twice
  window.__omniaKit = true;

  var root = document.documentElement;
  var reduce = window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var canHover = window.matchMedia && window.matchMedia("(hover: hover)").matches;

  // Enable CSS reveal animations only when motion is OK and JS is alive.
  // Without this class the .reveal start-state never hides content (no FOUC,
  // and a blocked/failed script can never leave the page invisible).
  if (!reduce) root.classList.add("omnia-anim");

  function ready(fn) {
    if (document.readyState !== "loading") fn();
    else document.addEventListener("DOMContentLoaded", fn);
  }

  ready(function () {
    // 1. Scroll reveal
    var reveals = [].slice.call(document.querySelectorAll(".reveal, .line-rise"));
    if (reduce || !("IntersectionObserver" in window)) {
      reveals.forEach(function (el) { el.classList.add("is-visible"); });
    } else {
      var io = new IntersectionObserver(function (entries) {
        entries.forEach(function (e) {
          if (e.isIntersecting) {
            e.target.classList.add("is-visible");
            io.unobserve(e.target);
          }
        });
      }, { rootMargin: "0px 0px -10% 0px", threshold: 0.1 });
      reveals.forEach(function (el) { io.observe(el); });
    }

    // 2. Mobile burger menu (#menu-toggle ↔ #mobile-menu)
    var toggle = document.getElementById("menu-toggle");
    var menu = document.getElementById("mobile-menu");
    if (toggle && menu) {
      var setOpen = function (open) {
        menu.classList.toggle("hidden", !open);
        toggle.setAttribute("aria-expanded", open ? "true" : "false");
      };
      setOpen(!menu.classList.contains("hidden"));
      toggle.addEventListener("click", function () {
        setOpen(menu.classList.contains("hidden"));
      });
      menu.addEventListener("click", function (e) {
        if (e.target.closest("a")) setOpen(false);
      });
      document.addEventListener("keydown", function (e) {
        if (e.key === "Escape") setOpen(false);
      });
    }

    // 3. FAQ accordion
    [].slice.call(document.querySelectorAll(".faq-item .faq-question")).forEach(function (q) {
      q.setAttribute("aria-expanded", "false");
      q.addEventListener("click", function () {
        var item = q.closest(".faq-item");
        if (!item) return;
        var open = item.classList.toggle("is-open");
        q.setAttribute("aria-expanded", open ? "true" : "false");
      });
    });

    // 4. Smooth in-page anchor scrolling
    document.addEventListener("click", function (e) {
      var a = e.target.closest('a[href^="#"]');
      if (!a) return;
      if (window.__omniaLenis) return; // Lenis (anchors:true) owns in-page scroll
      var href = a.getAttribute("href");
      if (!href || href.length < 2) return;
      var target = document.querySelector(href);
      if (!target) return;
      e.preventDefault();
      target.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
    });

    // 5. Back-to-top (#back-to-top)
    var btt = document.getElementById("back-to-top");
    if (btt) {
      var sync = function () { btt.classList.toggle("is-visible", window.scrollY > 600); };
      sync();
      window.addEventListener("scroll", sync, { passive: true });
      btt.addEventListener("click", function () {
        if (window.__omniaLenis) { window.__omniaLenis.scrollTo(0); return; }
        window.scrollTo({ top: 0, behavior: reduce ? "auto" : "smooth" });
      });
    }

    // 6. Parallax — [data-parallax="0.15"] translates Y by a fraction of scroll
    var parallax = [].slice.call(document.querySelectorAll("[data-parallax]"));
    if (!reduce && parallax.length) {
      var ticking = false;
      var updateParallax = function () {
        var vh = window.innerHeight;
        parallax.forEach(function (el) {
          var rect = el.getBoundingClientRect();
          var factor = parseFloat(el.getAttribute("data-parallax")) || 0.15;
          var offset = (rect.top + rect.height / 2 - vh / 2) * -factor;
          el.style.setProperty("--omnia-py", offset.toFixed(1) + "px");
        });
        ticking = false;
      };
      var onScrollP = function () {
        if (!ticking) { ticking = true; requestAnimationFrame(updateParallax); }
      };
      updateParallax();
      window.addEventListener("scroll", onScrollP, { passive: true });
      window.addEventListener("resize", onScrollP, { passive: true });
    }

    // 7. Count-up — <span data-count-to="2400" data-count-suffix="+">
    var counters = [].slice.call(document.querySelectorAll("[data-count-to]"));
    if (counters.length) {
      var fmt = function (n) { return n.toLocaleString("ru-RU"); };
      var runCount = function (el) {
        var target = parseFloat(el.getAttribute("data-count-to")) || 0;
        var suffix = el.getAttribute("data-count-suffix") || "";
        if (reduce) { el.textContent = fmt(target) + suffix; return; }
        var start = null, dur = 1400;
        var step = function (ts) {
          if (!start) start = ts;
          var p = Math.min((ts - start) / dur, 1);
          var eased = 1 - Math.pow(1 - p, 3);
          el.textContent = fmt(Math.round(target * eased)) + suffix;
          if (p < 1) requestAnimationFrame(step);
        };
        requestAnimationFrame(step);
      };
      if (reduce || !("IntersectionObserver" in window)) {
        counters.forEach(runCount);
      } else {
        var cio = new IntersectionObserver(function (entries) {
          entries.forEach(function (e) {
            if (e.isIntersecting) { runCount(e.target); cio.unobserve(e.target); }
          });
        }, { threshold: 0.4 });
        counters.forEach(function (el) { cio.observe(el); });
        // Kick once on next frame: above-fold counters (hero stats block)
        // can be already in viewport at init, and IntersectionObserver
        // doesn't always fire its initial entry synchronously for such
        // elements — esp. when the parent .reveal starts at opacity:0.
        // Without this kick, the span stays literal "0".
        requestAnimationFrame(function () {
          counters.forEach(function (el) {
            var r = el.getBoundingClientRect();
            if (r.top < window.innerHeight * 0.6 && r.bottom > 0) {
              runCount(el);
              cio.unobserve(el);
            }
          });
        });
      }
    }

    // 8. Magnetic + tilt hover (pointer-driven; desktop + motion only)
    if (!reduce && canHover) {
      [].slice.call(document.querySelectorAll(".magnetic")).forEach(function (el) {
        el.addEventListener("pointermove", function (ev) {
          var r = el.getBoundingClientRect();
          el.style.setProperty("--mx", ((ev.clientX - r.left - r.width / 2) * 0.2).toFixed(1) + "px");
          el.style.setProperty("--my", ((ev.clientY - r.top - r.height / 2) * 0.2).toFixed(1) + "px");
        });
        el.addEventListener("pointerleave", function () {
          el.style.setProperty("--mx", "0px"); el.style.setProperty("--my", "0px");
        });
      });
      [].slice.call(document.querySelectorAll(".tilt")).forEach(function (el) {
        el.addEventListener("pointermove", function (ev) {
          var r = el.getBoundingClientRect();
          el.style.setProperty("--ry", (((ev.clientX - r.left) / r.width - 0.5) * 10).toFixed(2) + "deg");
          el.style.setProperty("--rx", (((ev.clientY - r.top) / r.height - 0.5) * -10).toFixed(2) + "deg");
        });
        el.addEventListener("pointerleave", function () {
          el.style.setProperty("--rx", "0deg"); el.style.setProperty("--ry", "0deg");
        });
      });
    }

    // 9. v3.0 Awwwards-tier presets

    // 9a. Kinetic marquee (festival-brutalist): дублирует children внутри
    // .kinetic-marquee-track ×2, чтобы линейная CSS-анимация петлилась бесшовно.
    if (!reduce) {
      [].slice.call(document.querySelectorAll(".kinetic-marquee-track")).forEach(function (track) {
        if (track.dataset.cloned === "1") return;
        var originals = [].slice.call(track.children);
        originals.forEach(function (node) { track.appendChild(node.cloneNode(true)); });
        track.dataset.cloned = "1";
      });
    }

    // 9b. Custom cursor blob (wellness-casual): активируется через body[data-cursor="blob"].
    // Создаёт .cursor-blob-el, следующий за указателем; авто-выключен на touch/reduce.
    if (!reduce && canHover && document.body && document.body.dataset.cursor === "blob") {
      var blob = document.createElement("div");
      blob.className = "cursor-blob-el";
      blob.setAttribute("aria-hidden", "true");
      document.body.appendChild(blob);
      var blobX = window.innerWidth / 2;
      var blobY = window.innerHeight / 2;
      var targetX = blobX;
      var targetY = blobY;
      document.addEventListener("pointermove", function (ev) {
        targetX = ev.clientX;
        targetY = ev.clientY;
      }, { passive: true });
      var animateBlob = function () {
        blobX += (targetX - blobX) * 0.18;
        blobY += (targetY - blobY) * 0.18;
        blob.style.transform = "translate(" + blobX.toFixed(1) + "px," + blobY.toFixed(1) + "px) translate(-50%,-50%)";
        requestAnimationFrame(animateBlob);
      };
      requestAnimationFrame(animateBlob);
      var enlargeSel = "a,button,[role=button],input,textarea,select,.hover-lift,.magnetic";
      document.addEventListener("pointerover", function (ev) {
        if (ev.target && ev.target.closest && ev.target.closest(enlargeSel)) {
          blob.style.width = "56px"; blob.style.height = "56px";
        }
      });
      document.addEventListener("pointerout", function (ev) {
        if (ev.target && ev.target.closest && ev.target.closest(enlargeSel)) {
          blob.style.width = "28px"; blob.style.height = "28px";
        }
      });
    }

    // ─── Omnia-kit v3 — Phase C additions ─────────────────────────────
    //
    // 10. Scroll-driven IO fallback for .scroll-fade-up / .scroll-scale-in /
    //     .scroll-clip-reveal. Native CSS scroll-timeline does the work on
    //     Chrome 115+ / Safari 17.5+; we still flip the class on older
    //     browsers so the same markup degrades cleanly. Idempotent — both
    //     paths reach the same final state.
    if (!reduce && "IntersectionObserver" in window) {
      var scrollSel = ".scroll-fade-up,.scroll-scale-in,.scroll-clip-reveal";
      var scrollEls = [].slice.call(document.querySelectorAll(scrollSel));
      if (scrollEls.length) {
        var scrollIO = new IntersectionObserver(function (entries) {
          entries.forEach(function (e) {
            if (e.isIntersecting) {
              e.target.classList.add("scroll-visible");
              scrollIO.unobserve(e.target);
            }
          });
        }, { threshold: 0.18, rootMargin: "0px 0px -8% 0px" });
        scrollEls.forEach(function (el) {
          scrollIO.observe(el);
          // kick if already in viewport at init (same gotcha as count-up)
          var r = el.getBoundingClientRect();
          if (r.top < window.innerHeight * 0.7 && r.bottom > 0) {
            el.classList.add("scroll-visible");
            scrollIO.unobserve(el);
          }
        });
      }
    }

    // 11. Parallax — write `--scroll-y` on <html> once per RAF so
    //     .scroll-parallax / .parallax-layer-N can compute transform purely
    //     in CSS. Costs ~1 layout-property write per scroll frame.
    if (!reduce) {
      var parallaxEls = document.querySelectorAll(".scroll-parallax,.parallax-layer-1,.parallax-layer-2,.parallax-layer-3");
      if (parallaxEls.length) {
        var paTicking = false;
        var paUpdate = function () {
          root.style.setProperty("--scroll-y", window.scrollY + "px");
          paTicking = false;
        };
        var paOnScroll = function () {
          if (!paTicking) { paTicking = true; requestAnimationFrame(paUpdate); }
        };
        paUpdate();
        window.addEventListener("scroll", paOnScroll, { passive: true });
        window.addEventListener("resize", paOnScroll, { passive: true });
      }
    }

    // 12. Split-chars walker — wraps each non-space char of any
    //     `.split-chars` element in `<span style="--i:N">CHAR</span>` so the
    //     CSS keyframe `omnia-split-in` staggers them on entry. Idempotent
    //     via `data-omnia-split` marker.
    if (!reduce) {
      [].slice.call(document.querySelectorAll(".split-chars")).forEach(function (el) {
        if (el.getAttribute("data-omnia-split") === "1") return;
        var text = el.textContent || "";
        var html = "";
        var i = 0;
        for (var k = 0; k < text.length; k++) {
          var ch = text.charAt(k);
          if (ch === " " || ch === " ") { html += " "; continue; }
          html += '<span style="--i:' + i + '">' + (ch === "<" ? "&lt;" : ch === "&" ? "&amp;" : ch) + "</span>";
          i++;
        }
        el.innerHTML = html;
        el.setAttribute("data-omnia-split", "1");
      });
    }

    // 13. Cursor trail — single lagging dot. Skipped on touch / no-hover
    //     devices and when reduced-motion is on. Picks an existing
    //     `.cursor-trail` element (auto-created if absent) and updates its
    //     translate per pointermove via RAF.
    if (!reduce && canHover) {
      var trail = document.querySelector(".cursor-trail");
      if (!trail) {
        trail = document.createElement("div");
        trail.className = "cursor-trail";
        document.body.appendChild(trail);
      }
      var trailX = -100, trailY = -100, targetX = -100, targetY = -100, trailOn = false;
      document.addEventListener("pointermove", function (ev) {
        targetX = ev.clientX; targetY = ev.clientY;
        if (!trailOn) { trailOn = true; trail.classList.add("is-on"); }
      }, { passive: true });
      document.addEventListener("pointerleave", function () {
        trailOn = false; trail.classList.remove("is-on");
      });
      var trailFrame = function () {
        trailX += (targetX - trailX) * 0.22;
        trailY += (targetY - trailY) * 0.22;
        trail.style.transform = "translate3d(" + trailX.toFixed(1) + "px," + trailY.toFixed(1) + "px,0)";
        requestAnimationFrame(trailFrame);
      };
      requestAnimationFrame(trailFrame);

      // 14. Cursor-context — set body[data-cursor] over links/inputs/text
      //     so .cursor-blob-el can re-shape via CSS rules added in v3.
      var contextOver = function (ev) {
        var t = ev.target;
        if (!t || !t.closest) return;
        if (t.closest("a,button,[role=button]")) { document.body.setAttribute("data-cursor", "link"); }
        else if (t.closest("input,textarea,[contenteditable=true]")) { document.body.setAttribute("data-cursor", "text"); }
      };
      var contextOut = function () { document.body.removeAttribute("data-cursor"); };
      document.addEventListener("pointerover", contextOver, { passive: true });
      document.addEventListener("pointerout", contextOut, { passive: true });
    }

    // ───────────────────────────────────────────────────────────────
    // 15. anime.js-powered accents (Phase L10). Drives the data-anime
    //     hooks through anime.js (window.anime, vendored at
    //     assets/anime.min.js) for richer stagger / easing / number
    //     tweens. Progressive enhancement: if anime.min.js failed to
    //     load OR reduced-motion is on, we NEVER hide anything — content
    //     stays visible exactly as rendered (the markup is the floor).
    //     An element is hidden only in the same synchronous step that
    //     animates it, so a missing/blocked lib can't strand content.
    //     Idempotent per element via data-omnia-bound; re-runnable via
    //     window.__omniaKitScan() (the streaming preview calls it after
    //     each DOM patch). Vocabulary, set by templates / freeform HTML:
    //       data-anime="hero-stagger"    split headline, stagger words in
    //       data-anime="reveal-stagger"  cascade direct children on scroll
    //       data-anime="fade-up"         fade+rise one element on scroll
    //       data-anime="count-up" | [data-anime-counter]   0->N tween
    //       [data-anime-magnetic]        CTA follows pointer (desktop)
    //     Anti-slop caps: <=8 animated elements/page, one hero-stagger.
    // ───────────────────────────────────────────────────────────────
    var animeOK = typeof window.anime === "function" && !reduce;
    var ANIME_MAX = 8;        // hard ceiling on data-anime elements per page
    var animeBound = 0;       // bound so far (persists across re-scans)
    var heroStaggerDone = false;

    function omniaObserveOnce(el, cb, opts) {
      // Run cb() once when el enters the viewport (or immediately if it is
      // already in view — IO doesn't always fire synchronously for above-fold
      // nodes). No IO support → run now.
      if (!("IntersectionObserver" in window)) { cb(); return; }
      var io = new IntersectionObserver(function (entries) {
        for (var n = 0; n < entries.length; n++) {
          if (entries[n].isIntersecting) { io.disconnect(); cb(); return; }
        }
      }, opts || { threshold: 0.2, rootMargin: "0px 0px -8% 0px" });
      io.observe(el);
      var r = el.getBoundingClientRect();
      if (r.top < window.innerHeight * 0.85 && r.bottom > 0) { io.disconnect(); cb(); }
    }

    function omniaSplitWords(el, mode) {
      // Split el's text into inline-block spans. If el already has element
      // children (e.g. a gradient <span> inside the headline) we DON'T
      // destroy them — animate el as one unit instead. Returns stagger targets.
      for (var n = 0; n < el.childNodes.length; n++) {
        if (el.childNodes[n].nodeType === 1) return [el];
      }
      var text = el.textContent || "";
      el.textContent = "";
      var frag = document.createDocumentFragment();
      var targets = [];
      var parts = mode === "letter" ? text.split("") : text.split(/(\s+)/);
      parts.forEach(function (p) {
        if (p === "") return;
        var span = document.createElement("span");
        span.textContent = p;
        span.style.display = "inline-block";
        if (/^\s+$/.test(p)) span.style.whiteSpace = "pre";
        else targets.push(span);
        frag.appendChild(span);
      });
      el.appendChild(frag);
      return targets;
    }

    function omniaHeroStagger(el) {
      if (heroStaggerDone) return;          // one signature hero per page
      heroStaggerDone = true;
      var mode = el.dataset.animeSplit === "letter" ? "letter" : "word";
      var targets = omniaSplitWords(el, mode);
      var per = parseInt(el.dataset.animeStagger, 10);
      if (isNaN(per)) per = targets.length > 1
        ? Math.min(60, Math.max(16, Math.round(900 / targets.length))) : 0;
      window.anime({
        targets: targets,
        opacity: [0, 1],
        translateY: ["0.5em", "0em"],
        duration: 780,
        delay: window.anime.stagger(per),
        easing: "easeOutQuint",
      });
    }

    function omniaRevealStagger(el) {
      var kids = [].slice.call(el.children);
      if (!kids.length) return;
      kids.forEach(function (k) { k.style.opacity = "0"; });
      omniaObserveOnce(el, function () {
        window.anime({
          targets: kids,
          opacity: [0, 1],
          translateY: [26, 0],
          duration: 720,
          delay: window.anime.stagger(Math.min(110, Math.max(40, 700 / kids.length))),
          easing: "easeOutCubic",
        });
      });
    }

    function omniaFadeUp(el) {
      el.style.opacity = "0";
      omniaObserveOnce(el, function () {
        window.anime({
          targets: el, opacity: [0, 1], translateY: [24, 0],
          duration: 760, easing: "easeOutQuint",
        });
      });
    }

    function omniaCountUp(el) {
      var raw = (el.textContent || "0").trim();
      var m = raw.match(/^(\D*)([\d\s.,]+)(\D*)$/);
      if (!m) return;
      var prefix = m[1] || "", suffix = m[3] || "";
      var target = parseFloat(m[2].replace(/\s/g, "").replace(",", "."));
      if (isNaN(target)) return;
      var decimals = target % 1 === 0 ? 0 : 1;
      var obj = { v: 0 };
      omniaObserveOnce(el, function () {
        window.anime({
          targets: obj, v: target, duration: 1500, easing: "easeOutExpo",
          update: function () {
            el.textContent = prefix + (target >= 1000
              ? Math.round(obj.v).toLocaleString("ru-RU")
              : obj.v.toFixed(decimals)) + suffix;
          },
        });
      }, { threshold: 0.4 });
    }

    function omniaMagnetic(el) {
      if (!canHover) return;
      var radius = parseInt(el.dataset.animeMagneticRadius, 10) || 80;
      var strength = parseFloat(el.dataset.animeMagneticStrength) || 0.35;
      var rect = null;
      el.style.transition = "transform .3s cubic-bezier(.22,1,.36,1)";
      document.addEventListener("pointermove", function (ev) {
        if (!rect) rect = el.getBoundingClientRect();
        var dx = ev.clientX - (rect.left + rect.width / 2);
        var dy = ev.clientY - (rect.top + rect.height / 2);
        if (Math.sqrt(dx * dx + dy * dy) > radius) { el.style.transform = ""; return; }
        el.style.transform = "translate(" + (dx * strength).toFixed(1) + "px," +
          (dy * strength).toFixed(1) + "px)";
      }, { passive: true });
      el.addEventListener("pointerleave", function () { el.style.transform = ""; rect = null; });
      window.addEventListener("resize", function () { rect = null; }, { passive: true });
    }

    function omniaScanAnime(scope) {
      scope = scope || document;
      var els = [].slice.call(scope.querySelectorAll(
        '[data-anime],[data-anime-counter],[data-anime-magnetic],.anime-hero-reveal'
      ));
      for (var i = 0; i < els.length; i++) {
        var el = els[i];
        if (el.getAttribute("data-omnia-bound") === "1") continue;
        el.setAttribute("data-omnia-bound", "1");
        if (animeBound >= ANIME_MAX) continue;   // cap → element stays at floor
        var kind = el.getAttribute("data-anime") || "";
        // Magnetic is pure transform — works without anime.js (motion only).
        if (el.hasAttribute("data-anime-magnetic")) {
          if (!reduce) { omniaMagnetic(el); animeBound++; }
          continue;
        }
        if (!animeOK) continue;   // floor: leave content visible, do not bind
        if (kind === "hero-stagger" || el.classList.contains("anime-hero-reveal")) {
          omniaHeroStagger(el); animeBound++;
        } else if (kind === "reveal-stagger") {
          omniaRevealStagger(el); animeBound++;
        } else if (kind === "count-up" || el.hasAttribute("data-anime-counter")) {
          omniaCountUp(el); animeBound++;
        } else if (kind === "fade-up") {
          omniaFadeUp(el); animeBound++;
        }
      }
    }

    window.__omniaKitScan = function () { try { omniaScanAnime(document); } catch (e) {} };
    omniaScanAnime(document);

    // ─── Omnia-kit v4 — «Живой» слой: shader / lenis / scramble / pointer ─────
    //     Self-bootstrapping deps (Lenis via CDN; shader is inline WebGL, zero
    //     external HTTP). Every feature floors to plain markup: blocked CDN /
    //     no-WebGL / reduced-motion never strand content. Idempotent via
    //     per-element data-*-bound markers.

    function omniaLoadScript(src, cb) {
      var ex = document.querySelector('script[data-omnia-lib="' + src + '"]');
      if (ex) {
        if (ex.getAttribute("data-loaded") === "1") { if (cb) cb(); }
        else ex.addEventListener("load", function () { if (cb) cb(); });
        return;
      }
      var s = document.createElement("script");
      s.src = src; s.async = true; s.setAttribute("data-omnia-lib", src);
      s.addEventListener("load", function () { s.setAttribute("data-loaded", "1"); if (cb) cb(); });
      s.addEventListener("error", function () {}); // floor: silently skip
      document.head.appendChild(s);
    }

    // 16. Lenis inertial smooth-scroll. Floor = native scroll. Reduce → skip.
    if (!reduce && !window.__omniaLenis) {
      omniaLoadScript("https://unpkg.com/lenis@1.3.23/dist/lenis.min.js", function () {
        if (!window.Lenis || window.__omniaLenis) return;
        try {
          var lenis = new window.Lenis({ lerp: 0.1, smoothWheel: true, anchors: true });
          window.__omniaLenis = lenis;
          var lraf = function (t) { lenis.raf(t); requestAnimationFrame(lraf); };
          requestAnimationFrame(lraf);
        } catch (e) {}
      });
    }

    // 17. WebGL shader atmosphere — [data-omnia-shader]. Self-contained flowing
    //     mesh-gradient; colors from data-omnia-colors (comma hex) or CSS
    //     --sh1..--sh4. reduce/no-WebGL → CSS floor stays. IO-paused offscreen.
    //     [data-omnia-pointer]/.omnia-pointer host → cursor warps the field.
    function omniaHexToRGB(hex) {
      hex = (hex || "").trim().replace("#", "");
      if (hex.length === 3) hex = hex.charAt(0) + hex.charAt(0) + hex.charAt(1) + hex.charAt(1) + hex.charAt(2) + hex.charAt(2);
      if (hex.length !== 6) return null;
      var n = parseInt(hex, 16);
      if (isNaN(n)) return null;
      return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
    }
    function omniaShaderColors(host) {
      var out = [];
      var attr = host.getAttribute("data-omnia-colors");
      if (attr) attr.split(",").forEach(function (h) { var c = omniaHexToRGB(h); if (c) out.push(c); });
      if (out.length < 2) {
        var cs = getComputedStyle(host);
        ["--sh1", "--sh2", "--sh3", "--sh4"].forEach(function (v) {
          var c = omniaHexToRGB(cs.getPropertyValue(v)); if (c) out.push(c);
        });
      }
      while (out.length < 4) out.push(out[out.length - 1] || [0.04, 0.05, 0.1]);
      return out.slice(0, 4);
    }
    function omniaMountShader(host) {
      if (host.getAttribute("data-omnia-shader-bound") === "1") return;
      host.setAttribute("data-omnia-shader-bound", "1");
      var canvas = document.createElement("canvas");
      canvas.setAttribute("aria-hidden", "true");
      var gl = canvas.getContext("webgl") || canvas.getContext("experimental-webgl");
      if (!gl) return; // floor: CSS background stays
      var FS = [
        "#ifdef GL_FRAGMENT_PRECISION_HIGH",
        "precision highp float;",
        "#else",
        "precision mediump float;",
        "#endif",
        "uniform float u_time;uniform vec2 u_res;uniform vec2 u_mouse;",
        "uniform vec3 u_c1,u_c2,u_c3,u_c4;",
        "float hash(vec2 p){return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453);}",
        "float noise(vec2 p){vec2 i=floor(p),f=fract(p);float a=hash(i),b=hash(i+vec2(1.,0.)),c=hash(i+vec2(0.,1.)),d=hash(i+vec2(1.,1.));vec2 u=f*f*(3.-2.*f);return mix(mix(a,b,u.x),mix(c,d,u.x),u.y);}",
        "float fbm(vec2 p){float v=0.,a=.5;for(int i=0;i<4;i++){v+=a*noise(p);p=p*2.+vec2(1.7,9.2);a*=.5;}return v;}",
        "void main(){vec2 uv=gl_FragCoord.xy/u_res.xy;float asp=u_res.x/u_res.y;vec2 p=vec2(uv.x*asp,uv.y)*1.3;float t=u_time*0.15;vec2 mo=(u_mouse-0.5)*0.6;vec2 q=vec2(fbm(p+mo+t),fbm(p+vec2(5.2,1.3)-t*0.8));float f=fbm(p+1.7*q+t*0.5);vec3 col=mix(u_c1,u_c2,smoothstep(0.05,0.65,f));col=mix(col,u_c3,smoothstep(0.35,0.95,f+q.x*0.5));col=mix(col,u_c4,clamp(length(q)*0.9,0.,1.));col*=1.0-0.16*length(uv-0.5);float g=hash(uv*u_res.xy+t)*0.035-0.0175;gl_FragColor=vec4(col+g,1.0);}"
      ].join("\n");
      var VS = "attribute vec2 p;void main(){gl_Position=vec4(p,0.0,1.0);}";
      function compile(type, src) { var s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s); return s; }
      var prog = gl.createProgram();
      gl.attachShader(prog, compile(gl.VERTEX_SHADER, VS));
      gl.attachShader(prog, compile(gl.FRAGMENT_SHADER, FS));
      gl.linkProgram(prog);
      if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) return; // floor
      gl.useProgram(prog);
      host.appendChild(canvas);
      var buf = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, buf);
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
      var loc = gl.getAttribLocation(prog, "p");
      gl.enableVertexAttribArray(loc);
      gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
      var uTime = gl.getUniformLocation(prog, "u_time");
      var uRes = gl.getUniformLocation(prog, "u_res");
      var uMouse = gl.getUniformLocation(prog, "u_mouse");
      var cols = omniaShaderColors(host);
      ["u_c1", "u_c2", "u_c3", "u_c4"].forEach(function (nm, i) {
        gl.uniform3fv(gl.getUniformLocation(prog, nm), cols[i]);
      });
      var dpr = Math.min(window.devicePixelRatio || 1, 1.75);
      function resize() {
        var w = host.clientWidth || 1, h = host.clientHeight || 1;
        canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
        gl.viewport(0, 0, canvas.width, canvas.height);
      }
      resize();
      window.addEventListener("resize", resize, { passive: true });
      var mq = [0.5, 0.5], mo = [0.5, 0.5];
      if (host.hasAttribute("data-omnia-pointer") || host.classList.contains("omnia-pointer")) {
        host.addEventListener("pointermove", function (e) {
          var r = host.getBoundingClientRect();
          mq = [(e.clientX - r.left) / r.width, 1 - (e.clientY - r.top) / r.height];
        }, { passive: true });
      }
      var speed = parseFloat(host.getAttribute("data-omnia-speed"));
      if (isNaN(speed)) speed = 1;
      var start = 0, raf = 0, live = false;
      function frame(ts) {
        if (!start) start = ts;
        mo[0] += (mq[0] - mo[0]) * 0.06; mo[1] += (mq[1] - mo[1]) * 0.06;
        gl.uniform1f(uTime, ((ts - start) / 1000) * speed);
        gl.uniform2f(uRes, canvas.width, canvas.height);
        gl.uniform2f(uMouse, mo[0], mo[1]);
        gl.drawArrays(gl.TRIANGLES, 0, 3);
        if (!live) { live = true; canvas.classList.add("is-live"); }
        raf = requestAnimationFrame(frame);
      }
      if ("IntersectionObserver" in window) {
        new IntersectionObserver(function (es) {
          es.forEach(function (e) {
            if (e.isIntersecting) { if (!raf) raf = requestAnimationFrame(frame); }
            else if (raf) { cancelAnimationFrame(raf); raf = 0; }
          });
        }, { threshold: 0 }).observe(host);
      } else {
        raf = requestAnimationFrame(frame);
      }
    }
    if (!reduce && "WebGLRenderingContext" in window) {
      [].slice.call(document.querySelectorAll("[data-omnia-shader]")).forEach(omniaMountShader);
    }

    // 18. Headline scramble — [data-omnia-scramble] decodes text on entry.
    //     Skipped if the element has child elements (don't nuke gradient spans).
    if (!reduce) {
      [].slice.call(document.querySelectorAll("[data-omnia-scramble]")).forEach(function (el) {
        if (el.getAttribute("data-omnia-scramble-bound") === "1") return;
        for (var n = 0; n < el.childNodes.length; n++) {
          if (el.childNodes[n].nodeType === 1) return; // has element child → leave as-is
        }
        el.setAttribute("data-omnia-scramble-bound", "1");
        var finalText = el.textContent || "";
        var glyphs = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789#%&@";
        omniaObserveOnce(el, function () {
          var len = finalText.length, reveal = 0, steps = Math.max(18, Math.min(54, len));
          var tick = function () {
            var out = "";
            for (var i = 0; i < len; i++) {
              var ch = finalText.charAt(i);
              if (ch === " " || i < Math.floor(reveal)) out += ch;
              else out += glyphs.charAt(Math.floor(Math.random() * glyphs.length));
            }
            el.textContent = out;
            reveal += len / steps;
            if (reveal < len) requestAnimationFrame(tick);
            else el.textContent = finalText;
          };
          requestAnimationFrame(tick);
        });
      });
    }

    // 19. Pointer-reactive — [data-omnia-pointer] writes --mx/--my (-1..1);
    //     [data-omnia-spotlight] writes --spot-x/--spot-y (%). Desktop + motion.
    if (!reduce && canHover) {
      [].slice.call(document.querySelectorAll("[data-omnia-pointer],.omnia-pointer")).forEach(function (el) {
        if (el.getAttribute("data-omnia-pointer-bound") === "1") return;
        el.setAttribute("data-omnia-pointer-bound", "1");
        el.addEventListener("pointermove", function (e) {
          var r = el.getBoundingClientRect();
          el.style.setProperty("--mx", (((e.clientX - r.left) / r.width) * 2 - 1).toFixed(3));
          el.style.setProperty("--my", (((e.clientY - r.top) / r.height) * 2 - 1).toFixed(3));
        }, { passive: true });
        el.addEventListener("pointerleave", function () {
          el.style.setProperty("--mx", "0"); el.style.setProperty("--my", "0");
        });
      });
      [].slice.call(document.querySelectorAll("[data-omnia-spotlight],.omnia-spotlight")).forEach(function (el) {
        if (el.getAttribute("data-omnia-spot-bound") === "1") return;
        el.setAttribute("data-omnia-spot-bound", "1");
        el.addEventListener("pointermove", function (e) {
          var r = el.getBoundingClientRect();
          el.style.setProperty("--spot-x", (((e.clientX - r.left) / r.width) * 100).toFixed(1) + "%");
          el.style.setProperty("--spot-y", (((e.clientY - r.top) / r.height) * 100).toFixed(1) + "%");
        }, { passive: true });
      });
    }
  });
})();
