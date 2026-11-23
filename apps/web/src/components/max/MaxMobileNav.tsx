"use client";

import Link from "next/link";
import { MessageCircle, PanelsTopLeft, SlidersHorizontal } from "lucide-react";
import "./max-mobile.css";

export function MaxMobileNav({ projectId, active, onPreview }: {
  projectId: string;
  active: "chat" | "manage";
  onPreview?: (target: HTMLElement) => void;
}) {
  const base = `/max/${projectId}`;
  return <nav className="max-mobile-nav" aria-label="Основные разделы">
    <Link href={base} aria-current={active === "chat" ? "page" : undefined}>
      <MessageCircle aria-hidden="true" /><span>Чат</span>
    </Link>
    {onPreview
      ? <button type="button" onClick={event => onPreview(event.currentTarget)} aria-haspopup="dialog">
        <PanelsTopLeft aria-hidden="true" /><span>Превью</span>
      </button>
      : <Link href={`${base}?preview=1`}><PanelsTopLeft aria-hidden="true" /><span>Превью</span></Link>}
    <Link href={`${base}/manage`} aria-current={active === "manage" ? "page" : undefined}>
      <SlidersHorizontal aria-hidden="true" /><span>Управление</span>
    </Link>
  </nav>;
}
