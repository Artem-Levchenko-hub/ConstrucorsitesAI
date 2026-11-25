"use client";

import { useEffect } from "react";
import { apiFetch } from "@/lib/api/client";

export function StudioActivity({ projectId, release }: { projectId?: string; release: string }) {
  useEffect(() => {
    if (process.env.NEXT_PUBLIC_USE_MOCKS !== "false") return;
    let lastInteraction = 0;
    let lastActivity = 0;
    let lastError = 0;

    function record(kind: "open" | "active" | "error") {
      void apiFetch<void>("/api/studio/activity", {
        method: "POST",
        json: { project_id: projectId, release, kind },
        timeoutMs: 5000,
      }).catch(() => {});
    }

    function sendActivity() {
      const now = Date.now();
      if (document.hidden || now - lastInteraction >= 60_000 || now - lastActivity < 60_000) return;
      lastActivity = now;
      record("active");
    }

    function interact() {
      lastInteraction = Date.now();
      sendActivity();
    }

    function error() {
      const now = Date.now();
      if (now - lastError < 60_000) return;
      lastError = now;
      record("error");
    }

    record("open");
    const actions = ["pointerdown", "keydown", "wheel"] as const;
    for (const action of actions) window.addEventListener(action, interact, { passive: true });
    window.addEventListener("error", error);
    window.addEventListener("unhandledrejection", error);
    const timer = window.setInterval(sendActivity, 60_000);
    return () => {
      for (const action of actions) window.removeEventListener(action, interact);
      window.removeEventListener("error", error);
      window.removeEventListener("unhandledrejection", error);
      window.clearInterval(timer);
    };
  }, [projectId, release]);
  return null;
}
