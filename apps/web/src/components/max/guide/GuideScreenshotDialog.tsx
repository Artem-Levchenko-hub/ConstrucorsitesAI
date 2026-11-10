"use client";

import { useRef, useState, type ReactNode } from "react";
import { Maximize2 } from "lucide-react";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";

export function GuideScreenshotDialog({ title, src, children }: {
  title: string;
  src: string;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLAnchorElement>(null);

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <a
        ref={trigger}
        className="guide-screenshot__original"
        href={src}
        target="_blank"
        rel="noopener noreferrer"
        aria-label={`Открыть в полном размере: ${title}`}
        aria-haspopup="dialog"
        onClick={event => {
          if (event.button !== 0 || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
          event.preventDefault();
          setOpen(true);
        }}
      >
        Открыть в полном размере <Maximize2 aria-hidden="true" className="size-3.5 shrink-0" />
      </a>
      <DialogContent
        data-max-studio
        className="guide-screenshot-dialog"
        overlayClassName="guide-screenshot-dialog__backdrop"
        aria-describedby={undefined}
        onCloseAutoFocus={event => {
          event.preventDefault();
          trigger.current?.focus({ preventScroll: true });
        }}
      >
        <DialogTitle className="guide-screenshot-dialog__title">{title}</DialogTitle>
        <div className="guide-screenshot-dialog__stage" role="region" aria-label="Снимок экрана" tabIndex={0}>
          {children}
        </div>
      </DialogContent>
    </Dialog>
  );
}
