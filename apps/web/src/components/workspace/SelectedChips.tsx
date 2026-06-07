"use client";

import { AnimatePresence, motion } from "framer-motion";
import { MousePointerClick, X } from "lucide-react";
import { chipIn } from "@/lib/motion";
import { cn } from "@/lib/utils";

export type ChipItem = {
  /** Present for editable composer chips; absent for read-only history chips. */
  id?: string;
  selector: string;
  label?: string | null;
  text?: string | null;
  comment?: string | null;
};

/**
 * Renders picked-element chips. Two modes from one component (DRY):
 *  - editable (composer): pass `onComment` + `onRemove` → per-element comment
 *    input and a remove button.
 *  - read-only (chat history): omit the callbacks → static label + comment.
 */
export function SelectedChips({
  items,
  onComment,
  onRemove,
  className,
}: {
  items: ChipItem[];
  onComment?: (id: string, comment: string) => void;
  onRemove?: (id: string) => void;
  className?: string;
}) {
  if (items.length === 0) return null;
  const editable = Boolean(onComment || onRemove);

  return (
    // `initial={false}` keeps history-replay chips from popping on first paint;
    // only chips added live (picking an element to edit) animate in, and removed
    // ones animate out. `layout` slides the survivors closed behind a removal.
    <div className={cn("space-y-1.5", className)}>
      <AnimatePresence initial={false}>
        {items.map((it, i) => (
          <motion.div
            key={it.id ?? i}
            layout
            variants={chipIn}
            initial="hidden"
            animate="visible"
            exit="exit"
            className="rounded-md border border-accent/30 bg-accent-subtle/40 px-2 py-1.5"
          >
            <div className="flex items-center gap-1.5">
            <MousePointerClick className="h-3 w-3 text-accent shrink-0" />
            <span
              className="font-mono text-[11px] text-fg-secondary truncate"
              title={it.selector}
            >
              {it.label || it.selector}
            </span>
            {it.text && (
              <span className="text-[11px] text-fg-tertiary truncate">
                — {it.text}
              </span>
            )}
            {onRemove && it.id && (
              <button
                type="button"
                onClick={() => onRemove(it.id as string)}
                title="Убрать элемент"
                aria-label="Убрать выделенный элемент"
                className="ml-auto shrink-0 text-fg-tertiary hover:text-fg-primary transition-colors"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            )}
          </div>

          {editable && onComment && it.id ? (
            <input
              type="text"
              value={it.comment ?? ""}
              onChange={(e) => onComment(it.id as string, e.target.value)}
              placeholder="Что изменить в этом элементе?"
              aria-label={`Комментарий к элементу ${it.label || it.selector}`}
              className="mt-1 w-full bg-transparent text-xs text-fg-primary placeholder:text-fg-tertiary focus:outline-none"
            />
          ) : it.comment ? (
            <div className="mt-0.5 text-xs text-fg-secondary break-words">
              {it.comment}
            </div>
          ) : null}
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}
