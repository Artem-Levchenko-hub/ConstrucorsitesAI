"use client";

import Link from "next/link";
import { useEffect, useRef, type RefObject } from "react";
import { MessageCircle, PanelsTopLeft, SlidersHorizontal } from "lucide-react";
import { bindMaxMobileSwipes } from "./max-mobile-swipe";
import "./max-mobile.css";

export function MaxMobileNav({ projectId, active, onPreview, previewDialog, onClosePreview }: {
  projectId: string;
  active: "chat" | "manage";
  onPreview?: (target: HTMLElement) => void;
  previewDialog?: RefObject<HTMLDivElement | null>;
  onClosePreview?: () => void;
}) {
  const base = `/max/${projectId}`;
  const nav = useRef<HTMLElement>(null);
  useEffect(() => {
    if (nav.current) return bindMaxMobileSwipes(nav.current, active, previewDialog, onClosePreview);
  }, [active, previewDialog, onClosePreview, projectId]);
  return <nav ref={nav} className="max-mobile-nav" aria-label="Основные разделы">
    <Link href={base} draggable={false} aria-current={active === "chat" ? "page" : undefined}>
      <MessageCircle aria-hidden="true" /><span>Чат</span>
    </Link>
    {onPreview
      ? <button type="button" data-max-tab="preview" onClick={event => onPreview(event.currentTarget)} aria-haspopup="dialog">
        <PanelsTopLeft aria-hidden="true" /><span>Превью</span>
      </button>
      : <Link data-max-tab="preview" href={`${base}?preview=1`} draggable={false}><PanelsTopLeft aria-hidden="true" /><span>Превью</span></Link>}
    <Link data-max-tab="manage" href={`${base}/manage`} draggable={false} aria-current={active === "manage" ? "page" : undefined}>
      <SlidersHorizontal aria-hidden="true" /><span>Управление</span>
    </Link>
  </nav>;
}
