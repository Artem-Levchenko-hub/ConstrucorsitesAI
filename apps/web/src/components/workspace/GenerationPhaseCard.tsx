"use client";

import { useEffect, useState, type RefObject } from "react";
import { Check, Clock3 } from "lucide-react";
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

/** A single fitted part signals ongoing work; the geometry encodes no stage order. */
function PhaseMark({ ready }: { ready: boolean }) {
  return <svg className={styles.mark} viewBox="0 0 80 80" fill="none" aria-hidden="true" focusable="false"
    data-generation-indicator>
    <rect className={styles.frame} x="14" y="8" width="54" height="64" rx="8"
      stroke="currentColor" strokeWidth="1.5" />
    <path className={styles.rails} d="M23 18h16m-16 0h-1m1 37h14v8H23v-8Zm22 0h14v8H45v-8Z"
      stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    {ready ? <path className={styles.ready} d="m26 36 9 9 18-20" stroke="currentColor"
      strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
      : <>
        <rect className={styles.slot} x="22" y="27" width="38" height="20" rx="4"
          stroke="currentColor" strokeWidth="1.5" />
        <g className={styles.cursor}>
          <rect x="8" y="27" width="36" height="20" rx="4" fill="currentColor" />
          <path className={styles.tileDetail} d="M15 34h22m-22 6h13" stroke="currentColor"
            strokeWidth="1.5" strokeLinecap="round" />
        </g>
      </>}
  </svg>;
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
    <div className={styles.main}>
      <PhaseMark ready={phase.id === "ready"} />
      <div className={styles.content}>
        <div role="status" aria-live="polite" aria-atomic="true">
          <p className={styles.title}>{phase.title}</p>
          <p className={styles.explanation}>{phase.explanation}</p>
        </div>
      </div>
      {phase.milestones.length > 0 && <ul className={styles.milestones} aria-label="Подтверждённые этапы">
        {phase.milestones.map(label => <li key={label}><Check size={13} aria-hidden="true" />{label}</li>)}
      </ul>}
    </div>
    {backgroundNotice && <div className={styles.backgroundNotice} data-generation-background-notice>
      <p className={styles.estimate}>
        <Clock3 className={styles.noticeMark} size={13} strokeWidth={1.5} aria-hidden="true" />
        <span>Создание может занять 20–30 минут, иногда дольше.</span>
      </p>
      <p className={styles.continuity}>{backgroundNotice}</p>
    </div>}
  </div>;
}
