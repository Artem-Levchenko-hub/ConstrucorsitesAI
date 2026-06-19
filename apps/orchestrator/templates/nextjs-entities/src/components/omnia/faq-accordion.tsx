"use client";

import * as React from "react";
import { ChevronDown } from "lucide-react";

import { cn } from "@/lib/utils";

export interface FaqItem {
  /** The question — the row's tappable headline. Keep it one honest line. */
  question: React.ReactNode;
  /** The answer, revealed on expand. One or two short paragraphs read best. */
  answer: React.ReactNode;
}

export interface FaqAccordionProps {
  /** The questions, in display order. */
  items: FaqItem[];
  /** Index opened on first paint (Spotify/Navan pattern — the page never lands
   *  fully collapsed). Pass `null` to start with everything closed. Default 0. */
  defaultOpen?: number | null;
  /** Allow several answers open at once. Default false = classic single-open
   *  accordion (opening one closes the rest, keeps focus on one answer). */
  allowMultiple?: boolean;
  className?: string;
}

function FaqRow({
  item,
  open,
  onToggle,
  id,
}: {
  item: FaqItem;
  open: boolean;
  onToggle: () => void;
  id: string;
}) {
  return (
    <div
      className={cn(
        "overflow-hidden rounded-2xl border transition-colors duration-200",
        open
          ? "border-primary/40 bg-primary/[0.04] elev-1"
          : "border-border bg-card hover:border-primary/25",
      )}
    >
      <h3>
        <button
          type="button"
          onClick={onToggle}
          aria-expanded={open}
          aria-controls={`${id}-panel`}
          id={`${id}-trigger`}
          className="flex w-full items-center justify-between gap-4 px-5 py-5 text-left sm:px-6"
        >
          <span
            className={cn(
              "text-pretty text-[15px] font-semibold tracking-tight transition-colors sm:text-base",
              open ? "text-primary" : "text-foreground",
            )}
          >
            {item.question}
          </span>
          <ChevronDown
            aria-hidden
            className={cn(
              "size-5 shrink-0 transition-transform duration-300",
              open ? "rotate-180 text-primary" : "text-muted-foreground",
            )}
          />
        </button>
      </h3>

      {/* Dependency-free smooth height: grid-rows 0fr→1fr animates the panel
          open/closed without measuring the content. */}
      <div
        id={`${id}-panel`}
        role="region"
        aria-labelledby={`${id}-trigger`}
        className={cn(
          "grid transition-[grid-template-rows] duration-300 ease-out",
          open ? "grid-rows-[1fr]" : "grid-rows-[0fr]",
        )}
      >
        <div className="overflow-hidden">
          <div className="px-5 pb-5 text-pretty text-sm leading-relaxed text-muted-foreground sm:px-6 sm:text-[15px]">
            {item.answer}
          </div>
        </div>
      </div>
    </div>
  );
}

/**
 * The public storefront's FAQ — a brand-aware accordion the way enterprise
 * marketing pages present it (Mobbin: Google Maps, Spotify «Still curious?»,
 * Coursera, Navan, TikTok Creative Exchange): a single column of rounded rows
 * where the open row lifts into a brand-tinted card with a rotating chevron and
 * the answer slides open. It reads as a different brand per niche straight from
 * the palette tokens (`--primary`) with no per-app styling. Drop it inside a
 * <StorefrontSection> (omit that section's `columns` — this component owns its
 * own single-column rhythm), so the page never hand-rolls a flat list of
 * <details> again.
 *
 *   <StorefrontSection id="faq" eyebrow="Вопросы" title="Частые вопросы"
 *     lead="Коротко о записи, ценах и гарантии." align="center" tint>
 *     <FaqAccordion items={[
 *       { question: "Нужно ли записываться заранее?",
 *         answer: "Да, онлайн-запись занимает минуту — выберите врача и удобное время." },
 *       { question: "Больно ли лечить зубы у вас?",
 *         answer: "Лечение под местной анестезией — вы не почувствуете боли даже при сложных случаях." },
 *       { question: "Даёте ли вы гарантию на работу?",
 *         answer: "На все виды лечения действует гарантия до 2 лет, прописанная в договоре." },
 *     ]} />
 *   </StorefrontSection>
 */
export function FaqAccordion({
  items,
  defaultOpen = 0,
  allowMultiple = false,
  className,
}: FaqAccordionProps) {
  const [openSet, setOpenSet] = React.useState<Set<number>>(() =>
    defaultOpen == null ? new Set() : new Set([defaultOpen]),
  );

  const toggle = React.useCallback(
    (i: number) => {
      setOpenSet((prev) => {
        const next = new Set(allowMultiple ? prev : []);
        if (prev.has(i)) next.delete(i);
        else next.add(i);
        return next;
      });
    },
    [allowMultiple],
  );

  return (
    <div className={cn("stagger mx-auto flex max-w-3xl flex-col gap-3", className)}>
      {items.map((item, i) => (
        <FaqRow
          key={i}
          id={`faq-${i}`}
          item={item}
          open={openSet.has(i)}
          onToggle={() => toggle(i)}
        />
      ))}
    </div>
  );
}
