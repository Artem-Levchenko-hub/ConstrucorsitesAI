"use client";

import { useQuery } from "@tanstack/react-query";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { Sparkles } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import {
  JOY_DURATION_MS,
  joyShouldShow,
  type JoyTrigger,
} from "@/lib/joy-moment";

/**
 * V3.8 — JOY-MOMENT. Ровно одна бренд-цветная success-нота на завершённый билд.
 *
 * Подписан на client-only кэш `["joy", projectId]`, который usePromptStream
 * пишет на `llm.done` ТОЛЬКО для build-хода (не edit, не error — см.
 * `buildJoyTrigger`). Решение «показать сейчас» делегировано чистому
 * `joyShouldShow` (тот же код-путь, что пиннят юнит-тесты): новый build-триггер
 * показывается ровно 1× (дедуп по message_id), уходит через `JOY_DURATION_MS`
 * (< 2.5s); под `prefers-reduced-motion` подавлен целиком — празднование это
 * чистая декорация, под opt-out оно молчит.
 *
 * Окрашен акцентом ЭТОГО билда (`trigger.accent` из art-director-брифа), поэтому
 * вспышка несёт палитру, которую юзер только что увидел рождаться. Оверлей не
 * перехватывает клики (`pointer-events-none`), живёт поверх preview-полотна.
 */
export function JoyBurst({ projectId }: { projectId: string }) {
  const reduced = useReducedMotion();
  const { data: trigger } = useQuery<JoyTrigger | null>({
    queryKey: ["joy", projectId],
    queryFn: () => null,
    enabled: false,
    initialData: null,
  });

  const [shown, setShown] = useState<JoyTrigger | null>(null);
  const lastIdRef = useRef<string | null>(null);

  useEffect(() => {
    if (!joyShouldShow(trigger, lastIdRef.current, !!reduced)) return;
    // trigger гарантированно не null (joyShouldShow это проверил).
    const t = trigger as JoyTrigger;
    lastIdRef.current = t.id;
    setShown(t);
    const timer = window.setTimeout(() => setShown(null), JOY_DURATION_MS);
    return () => window.clearTimeout(timer);
  }, [trigger, reduced]);

  return (
    <div
      aria-hidden={!shown}
      className="pointer-events-none absolute inset-x-0 top-3 z-20 flex justify-center"
    >
      <AnimatePresence>
        {shown && (
          <motion.div
            key={shown.id}
            role="status"
            aria-live="polite"
            initial={{ opacity: 0, y: -10, scale: 0.92 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -6, scale: 0.96 }}
            transition={{ type: "spring", stiffness: 420, damping: 26 }}
            className="flex items-center gap-2 rounded-full border bg-surface-raised/95 px-3.5 py-1.5 text-xs font-medium text-fg-primary shadow-lg backdrop-blur"
            style={{
              borderColor: shown.accent,
              boxShadow: `0 6px 24px -6px ${shown.accent}66`,
            }}
          >
            <motion.span
              className="flex h-5 w-5 items-center justify-center rounded-full"
              style={{ backgroundColor: shown.accent, color: "#fff" }}
              initial={{ scale: 0, rotate: -30 }}
              animate={{ scale: 1, rotate: 0 }}
              transition={{ type: "spring", stiffness: 600, damping: 18, delay: 0.04 }}
            >
              <Sparkles className="h-3 w-3" />
            </motion.span>
            Готово — сайт собран
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
