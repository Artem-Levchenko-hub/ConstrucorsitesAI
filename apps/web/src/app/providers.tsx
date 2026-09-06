"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MotionConfig } from "framer-motion";
import { useEffect, useRef, useState } from "react";
import { Toaster, toast, useSonner } from "sonner";
import "./notifications.css";

function Notifications() {
  const { toasts } = useSonner();
  const persistentErrors = useRef(new Set<string | number>());
  useEffect(() => {
    // Sonner has no per-type host duration. Promote active errors in place,
    // including promise failures, without changing their IDs, actions or callers.
    const active = toast.getToasts();
    for (const id of persistentErrors.current) {
      if (!active.some(item => item.id === id)) persistentErrors.current.delete(id);
    }
    // Read current store entries: hook events can lag a same-ID success update.
    for (const item of active) {
      if (!("type" in item)) continue;
      if (item.type === "error" && item.duration !== Infinity) {
        persistentErrors.current.add(item.id);
        toast.error(item.title, { ...item, duration: Infinity });
      } else if (item.type !== "error" && persistentErrors.current.delete(item.id) && item.duration === Infinity) {
        // Sonner merges same-ID updates; do not carry our error duration into success.
        toast.message(item.title, { ...item, duration: 8000 });
      }
    }
  }, [toasts]);
  return <Toaster theme="light" position="top-right" richColors closeButton visibleToasts={3} duration={8000} className="max-notifications" containerAriaLabel="Уведомления" />;
}

export function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 60_000,
            refetchOnWindowFocus: false,
          },
        },
      }),
  );

  return (
    <QueryClientProvider client={queryClient}>
      {/* One place to honour prefers-reduced-motion for ALL framer-motion in the
          app: "user" keeps opacity transitions but drops transforms/layout for
          users who ask for less motion. The CSS rule in globals.css only covers
          CSS transitions, not framer's JS-driven animations — this closes that
          gap so every micro-interaction degrades gracefully. */}
      <MotionConfig reducedMotion="user">
        {children}
        <Notifications />
      </MotionConfig>
    </QueryClientProvider>
  );
}
