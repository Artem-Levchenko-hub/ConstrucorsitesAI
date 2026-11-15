"use client";

import { useEffect, useRef, useState, type MouseEvent, type ReactNode } from "react";

import { guideChapters, type GuideLessonId } from "@/lib/guide-lessons";

function ChapterLinks({
  active, onNavigate,
}: {
  active: GuideLessonId | undefined;
  onNavigate: (chapter: GuideLessonId) => void;
}) {
  function select(event: MouseEvent<HTMLAnchorElement>, chapter: GuideLessonId) {
    if (
      !event.defaultPrevented && event.button === 0 &&
      !event.altKey && !event.ctrlKey && !event.metaKey && !event.shiftKey
    ) {
      onNavigate(chapter);
    }
  }

  return (
    <div className="guide-chapter-links">
      {guideChapters.map(({ id, title, number }) => (
        <a key={id} href={`#${id}`} aria-current={id === active ? "location" : undefined}
          onClick={event => select(event, id)}>
          <span className="guide-chapter-link-number w-6 shrink-0 font-mono text-[10px]">{number}</span>
          {title}
        </a>
      ))}
    </div>
  );
}

export function GuideNavigation({ children }: { children: ReactNode }) {
  const [active, setActive] = useState<GuideLessonId>();
  const desktopList = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const sections = guideChapters.flatMap(({ id }) => {
      const element = document.getElementById(id);
      return element ? [{ id, element }] : [];
    });
    const first = sections[0];
    if (!first) return;

    let frame: number | undefined;
    function measure() {
      const offset = Number.parseFloat(getComputedStyle(first.element).scrollMarginTop) || 0;
      const last = sections.at(-1);
      const pageHeight = document.documentElement.scrollHeight;
      if (pageHeight > window.innerHeight && window.scrollY + window.innerHeight >= pageHeight - 1) {
        setActive(last?.id);
        return;
      }
      setActive(sections.findLast(({ element }) => (
        element.getBoundingClientRect().top <= offset + 1
      ))?.id ?? first.id);
    }

    function scheduleMeasurement() {
      if (frame !== undefined) return;
      frame = window.requestAnimationFrame(() => {
        frame = undefined;
        measure();
      });
    }

    function initialChapter() {
      let fragment: string;
      try {
        fragment = decodeURIComponent(window.location.hash.slice(1));
      } catch {
        return undefined;
      }
      const target = document.getElementById(fragment)?.closest(".guide-chapter");
      return sections.find(({ element }) => element === target)?.id;
    }

    const initialFrame = window.requestAnimationFrame(() => {
      const chapter = frame === undefined ? initialChapter() : undefined;
      if (chapter) setActive(chapter);
      scheduleMeasurement();
    });
    // History may restore a reading position that differs from the original fragment.
    window.addEventListener("scroll", scheduleMeasurement, { passive: true });
    window.addEventListener("resize", scheduleMeasurement);
    window.addEventListener("hashchange", scheduleMeasurement);
    window.addEventListener("popstate", scheduleMeasurement);
    window.addEventListener("pageshow", scheduleMeasurement);
    return () => {
      window.cancelAnimationFrame(initialFrame);
      if (frame !== undefined) window.cancelAnimationFrame(frame);
      window.removeEventListener("scroll", scheduleMeasurement);
      window.removeEventListener("resize", scheduleMeasurement);
      window.removeEventListener("hashchange", scheduleMeasurement);
      window.removeEventListener("popstate", scheduleMeasurement);
      window.removeEventListener("pageshow", scheduleMeasurement);
    };
  }, []);

  useEffect(() => {
    const list = desktopList.current;
    const link = list?.querySelector('[aria-current="location"]');
    if (!list || !link || list.contains(document.activeElement)) return;
    if (!list.clientHeight || list.scrollHeight <= list.clientHeight) return;
    const listBounds = list.getBoundingClientRect();
    const linkBounds = link.getBoundingClientRect();
    // Keep the indicator visible in the sidebar without scrolling the document.
    if (linkBounds.top < listBounds.top) list.scrollTop += linkBounds.top - listBounds.top;
    else if (linkBounds.bottom > listBounds.bottom) {
      list.scrollTop += linkBounds.bottom - listBounds.bottom;
    }
  }, [active]);

  return (
    <>
      <aside className="hidden lg:block">
        <nav aria-label="Оглавление" className="guide-desktop-toc">
          <p className="omnia-kicker px-3 text-[#828491]">Оглавление</p>
          <div ref={desktopList} className="guide-desktop-toc-links" role="group"
            aria-label="Главы руководства" tabIndex={0}>
            <ChapterLinks active={active} onNavigate={setActive} />
          </div>
        </nav>
      </aside>

      <div className="min-w-0">
        <details className="mt-6 rounded-xl border border-[#2b2d32] bg-[#191b20] p-4 lg:hidden">
          <summary className="cursor-pointer text-sm font-semibold">Оглавление руководства</summary>
          <nav aria-label="Главы руководства" className="mt-3">
            <ChapterLinks active={active} onNavigate={setActive} />
          </nav>
        </details>
        {children}
      </div>
    </>
  );
}
