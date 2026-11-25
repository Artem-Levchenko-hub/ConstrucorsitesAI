"use client";

import { useEffect, useState, type RefObject } from "react";
import type { GenerationPhase } from "@/lib/generation-phase";
import styles from "./GenerationPhaseCard.module.css";

/** One observer controls both the authored loop and the optional elapsed timer. */
export function useGenerationPhaseActivity(ref: RefObject<HTMLElement | null>, active: boolean) {
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    if (!active || !ref.current) return;
    let inView = typeof IntersectionObserver === "undefined";
    const sync = () => setVisible(inView && document.visibilityState !== "hidden");
    const observer = typeof IntersectionObserver === "undefined" ? null
      : new IntersectionObserver(([entry]) => { inView = entry.isIntersecting; sync(); });
    observer?.observe(ref.current);
    document.addEventListener("visibilitychange", sync);
    sync();
    return () => {
      observer?.disconnect();
      document.removeEventListener("visibilitychange", sync);
    };
  }, [active, ref]);
  return active && visible;
}

export function GenerationPhaseCard({ phase, motionActive }: {
  phase: GenerationPhase;
  motionActive: boolean;
}) {
  const explanation = phase.active ? phase.detail : phase.explanation;
  const backgroundNotice = phase.active
    ? "Создание может занять до 90 минут. Вкладку можно закрыть — работа продолжится."
    : phase.id === "queued" || phase.id === "waiting"
      ? "Вкладку можно закрыть — задача сохранена на сервере."
      : null;
  return <div className={styles.phase} data-generation-phase={phase.id}
    data-motion={phase.active && motionActive ? "running" : "paused"}>
    <span className={styles.dots} data-generation-indicator aria-hidden="true">
      <span className={styles.dot} />
      <span className={styles.dot} />
      <span className={styles.dot} />
    </span>
    <div className={styles.content}>
      <div role="status" aria-live="polite" aria-atomic="true">
        <p className={styles.title}>{phase.title}</p>
        {explanation && <p className={styles.helper}>{explanation}</p>}
      </div>
      {backgroundNotice && <p className={styles.helper} data-generation-background-notice>{backgroundNotice}</p>}
    </div>
  </div>;
}
