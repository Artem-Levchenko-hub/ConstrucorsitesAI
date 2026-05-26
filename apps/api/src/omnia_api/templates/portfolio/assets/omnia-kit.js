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
    var reveals = [].slice.call(document.querySelectorAll(".reveal"));
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
  });
})();
