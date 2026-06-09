"use client";

import { motion } from "framer-motion";
import { Plus } from "lucide-react";
import { EASE_OUT } from "@/lib/motion";

/**
 * Quick-reply chips for a progressive-discovery question (P1). Rendered beneath
 * the latest assistant question; tapping a chip submits it as the user's answer.
 * `allowCustom` adds a "Другое" chip that hands focus to the free-text input so
 * a chip set never traps the user into the offered options.
 *
 * Stateless on purpose (R-01): the parent owns when to show/hide these (only
 * while the question is the active turn) and what a tap/custom does.
 */
export function DiscoveryChips({
  choices,
  allowCustom,
  onPick,
  onCustom,
}: {
  choices: string[];
  allowCustom: boolean;
  onPick: (choice: string) => void;
  onCustom: () => void;
}) {
  if (choices.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-1.5 px-4 pb-3 pt-0.5">
      {choices.map((c, i) => (
        <motion.button
          key={c}
          type="button"
          onClick={() => onPick(c)}
          initial={{ opacity: 0, y: 4 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.2, ease: EASE_OUT, delay: i * 0.03 }}
          className="inline-flex items-center rounded-full border border-accent/30 bg-accent-subtle/50 px-3 py-1.5 text-xs font-medium text-fg-primary transition-colors hover:border-accent/60 hover:bg-accent-subtle focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
        >
          {c}
        </motion.button>
      ))}
      {allowCustom && (
        <motion.button
          type="button"
          onClick={onCustom}
          initial={{ opacity: 0, y: 4 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{
            duration: 0.2,
            ease: EASE_OUT,
            delay: choices.length * 0.03,
          }}
          className="inline-flex items-center gap-1 rounded-full border border-border-default bg-surface-overlay/60 px-3 py-1.5 text-xs font-medium text-fg-secondary transition-colors hover:border-border-strong hover:text-fg-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
        >
          <Plus className="h-3 w-3" />
          Другое
        </motion.button>
      )}
    </div>
  );
}
