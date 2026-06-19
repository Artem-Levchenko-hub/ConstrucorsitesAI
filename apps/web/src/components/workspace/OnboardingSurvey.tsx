"use client";

import { useState } from "react";
import { motion } from "framer-motion";
import { ArrowRight, Check, Plus, X } from "lucide-react";
import { EASE_OUT } from "@/lib/motion";
import type { SurveyQuestion } from "@/lib/api/types";

/**
 * Onboarding SURVEY popup (owner 2026-06-19 — «несколько вопросов сразу»).
 *
 * The server plans the whole question batch from the first prompt and returns it
 * in one shot (PromptResponse.survey). This modal shows ALL of them at once — the
 * user answers everything and hits «Готово» ONCE, instead of one chat turn per
 * question. Every question is clickable (chips / preset palette swatches) and
 * keeps an open «Другое» free-text path. Skippable — the build never blocks on it.
 *
 * On submit it hands the parent (ChatPanel) a single combined-answers string and
 * the picked design-preset id; the parent fires ONE build prompt (skip_clarify).
 */
export function OnboardingSurvey({
  questions,
  onDone,
  onSkip,
}: {
  questions: SurveyQuestion[];
  /** Combined free-text answers + the picked preset id (or null). */
  onDone: (combinedAnswers: string, presetId: string | null) => void;
  onSkip: () => void;
}) {
  // Per-question text answers (selected chips + inline customs), indexed by
  // question position. Palette pick is tracked separately.
  const [answers, setAnswers] = useState<Record<number, string[]>>({});
  const [custom, setCustom] = useState<Record<number, string>>({});
  const [preset, setPreset] = useState<string | null>(null);

  const toggle = (qi: number, value: string, multi: boolean) =>
    setAnswers((prev) => {
      const cur = prev[qi] ?? [];
      if (multi) {
        return {
          ...prev,
          [qi]: cur.includes(value)
            ? cur.filter((v) => v !== value)
            : [...cur, value],
        };
      }
      // single-select — replace
      return { ...prev, [qi]: cur.includes(value) ? [] : [value] };
    });

  const addCustom = (qi: number) => {
    const v = (custom[qi] ?? "").trim();
    if (!v) return;
    setAnswers((prev) => {
      const cur = prev[qi] ?? [];
      return cur.includes(v) ? prev : { ...prev, [qi]: [...cur, v] };
    });
    setCustom((prev) => ({ ...prev, [qi]: "" }));
  };

  const submit = () => {
    const parts: string[] = [];
    questions.forEach((q, i) => {
      if (q.kind === "palette") return; // preset rides separately
      const picked = answers[i] ?? [];
      if (picked.length) parts.push(`${q.message} — ${picked.join(", ")}`);
    });
    if (preset) {
      const name =
        questions
          .flatMap((q) => (q.kind === "palette" ? q.options : []))
          .find((o) => o.id === preset)?.name ?? preset;
      parts.push(`Палитра — ${name}`);
    }
    onDone(parts.join("\n"), preset);
  };

  const baseChip =
    "inline-flex items-center gap-1 rounded-full border px-3 py-1.5 text-xs font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/50";
  const idleChip =
    "border-accent/30 bg-accent-subtle/50 text-fg-primary hover:border-accent/60 hover:bg-accent-subtle";
  const onChip = "border-accent bg-accent text-accent-fg hover:bg-accent-hover";

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-label="Уточняющие вопросы"
    >
      <motion.div
        initial={{ opacity: 0, scale: 0.96, y: 8 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.96 }}
        transition={{ duration: 0.22, ease: EASE_OUT }}
        className="flex max-h-[85vh] w-full max-w-lg flex-col overflow-hidden rounded-2xl border border-border-default bg-surface-raised shadow-2xl"
      >
        <div className="flex items-start justify-between gap-3 px-5 pt-5 pb-3">
          <div>
            <div className="text-sm font-semibold text-fg-primary">
              Пару вопросов, чтобы собрать точнее
            </div>
            <div className="mt-0.5 text-xs text-fg-tertiary">
              Нажмите варианты или впишите своё. Можно пропустить — подберём сами.
            </div>
          </div>
          <button
            type="button"
            onClick={onSkip}
            aria-label="Пропустить"
            className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-fg-tertiary transition-colors hover:bg-surface-overlay hover:text-fg-primary"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="flex-1 space-y-5 overflow-y-auto px-5 py-2 scrollbar-elegant">
          {questions.map((q, qi) => (
            <div key={qi} className="space-y-2">
              <div className="text-sm font-medium text-fg-primary">
                {q.message}
              </div>

              {q.kind === "palette" ? (
                <div className="grid grid-cols-2 gap-2">
                  {q.options.map((o) => {
                    const sel = preset === o.id;
                    return (
                      <button
                        key={o.id}
                        type="button"
                        onClick={() => setPreset(sel ? null : o.id)}
                        aria-pressed={sel}
                        className={`flex items-center gap-2 rounded-xl border p-2 text-left transition-colors ${
                          sel
                            ? "border-accent bg-accent-subtle"
                            : "border-border-subtle hover:border-border-strong"
                        }`}
                      >
                        <span
                          aria-hidden="true"
                          className="h-8 w-8 shrink-0 rounded-lg border border-border-subtle"
                          style={{
                            background: `linear-gradient(135deg, ${o.bg} 0 50%, ${o.accent} 50% 100%)`,
                          }}
                        />
                        <span className="min-w-0 flex-1">
                          <span className="flex items-center gap-1 text-xs font-medium text-fg-primary">
                            <span className="truncate">{o.name}</span>
                            {sel && <Check className="h-3 w-3 shrink-0 text-accent" />}
                          </span>
                          <span className="line-clamp-1 text-[11px] text-fg-tertiary">
                            {o.one_liner}
                          </span>
                        </span>
                      </button>
                    );
                  })}
                </div>
              ) : (
                <div className="flex flex-wrap gap-1.5">
                  {q.choices.map((c) => {
                    const sel = (answers[qi] ?? []).includes(c);
                    return (
                      <button
                        key={c}
                        type="button"
                        onClick={() => toggle(qi, c, q.multi_select)}
                        aria-pressed={sel}
                        className={`${baseChip} ${sel ? onChip : idleChip}`}
                      >
                        {sel && <Check className="h-3 w-3" />}
                        {c}
                      </button>
                    );
                  })}
                  {/* custom answers the user typed (always shown as selected) */}
                  {(answers[qi] ?? [])
                    .filter((v) => !q.choices.includes(v))
                    .map((v) => (
                      <button
                        key={`x-${v}`}
                        type="button"
                        onClick={() => toggle(qi, v, true)}
                        aria-pressed
                        className={`${baseChip} ${onChip}`}
                      >
                        <Check className="h-3 w-3" />
                        {v}
                      </button>
                    ))}
                  {q.allow_custom && (
                    <span className="inline-flex items-center gap-1">
                      <input
                        type="text"
                        value={custom[qi] ?? ""}
                        onChange={(e) =>
                          setCustom((prev) => ({ ...prev, [qi]: e.target.value }))
                        }
                        onKeyDown={(e) => {
                          if (e.key === "Enter") {
                            e.preventDefault();
                            addCustom(qi);
                          }
                        }}
                        placeholder="Другое…"
                        className="w-28 rounded-full border border-border-default bg-surface-overlay/60 px-3 py-1.5 text-xs text-fg-primary placeholder:text-fg-tertiary focus:border-accent/60 focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
                      />
                      {(custom[qi] ?? "").trim() && (
                        <button
                          type="button"
                          onClick={() => addCustom(qi)}
                          aria-label="Добавить"
                          className="flex h-6 w-6 items-center justify-center rounded-full bg-accent text-accent-fg hover:bg-accent-hover"
                        >
                          <Plus className="h-3 w-3" />
                        </button>
                      )}
                    </span>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>

        <div className="flex items-center justify-between gap-3 border-t border-border-subtle px-5 py-3">
          <button
            type="button"
            onClick={onSkip}
            className="text-xs font-medium text-fg-tertiary transition-colors hover:text-fg-secondary"
          >
            Пропустить — собрать сразу
          </button>
          <button
            type="button"
            onClick={submit}
            className="inline-flex items-center gap-1.5 rounded-full bg-accent px-5 py-2 text-xs font-semibold text-accent-fg transition-colors hover:bg-accent-hover"
          >
            Готово, собираем
            <ArrowRight className="h-3.5 w-3.5" />
          </button>
        </div>
      </motion.div>
    </motion.div>
  );
}
