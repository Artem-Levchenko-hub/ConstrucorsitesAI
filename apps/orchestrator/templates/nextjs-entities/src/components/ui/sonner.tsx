"use client";

import * as React from "react";
import { Toaster as Sonner, type ToasterProps } from "sonner";

/**
 * Toast host. Mounted once in app/layout.tsx. Trigger toasts anywhere with
 * `import { toast } from "sonner"` → `toast.success("Сохранено")`.
 * Themed via CSS variables so it tracks the kit's tokens (no next-themes dep).
 */
function Toaster(props: ToasterProps) {
  return (
    <Sonner
      className="toaster group"
      position="top-right"
      style={
        {
          "--normal-bg": "var(--popover)",
          "--normal-text": "var(--popover-foreground)",
          "--normal-border": "var(--border)",
        } as React.CSSProperties
      }
      toastOptions={{
        classNames: {
          toast:
            "group toast group-[.toaster]:rounded-lg group-[.toaster]:border group-[.toaster]:border-border group-[.toaster]:bg-popover group-[.toaster]:text-popover-foreground group-[.toaster]:shadow-lg",
          description: "group-[.toast]:text-muted-foreground",
          actionButton:
            "group-[.toast]:bg-primary group-[.toast]:text-primary-foreground",
          cancelButton: "group-[.toast]:bg-muted group-[.toast]:text-muted-foreground",
        },
      }}
      {...props}
    />
  );
}

export { Toaster };
