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
  });
})();
