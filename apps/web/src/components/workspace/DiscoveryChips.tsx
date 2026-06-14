"use client";

import { useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { ArrowUp, Check, Plus } from "lucide-react";
import { EASE_OUT } from "@/lib/motion";

/**
 * Interactive answer card for a progressive-discovery question (P1 / pillar 2).
 * Rendered beneath the latest assistant question — the user answers IN THE SAME
 * BLOCK, Claude-Code style, without ever leaving for the chat input:
 *
 *  • single-select (default): tap a chip → submits it as the answer. «Другое»
 *    opens a real text field inline; Enter / the arrow button submits.
 *  • multi-select (`multiSelect`): chips become toggles, «Другое» adds custom
 *    values to the selection, and a «Готово» button submits the whole set as one
 *    answer (joined). Lets the user pick several sections/features in one turn.
 *
 * The free-text «Другое» path always stays open (`allowCustom`) so a chip set
 * never traps the user. Internal selection state is reset per question because
 * the parent keys this component by the question's message id.
 */
export function DiscoveryChips({
  choices,
  allowCustom,
  multiSelect,
  onPick,
}: {
  choices: string[];
  allowCustom: boolean;
  multiSelect: boolean;
  /** Submit the answer (a single chip, an inline custom answer, or the joined
   *  multi-select set) as the user's reply to the question. */
  onPick: (answer: string) => void;
}) {
  // Multi-select picks — preset chips toggled on + any inline custom additions.
  const [selected, setSelected] = useState<string[]>([]);
  const [customOpen, setCustomOpen] = useState(false);
  const [customValue, setCustomValue] = useState("");

  if (choices.length === 0) return null;

  const isSelected = (c: string) => selected.includes(c);
  const toggle = (c: string) =>
    setSelected((prev) =>
      prev.includes(c) ? prev.filter((x) => x !== c) : [...prev, c],
    );
  // Custom values the user typed in multi-select that aren't preset chips —
  // rendered as their own (always-on) selected chips so the picks stay visible.
  const customExtras = selected.filter((c) => !choices.includes(c));

  const handleChip = (c: string) => {
    if (multiSelect) toggle(c);
    else onPick(c);
  };

  const submitCustom = () => {
    const v = customValue.trim();
    if (!v) return;
    if (multiSelect) {
      setSelected((prev) => (prev.includes(v) ? prev : [...prev, v]));
      setCustomValue("");
      // Keep the field open so several custom values can be added in a row.
    } else {
      onPick(v);
    }
  };

  const submitMulti = () => {
    if (selected.length === 0) return;
    onPick(selected.join(", "));
  };

  const baseChip =
    "inline-flex items-center gap-1 rounded-full border px-3 py-1.5 text-xs font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/50";
  const idleChip =
    "border-accent/30 bg-accent-subtle/50 text-fg-primary hover:border-accent/60 hover:bg-accent-subtle";
  const onChip = "border-accent bg-accent text-accent-fg hover:bg-accent-hover";

  return (
    <div className="space-y-2 px-4 pb-3 pt-0.5">
      <div className="flex flex-wrap gap-1.5">
        {choices.map((c, i) => (
          <motion.button
            key={c}
            type="button"
            onClick={() => handleChip(c)}
            aria-pressed={multiSelect ? isSelected(c) : undefined}
            initial={{ opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.2, ease: EASE_OUT, delay: i * 0.03 }}
            className={`${baseChip} ${
              multiSelect && isSelected(c) ? onChip : idleChip
            }`}
          >
            {multiSelect && isSelected(c) && <Check className="h-3 w-3" />}
            {c}
          </motion.button>
        ))}

        {/* Custom multi-select additions — always shown as selected, tap to drop. */}
        {customExtras.map((c) => (
          <button
            key={`custom-${c}`}
            type="button"
            onClick={() => toggle(c)}
            aria-pressed
            className={`${baseChip} ${onChip}`}
          >
            <Check className="h-3 w-3" />
            {c}
          </button>
        ))}

        {allowCustom && !customOpen && (
          <motion.button
            type="button"
            onClick={() => setCustomOpen(true)}
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

      {/* Inline «Другое» free-text field — answer right here, not in the far chat
          input (NORTH STAR pillar 2: «inline-ответ в том же блоке, как в Claude Code»). */}
      <AnimatePresence>
        {allowCustom && customOpen && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.18, ease: EASE_OUT }}
            className="overflow-hidden"
          >
            <div className="flex items-center gap-1.5">
              <input
                autoFocus
                type="text"
                value={customValue}
                onChange={(e) => setCustomValue(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    submitCustom();
                  } else if (e.key === "Escape") {
                    setCustomOpen(false);
                    setCustomValue("");
                  }
                }}
                placeholder={
                  multiSelect ? "Добавьте свой вариант" : "Впишите свой ответ"
                }
                className="min-w-0 flex-1 rounded-full border border-border-default bg-surface-overlay/60 px-3.5 py-1.5 text-xs text-fg-primary placeholder:text-fg-tertiary focus:border-accent/60 focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
              />
              <button
                type="button"
                onClick={submitCustom}
                disabled={!customValue.trim()}
                aria-label={multiSelect ? "Добавить вариант" : "Отправить ответ"}
                className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-accent text-accent-fg transition-colors hover:bg-accent-hover disabled:opacity-40"
              >
                {multiSelect ? (
                  <Plus className="h-3.5 w-3.5" />
                ) : (
                  <ArrowUp className="h-3.5 w-3.5" />
                )}
              </button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* «Готово» — submit the whole multi-select set as one answer. */}
      {multiSelect && (
        <motion.button
          type="button"
          onClick={submitMulti}
          disabled={selected.length === 0}
          initial={{ opacity: 0, y: 4 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.2, ease: EASE_OUT }}
          className="inline-flex items-center gap-1.5 rounded-full bg-accent px-4 py-1.5 text-xs font-semibold text-accent-fg transition-colors hover:bg-accent-hover disabled:opacity-40"
        >
          Готово
          {selected.length > 0 && (
            <span className="tabular-nums opacity-80">· {selected.length}</span>
          )}
        </motion.button>
      )}
    </div>
  );
}
