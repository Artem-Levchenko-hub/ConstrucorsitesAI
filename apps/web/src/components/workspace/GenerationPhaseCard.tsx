"use client";

import { useEffect, useState, type RefObject } from "react";
import { Check } from "lucide-react";
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
  const backgroundNotice = phase.active
    ? "Можно закрыть вкладку: работа продолжится на сервере, готовый результат останется здесь."
    : phase.id === "queued" || phase.id === "waiting"
      ? "Можно закрыть вкладку: задача сохранена на сервере. Готовый результат будет доступен здесь."
      : null;
  return <div className={styles.phase} data-generation-phase={phase.id}
    data-motion={phase.active && motionActive ? "running" : "paused"}>
    <svg className={styles.mark} viewBox="0 0 44 44" fill="none" aria-hidden="true">
      <path className={styles.frame} d="M13 8H8v28h28V8h-5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
      {phase.id === "ready" ? <path className={styles.ready} d="m15 22 5 5 10-11" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        : <>
          <path className={styles.rails} d="M14 16h16M14 22h12M14 28h16" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
          <rect className={styles.cursor} x="14" y="14" width="5" height="4" rx="1" fill="currentColor" />
        </>}
    </svg>
    <div className={styles.content}>
      <div role="status" aria-live="polite" aria-atomic="true">
        <div key={phase.id} className={styles.copy}>
          <p className={styles.title}>{phase.title}</p>
          <p className={styles.explanation}>{phase.explanation}</p>
        </div>
      </div>
      {backgroundNotice && <p className={styles.backgroundNotice} data-generation-background-notice>
        Создание может занять 20–30 минут, иногда дольше. {backgroundNotice}
      </p>}
      {phase.milestones.length > 0 && <ul className={styles.milestones} aria-label="Подтверждённые этапы">
        {phase.milestones.map(label => <li key={label}><Check size={12} aria-hidden="true" />{label}</li>)}
      </ul>}
    </div>
  </div>;
}
