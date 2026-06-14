"use client";

import { motion } from "framer-motion";
import { GitFork, Sparkles, Wand2 } from "lucide-react";
import { EASE_OUT } from "@/lib/motion";

/**
 * The warm landing a remixer sees instead of a cold empty chat (NORTH STAR
 * pillar 4 — viral shareability). Backend seeds a `<remix>` message on fork
 * (apps/api/src/omnia_api/services/fork_recap.py); this renders it as a recap
 * card: what was remixed, its captured design DNA, and one-tap starter edits.
 *
 * `onSuggest` submits a starter prompt through the normal chat pipeline (same
 * seam as the error card's «Починить»). Omitted in replays / screenshots →
 * chips render non-interactive.
 */
export function RemixRecapCard({
  name,
  dna,
  suggestions,
  onSuggest,
}: {
  name: string;
  dna: string;
  suggestions: string[];
  onSuggest?: (prompt: string) => void;
}) {
  const chips = dna
    .split("·")
    .map((s) => s.trim())
    .filter(Boolean);

  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.28, ease: EASE_OUT }}
      className="rounded-2xl border border-accent/30 bg-accent-subtle/40 p-3.5"
    >
      <div className="mb-1 flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wider text-accent">
        <GitFork className="h-3 w-3" />
        Ремикс
      </div>
      <div className="text-sm font-medium leading-snug text-fg-primary">
        Вы ремикснули{" "}
        <span className="text-accent">«{name}»</span>
      </div>

      {chips.length > 0 && (
        <div className="mt-2.5">
          <div className="mb-1.5 flex items-center gap-1.5 text-[10px] uppercase tracking-wide text-fg-tertiary">
            <Sparkles className="h-3 w-3 text-accent" />
            Дизайн-ДНК сохранена
          </div>
          <div className="flex flex-wrap gap-1.5">
            {chips.map((c, i) => (
              <motion.span
                key={c}
                initial={{ opacity: 0, y: 4 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.2, ease: EASE_OUT, delay: 0.08 + i * 0.04 }}
                className="inline-flex items-center rounded-lg border border-border-subtle bg-surface-overlay/70 px-2 py-0.5 text-xs text-fg-secondary"
              >
                {c}
              </motion.span>
            ))}
          </div>
        </div>
      )}

      {suggestions.length > 0 && (
        <div className="mt-3">
          <div className="mb-1.5 flex items-center gap-1.5 text-[10px] uppercase tracking-wide text-fg-tertiary">
            <Wand2 className="h-3 w-3 text-accent" />
            С чего начать
          </div>
          <div className="flex flex-col gap-1.5">
            {suggestions.map((s, i) => (
              <motion.button
                key={s}
                type="button"
                disabled={!onSuggest}
                onClick={() => onSuggest?.(s)}
                initial={{ opacity: 0, y: 4 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.2, ease: EASE_OUT, delay: 0.12 + i * 0.05 }}
                className="group flex items-center gap-2 rounded-xl border border-border-subtle bg-surface-raised/60 px-2.5 py-2 text-left text-xs text-fg-primary transition-colors hover:border-accent/50 hover:bg-accent-subtle/60 disabled:cursor-default disabled:hover:border-border-subtle disabled:hover:bg-surface-raised/60"
              >
                <Wand2 className="h-3.5 w-3.5 shrink-0 text-fg-tertiary transition-colors group-hover:text-accent" />
                <span className="min-w-0 flex-1 leading-snug">{s}</span>
              </motion.button>
            ))}
          </div>
        </div>
      )}
    </motion.div>
  );
}
