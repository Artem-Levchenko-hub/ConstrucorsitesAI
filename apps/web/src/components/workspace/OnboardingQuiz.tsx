"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AnimatePresence, motion } from "framer-motion";
import {
  ArrowLeft,
  ArrowRight,
  Check,
  Pencil,
  Sparkles,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { getDesignPresets } from "@/lib/api/presets";
import type { DesignPresetPublic } from "@/lib/api/types";
import { cn } from "@/lib/utils";

/**
 * First-prompt onboarding quiz. Instead of dumping the raw idea straight into
 * the generator, we ask a few quick, tappable questions (goal, tone, style +
 * palette, sections) so the very first build lands close. Curated + instant (no
 * LLM round-trip) to keep the user "in flow"; EVERY step has a "свой вариант"
 * free-text escape; the whole quiz is skippable.
 *
 * On finish it compiles a short Russian brief and hands back the chosen preset
 * id (if any) — the caller folds the brief into the prompt and pins the preset.
 */

type ChoiceStep = {
  id: string;
  kind: "single" | "multi";
  title: string;
  subtitle?: string;
  choices: { value: string; emoji?: string }[];
};
type TextStep = { id: string; kind: "text"; title: string; subtitle?: string; placeholder: string };
type StyleStep = { id: "style"; kind: "style"; title: string; subtitle?: string };
type Step = ChoiceStep | StyleStep | TextStep;

const STEPS: Step[] = [
  {
    id: "goal",
    kind: "single",
    title: "Какая главная задача сайта?",
    subtitle: "Под неё подберём структуру и акценты.",
    choices: [
      { value: "Заявки и лиды", emoji: "📩" },
      { value: "Продажи онлайн", emoji: "🛒" },
      { value: "Запись / бронирование", emoji: "📅" },
      { value: "Презентация и имидж", emoji: "✨" },
      { value: "Портфолио / витрина работ", emoji: "🎨" },
    ],
  },
  {
    id: "tone",
    kind: "single",
    title: "Какое настроение ближе?",
    subtitle: "Тон влияет на шрифты, тексты и динамику.",
    choices: [
      { value: "Премиум и сдержанно", emoji: "🤍" },
      { value: "Дружелюбно и тепло", emoji: "🧡" },
      { value: "Смело и ярко", emoji: "⚡" },
      { value: "Строго и по-деловому", emoji: "🏛️" },
      { value: "Минимализм", emoji: "◻️" },
    ],
  },
  {
    id: "style",
    kind: "style",
    title: "Выберите стиль и палитру",
    subtitle: "Это задаёт цвета и характер. Или впишите свои цвета.",
  },
  {
    id: "sections",
    kind: "multi",
    title: "Какие блоки нужны?",
    subtitle: "Отметьте всё, что должно быть. Можно несколько.",
    choices: [
      { value: "Услуги / товары", emoji: "🧩" },
      { value: "Цены / тарифы", emoji: "💳" },
      { value: "Отзывы", emoji: "⭐" },
      { value: "FAQ", emoji: "❓" },
      { value: "Галерея / портфолио", emoji: "🖼️" },
      { value: "О нас / команда", emoji: "👥" },
      { value: "Контакты / карта", emoji: "📍" },
      { value: "Форма заявки", emoji: "✍️" },
    ],
  },
  {
    id: "extra",
    kind: "text",
    title: "Что ещё важно учесть?",
    subtitle: "Оффер, УТП, контакты, сайт-референс — пара слов. Можно пропустить.",
    placeholder: "Напр.: бесплатный замер за 24ч, телефон +7…, нравится сайт apple.com",
  },
];

// Display order for the swatch row (only keys that exist are shown).
const SWATCH_ORDER = ["bg", "accent", "fg", "muted", "bg_alt", "border"];

type Answer = { selected: string[]; custom: string };
const EMPTY: Answer = { selected: [], custom: "" };

const slide = {
  enter: (dir: number) => ({ x: dir > 0 ? 36 : -36, opacity: 0 }),
  center: { x: 0, opacity: 1 },
  exit: (dir: number) => ({ x: dir > 0 ? -36 : 36, opacity: 0 }),
};

export function OnboardingQuiz({
  idea,
  onComplete,
  onSkip,
}: {
  idea: string;
  onComplete: (brief: string, presetId: string | null) => void;
  onSkip: () => void;
}) {
  const { data: presets, isPending: presetsLoading } = useQuery({
    queryKey: ["design-presets"],
    queryFn: getDesignPresets,
    staleTime: 30 * 60_000,
  });

  const [stepIdx, setStepIdx] = useState(0);
  const [dir, setDir] = useState(1);
  const [answers, setAnswers] = useState<Record<string, Answer>>({});
  // Per-step "свой вариант" input visibility.
  const [customOpen, setCustomOpen] = useState<Record<string, boolean>>({});
  // The catalog has ~29 presets; show a curated first 8 to stay "in flow",
  // expandable to the full list on demand.
  const [showAllPresets, setShowAllPresets] = useState(false);
  const visiblePresets = useMemo(
    () => (showAllPresets ? presets : presets?.slice(0, 8)),
    [showAllPresets, presets],
  );

  const step = STEPS[stepIdx];
  const isLast = stepIdx === STEPS.length - 1;
  const ans = answers[step.id] ?? EMPTY;

  const setAns = (patch: Partial<Answer>) =>
    setAnswers((a) => ({ ...a, [step.id]: { ...(a[step.id] ?? EMPTY), ...patch } }));

  const go = (delta: number) => {
    setDir(delta);
    setStepIdx((i) => Math.min(STEPS.length - 1, Math.max(0, i + delta)));
  };

  // ── single / multi chip handlers ──────────────────────────────────────
  const pickSingle = (value: string) =>
    setAns({ selected: ans.selected[0] === value ? [] : [value], custom: "" });
  const toggleMulti = (value: string) =>
    setAns({
      selected: ans.selected.includes(value)
        ? ans.selected.filter((v) => v !== value)
        : [...ans.selected, value],
    });
  const openCustom = () => {
    setCustomOpen((c) => ({ ...c, [step.id]: true }));
    // For single/style, picking custom clears the chip selection.
    if (step.kind === "single" || step.kind === "style") setAns({ selected: [] });
  };

  const compile = (): { brief: string; presetId: string | null } => {
    const get = (id: string) => answers[id] ?? EMPTY;
    const lines: string[] = [];
    const single = (id: string) => {
      const a = get(id);
      return a.custom.trim() || a.selected[0] || "";
    };
    const goal = single("goal");
    const tone = single("tone");
    if (goal) lines.push(`Главная задача: ${goal}`);
    if (tone) lines.push(`Настроение: ${tone}`);

    // style + palette
    let presetId: string | null = null;
    const styleA = get("style");
    if (styleA.custom.trim()) {
      lines.push(`Цвета/стиль: ${styleA.custom.trim()}`);
    } else if (styleA.selected[0] && presets) {
      const p = presets.find((x) => x.id === styleA.selected[0]);
      if (p) {
        presetId = p.id;
        const hexes = SWATCH_ORDER.filter((k) => p.palette[k])
          .map((k) => p.palette[k])
          .join(", ");
        lines.push(`Стиль: ${p.name} — ${p.one_liner} (палитра: ${hexes})`);
      }
    }

    const secA = get("sections");
    const secs = [...secA.selected, ...(secA.custom.trim() ? [secA.custom.trim()] : [])];
    if (secs.length) lines.push(`Обязательные блоки: ${secs.join(", ")}`);

    const extra = get("extra").custom.trim();
    if (extra) lines.push(`Важно учесть: ${extra}`);

    const brief = lines.length
      ? `${idea}\n\n— Бриф из опроса —\n${lines.map((l) => `• ${l}`).join("\n")}`
      : idea;
    return { brief, presetId };
  };

  const finish = () => {
    const { brief, presetId } = compile();
    onComplete(brief, presetId);
  };

  // Enter advances / finishes (but not while typing in a textarea).
  const rootRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onSkip();
        return;
      }
      if (e.key === "Enter" && !e.shiftKey) {
        const tag = (e.target as HTMLElement)?.tagName;
        if (tag === "TEXTAREA") return; // let multiline custom inputs use Enter
        e.preventDefault();
        if (isLast) finish();
        else go(1);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stepIdx, answers, presets, isLast]);

  const showCustom = customOpen[step.id] ?? false;

  return (
    <motion.div
      ref={rootRef}
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
    >
      <div
        className="absolute inset-0 bg-black/55 backdrop-blur-sm"
        onClick={onSkip}
      />
      <motion.div
        initial={{ opacity: 0, y: 16, scale: 0.98 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={{ duration: 0.25, ease: [0.16, 1, 0.3, 1] }}
        className="relative w-full max-w-xl rounded-2xl border border-border-default bg-surface-panel-dark shadow-2xl overflow-hidden"
      >
        {/* Header: progress + skip */}
        <div className="px-6 pt-5 pb-3">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-1.5 text-xs font-medium text-accent">
              <Sparkles className="h-3.5 w-3.5" />
              Быстрый опрос
              <span className="text-fg-tertiary font-mono ml-1">
                {stepIdx + 1}/{STEPS.length}
              </span>
            </div>
            <button
              type="button"
              onClick={onSkip}
              className="flex items-center gap-1 text-xs text-fg-tertiary hover:text-fg-secondary transition-colors"
              title="Пропустить и сразу сгенерировать"
            >
              Пропустить
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
          <div className="h-1 rounded-full bg-surface-overlay overflow-hidden">
            <motion.div
              className="h-full rounded-full bg-accent"
              initial={false}
              animate={{ width: `${((stepIdx + 1) / STEPS.length) * 100}%` }}
              transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
            />
          </div>
        </div>

        {/* Step body */}
        <div className="px-6 pb-2 min-h-[296px]">
          <AnimatePresence mode="wait" custom={dir}>
            <motion.div
              key={step.id}
              custom={dir}
              variants={slide}
              initial="enter"
              animate="center"
              exit="exit"
              transition={{ duration: 0.22, ease: [0.16, 1, 0.3, 1] }}
            >
              <h2 className="text-lg font-semibold text-fg-primary">
                {step.title}
              </h2>
              {step.subtitle && (
                <p className="text-xs text-fg-tertiary mt-1 mb-4">
                  {step.subtitle}
                </p>
              )}

              {/* TEXT step */}
              {step.kind === "text" && (
                <textarea
                  autoFocus
                  value={ans.custom}
                  onChange={(e) => setAns({ custom: e.target.value })}
                  placeholder={step.placeholder}
                  rows={4}
                  className="w-full rounded-xl border border-border-default bg-surface-input px-3.5 py-3 text-sm text-fg-primary placeholder:text-fg-tertiary resize-none focus:outline-none focus:border-[rgba(124,92,255,0.5)] focus:shadow-[0_0_0_4px_rgba(124,92,255,0.10)] transition-all"
                />
              )}

              {/* SINGLE / MULTI chips */}
              {(step.kind === "single" || step.kind === "multi") && (
                <div className="grid grid-cols-2 gap-2">
                  {step.choices.map((c) => {
                    const active = ans.selected.includes(c.value);
                    return (
                      <button
                        key={c.value}
                        type="button"
                        onClick={() =>
                          step.kind === "single"
                            ? pickSingle(c.value)
                            : toggleMulti(c.value)
                        }
                        className={cn(
                          "flex items-center gap-2 rounded-xl border px-3 py-2.5 text-left text-sm transition-all",
                          active
                            ? "border-accent bg-accent-subtle text-fg-primary ring-1 ring-inset ring-[rgba(124,92,255,0.35)]"
                            : "border-border-default bg-surface-raised text-fg-secondary hover:border-border-strong hover:text-fg-primary",
                        )}
                      >
                        {c.emoji && <span className="text-base leading-none">{c.emoji}</span>}
                        <span className="flex-1 min-w-0">{c.value}</span>
                        {active && <Check className="h-3.5 w-3.5 text-accent shrink-0" />}
                      </button>
                    );
                  })}
                </div>
              )}

              {/* STYLE + PALETTE presets */}
              {step.kind === "style" && (
                <div>
                <div className="grid grid-cols-2 gap-2 max-h-[244px] overflow-y-auto scrollbar-elegant pr-1">
                  {presetsLoading &&
                    Array.from({ length: 4 }).map((_, i) => (
                      <div
                        key={i}
                        className="h-[72px] rounded-xl border border-border-subtle bg-surface-raised animate-pulse"
                      />
                    ))}
                  {visiblePresets?.map((p: DesignPresetPublic) => {
                    const active = ans.selected[0] === p.id;
                    return (
                      <button
                        key={p.id}
                        type="button"
                        onClick={() =>
                          setAns({ selected: active ? [] : [p.id], custom: "" })
                        }
                        className={cn(
                          "flex flex-col gap-2 rounded-xl border px-3 py-2.5 text-left transition-all",
                          active
                            ? "border-accent bg-accent-subtle ring-1 ring-inset ring-[rgba(124,92,255,0.35)]"
                            : "border-border-default bg-surface-raised hover:border-border-strong",
                        )}
                      >
                        <div className="flex items-center gap-1">
                          {SWATCH_ORDER.filter((k) => p.palette[k]).map((k) => (
                            <span
                              key={k}
                              className="h-4 w-4 rounded-[5px] border border-black/10 shadow-sm"
                              style={{ backgroundColor: p.palette[k] }}
                              title={`${k}: ${p.palette[k]}`}
                            />
                          ))}
                          {active && (
                            <Check className="h-3.5 w-3.5 text-accent ml-auto" />
                          )}
                        </div>
                        <div className="min-w-0">
                          <div className="text-sm font-medium text-fg-primary truncate">
                            {p.name}
                          </div>
                          <div className="text-[11px] text-fg-tertiary line-clamp-1">
                            {p.one_liner}
                          </div>
                        </div>
                      </button>
                    );
                  })}
                </div>
                {!showAllPresets && (presets?.length ?? 0) > 8 && (
                  <button
                    type="button"
                    onClick={() => setShowAllPresets(true)}
                    className="mt-2 text-xs text-fg-tertiary hover:text-accent transition-colors"
                  >
                    Показать все стили ({presets?.length})
                  </button>
                )}
                </div>
              )}

              {/* "Свой вариант" — present on every non-text step */}
              {step.kind !== "text" && (
                <div className="mt-2">
                  {showCustom || ans.custom ? (
                    <input
                      autoFocus
                      value={ans.custom}
                      onChange={(e) => setAns({ custom: e.target.value })}
                      placeholder={
                        step.kind === "style"
                          ? "Свои цвета: напр. тёмно-зелёный + золото, минимализм"
                          : step.kind === "multi"
                            ? "Свой блок — через запятую"
                            : "Свой вариант…"
                      }
                      className="w-full rounded-xl border border-border-default bg-surface-input px-3.5 py-2.5 text-sm text-fg-primary placeholder:text-fg-tertiary focus:outline-none focus:border-[rgba(124,92,255,0.5)] focus:shadow-[0_0_0_4px_rgba(124,92,255,0.10)] transition-all"
                    />
                  ) : (
                    <button
                      type="button"
                      onClick={openCustom}
                      className="flex items-center gap-1.5 text-xs text-fg-tertiary hover:text-accent transition-colors"
                    >
                      <Pencil className="h-3.5 w-3.5" />
                      Свой вариант
                    </button>
                  )}
                </div>
              )}
            </motion.div>
          </AnimatePresence>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between gap-2 px-6 py-4 border-t border-border-subtle">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => go(-1)}
            disabled={stepIdx === 0}
            className="gap-1.5"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            Назад
          </Button>

          {isLast ? (
            <Button type="button" size="sm" onClick={finish} className="gap-1.5 rounded-full px-4">
              <Sparkles className="h-3.5 w-3.5" />
              Создать сайт
            </Button>
          ) : (
            <Button type="button" size="sm" onClick={() => go(1)} className="gap-1.5 rounded-full px-4">
              Далее
              <ArrowRight className="h-3.5 w-3.5" />
            </Button>
          )}
        </div>
      </motion.div>
    </motion.div>
  );
}
