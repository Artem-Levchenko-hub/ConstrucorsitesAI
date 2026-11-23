"use client";

import Link from "next/link";
import { useEffect, useRef, type RefObject } from "react";
import { MessageCircle, PanelsTopLeft, SlidersHorizontal } from "lucide-react";
import { bindMaxMobileSwipes } from "./max-mobile-swipe";
import "./max-mobile.css";

export function MaxMobileNav({ projectId, active, onPreview, previewDialog, onClosePreview }: {
  projectId: string;
  active: "chat" | "preview" | "manage";
  onPreview?: (target: HTMLElement) => void;
  previewDialog?: RefObject<HTMLDivElement | null>;
  onClosePreview?: () => void;
}) {
  const base = `/max/${projectId}`;
  const nav = useRef<HTMLElement>(null);
  useEffect(() => {
    if (nav.current && active !== "preview") return bindMaxMobileSwipes(nav.current, active, previewDialog, onClosePreview);
  }, [active, previewDialog, onClosePreview, projectId]);
  const chatLabel = <><MessageCircle aria-hidden="true" /><span>Чат</span></>;
  const previewLabel = <><PanelsTopLeft aria-hidden="true" /><span>Превью</span></>;
  return <nav ref={nav} className="max-mobile-nav" aria-label="Основные разделы">
    {active === "preview"
      ? <button type="button" data-max-tab="chat" onClick={onClosePreview}>{chatLabel}</button>
      : <Link href={base} draggable={false} aria-current={active === "chat" ? "page" : undefined}>{chatLabel}</Link>}
    {!onPreview && active !== "preview"
      ? <Link data-max-tab="preview" href={`${base}?preview=1`} draggable={false}>{previewLabel}</Link>
      : <button type="button" data-max-tab="preview" onClick={onPreview && (event => onPreview(event.currentTarget))}
          aria-haspopup={onPreview ? "dialog" : undefined} aria-current={active === "preview" ? "page" : undefined}>{previewLabel}</button>}
    <Link data-max-tab="manage" href={`${base}/manage`} draggable={false} aria-current={active === "manage" ? "page" : undefined}>
      <SlidersHorizontal aria-hidden="true" /><span>Управление</span>
    </Link>
  </nav>;
}
