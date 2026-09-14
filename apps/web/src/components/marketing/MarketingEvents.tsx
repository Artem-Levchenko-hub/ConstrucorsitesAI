"use client";
import { useEffect, useRef } from "react";
type MarketingEvent = { event: string; page: "landing" | "registration"; placement: string };
// Listen to omnia:marketing or connect an existing GTM dataLayer. No cookies,
// third-party scripts, form values or URL parameters are collected here.
export function MarketingEvents({ page }: { page: MarketingEvent["page"] }) {
  const seen = useRef(new Set<string>());
  useEffect(() => {
    const emit = (name: string, placement: string) => {
      const detail: MarketingEvent = { event: `max_${name}`, page, placement };
      window.dispatchEvent(new CustomEvent("omnia:marketing", { detail }));
      try { (window as Window & { dataLayer?: { push: (event: MarketingEvent) => unknown } }).dataLayer?.push(detail); } catch { /* Analytics never blocks navigation. */ }
    };
    if (!seen.current.has(page)) { seen.current.add(page); emit("page_view", page); }
    const click = (event: MouseEvent) => {
      const target = event.target instanceof Element ? event.target.closest<HTMLElement>("[data-marketing]") : null;
      const name = target?.dataset.marketing;
      if (name && ["signup_click", "login_click", "scenario_select", "guide_click"].includes(name)) emit(name, target?.dataset.placement || "unknown");
    };
    const toggle = (event: Event) => { const target = event.target; if (target instanceof HTMLDetailsElement && target.open && target.dataset.faq) emit("faq_open", target.dataset.faq); };
    const observer = typeof IntersectionObserver === "undefined" ? null : new IntersectionObserver(entries => {
      entries.forEach(entry => { const name = (entry.target as HTMLElement).dataset.marketingSection;
        if (entry.isIntersecting && name && !seen.current.has(name)) { seen.current.add(name); emit("section_view", name); }
      });
    }, { threshold: 0.25 });
    document.querySelectorAll("[data-marketing-section]").forEach(el => observer?.observe(el));
    document.addEventListener("click", click); document.addEventListener("toggle", toggle, true);
    return () => { document.removeEventListener("click", click); document.removeEventListener("toggle", toggle, true); observer?.disconnect(); };
  }, [page]);
  return null;
}
