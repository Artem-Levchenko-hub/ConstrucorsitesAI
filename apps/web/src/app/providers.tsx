"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MotionConfig } from "framer-motion";
import { useState } from "react";
import { Toaster } from "sonner";

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
        <Toaster theme="dark" richColors closeButton />
      </MotionConfig>
    </QueryClientProvider>
  );
}
