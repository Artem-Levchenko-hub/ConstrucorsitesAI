"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useMutation } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  Sparkles,
  ArrowUp,
  Loader2,
  FileCode2,
  TerminalSquare,
  Globe,
  ScrollText,
} from "lucide-react";
import { createProject } from "@/lib/api/projects";

// What the agent can actually do — the "full access to the environment" promise,
// shown as capability chips so the user understands this is an agent, not a form.
const CAPS = [
  { icon: FileCode2, label: "Читает и пишет файлы" },
  { icon: TerminalSquare, label: "Запускает команды" },
  { icon: ScrollText, label: "Читает логи, чинит ошибки" },
  { icon: Globe, label: "Любой стек, любая задача" },
];

const EXAMPLES = [
  "Собери CRM для автосервиса: клиенты, заказы, склад",
  "Сделай real-time мессенджер с комнатами и присутствием",
  "Напиши Python-парсер цен конкурентов с выгрузкой в CSV",
  "Дашборд аналитики с графиками и фильтрами по датам",
];

/**
 * The `/deep-research` entry — a focused "land in a cloud Claude Code" screen.
 * The user describes ANY task; we create a fresh project (template "blank" — the
 * backend's discovery auto-routes the stack) and navigate into the workspace
 * with the task pre-loaded (`?p=`), where the agentic builder picks it up with
 * full tools and a live transcript. No template picker, no wizard — just a prompt
 * and the agent.
 */
export function DeepResearchEntry() {
  const router = useRouter();
  const [prompt, setPrompt] = useState("");

  const { mutate, isPending } = useMutation({
    mutationFn: createProject,
    onSuccess: (project) => {
      router.push(
        `/projects/${project.id}?p=${encodeURIComponent(prompt.trim())}`,
      );
    },
    onError: (e) => {
      toast.error("Не удалось запустить агента", {
        description: e instanceof Error ? e.message : undefined,
      });
    },
  });

  const launch = () => {
    const text = prompt.trim();
    if (!text || isPending) return;
    mutate({ name: text.slice(0, 48), template: "blank" });
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      launch();
    }
  };

  return (
    <div className="flex-1 min-h-0 overflow-y-auto">
      <div className="mx-auto flex w-full max-w-3xl flex-col px-6 py-16 sm:py-24">
        <div className="inline-flex items-center gap-2 self-start rounded-full border border-border-subtle bg-surface-raised/60 px-3 py-1 text-[12px] font-mono text-fg-tertiary">
          <span className="inline-block h-1.5 w-1.5 rounded-full bg-accent" />
          Облачный Claude Code
        </div>

        <h1 className="mt-5 text-balance text-[clamp(30px,4.5vw,46px)] font-semibold leading-[1.05] tracking-tight text-fg-primary">
          Агент с полным доступом к среде.
        </h1>
        <p className="mt-4 max-w-xl text-[16px] leading-relaxed text-fg-secondary">
          Опиши задачу — агент сам читает и пишет файлы, запускает команды,
          смотрит логи, чинит ошибки и доводит результат до рабочего. Ты видишь
          каждый его шаг вживую.
        </p>

        <div className="mt-6 flex flex-wrap gap-2">
          {CAPS.map((c) => (
            <span
              key={c.label}
              className="inline-flex items-center gap-1.5 rounded-lg border border-border-subtle bg-surface-overlay/50 px-2.5 py-1 text-[12px] text-fg-secondary"
            >
              <c.icon className="h-3.5 w-3.5 text-accent" />
              {c.label}
            </span>
          ))}
        </div>

        <div className="mt-8 rounded-2xl border border-border-default bg-surface-raised transition-colors focus-within:border-accent/50">
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            onKeyDown={onKeyDown}
            rows={4}
            autoFocus
            placeholder="Что построить или сделать? Например: собери дашборд заявок с фильтрами и экспортом в Excel…"
            className="w-full resize-none bg-transparent px-4 py-3.5 text-[15px] leading-relaxed text-fg-primary placeholder:text-fg-tertiary focus:outline-none"
          />
          <div className="flex items-center justify-between gap-3 px-3 pb-3">
            <span className="text-[11px] text-fg-tertiary">
              Enter — запустить · Shift+Enter — новая строка
            </span>
            <button
              type="button"
              onClick={launch}
              disabled={!prompt.trim() || isPending}
              className="inline-flex items-center gap-2 rounded-xl bg-accent px-4 py-2 text-sm font-semibold text-accent-fg transition-[transform,opacity] hover:opacity-90 active:scale-[0.98] disabled:opacity-40"
            >
              {isPending ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Запускаю…
                </>
              ) : (
                <>
                  <ArrowUp className="h-4 w-4" />
                  Запустить агента
                </>
              )}
            </button>
          </div>
        </div>

        <div className="mt-6 flex items-center gap-2 text-[12px] text-fg-tertiary">
          <Sparkles className="h-3.5 w-3.5 text-accent" />
          Примеры
        </div>
        <div className="mt-2 flex flex-wrap gap-2">
          {EXAMPLES.map((ex) => (
            <button
              key={ex}
              type="button"
              onClick={() => setPrompt(ex)}
              disabled={isPending}
              className="rounded-lg border border-border-subtle bg-surface-overlay/40 px-3 py-1.5 text-left text-[13px] text-fg-secondary transition-colors hover:border-border-default hover:text-fg-primary disabled:opacity-50"
            >
              {ex}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
