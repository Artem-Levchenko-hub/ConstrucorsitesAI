import * as React from "react";
import Link from "next/link";

import { cn } from "@/lib/utils";
import { Card } from "@/components/ui/card";
import { buttonVariants } from "@/components/ui/button";

/* Setup-guide checklist — the onboarding gamification card every fresh app
 * deserves on its dashboard (Mobbin refs: Vanta «Starter Guide 6/20», Hootsuite
 * «Getting Started 4/6», HoneyBook «Check off the steps»). An app with empty
 * data shows the first steps to take instead of a dead screen; each step links
 * to the action that completes it, a progress ring + «N/M выполнено» pulls the
 * user toward 100%, and finishing flips the card to a celebratory reward state.
 *
 * Deep, narrow interface (R-01) built on the existing ui kit (R-04):
 *
 *   <SetupChecklist
 *     title="С чего начать"
 *     steps={[
 *       { label: "Заполните профиль", done: true, action: { label: "Профиль", href: "/dashboard/settings" } },
 *       { label: "Добавьте первый товар", description: "Витрина оживёт", action: { label: "Добавить", href: "/dashboard/products/new" } },
 *       { label: "Пригласите коллегу", done: false },
 *     ]}
 *   />
 *
 * Presentational and server-renderable (no hooks): progress is derived from the
 * `done` flags, so it works in an RSC dashboard with JS off.
 */

/** A step's call-to-action. JSX is canonical, but the generator often reaches
 *  for a `{ label, href }` shorthand — accept it too and render a styled link,
 *  so a dashboard never crashes on "Objects are not valid as a React child"
 *  (tolerant reader, R-10). */
export type ChecklistAction = React.ReactNode | { label: string; href: string };

export interface ChecklistStep {
  label: React.ReactNode;
  /** Helper line under the label. */
  description?: React.ReactNode;
  /** Whether this step is completed. */
  done?: boolean;
  /** Where the step leads when still pending (the next action). Hidden once
   *  `done`. */
  action?: ChecklistAction;
}

export interface SetupChecklistProps {
  steps: ChecklistStep[];
  /** Defaults to “С чего начать”. */
  title?: React.ReactNode;
  description?: React.ReactNode;
  /** Copy shown in the header + reward state once every step is done. Defaults
   *  to “Всё готово 🎉”. */
  doneLabel?: React.ReactNode;
  className?: string;
}

function isActionLink(a: unknown): a is { label: string; href: string } {
  return (
    typeof a === "object" &&
    a !== null &&
    "label" in a &&
    "href" in a &&
    typeof (a as { label: unknown }).label === "string" &&
    typeof (a as { href: unknown }).href === "string"
  );
}

function CheckIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={3}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className="size-3.5"
    >
      <path d="M5 12.5l4.5 4.5L19 6.5" />
    </svg>
  );
}

/** Compact progress ring reusing the dashboard conic-gradient idiom (see
 *  `DonutStat` in charts) so the kit reads as one system. Shows «done/total» in
 *  the centre. */
function ProgressRing({ done, total }: { done: number; total: number }) {
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;
  const complete = total > 0 && done === total;
  const size = 60;
  return (
    <div
      role="img"
      aria-label={`Выполнено ${done} из ${total}`}
      className={cn(
        "relative grid shrink-0 place-items-center rounded-full",
        complete ? "text-primary" : "text-primary/90",
      )}
      style={{
        width: size,
        height: size,
        background: `conic-gradient(currentColor ${pct}%, var(--muted) ${pct}% 100%)`,
      }}
    >
      <div className="grid place-items-center rounded-full bg-card text-center" style={{ width: size - 12, height: size - 12 }}>
        {complete ? (
          <span className="text-primary">
            <CheckIcon />
          </span>
        ) : (
          <span className="text-xs font-semibold tabular-nums leading-none text-foreground">
            {done}
            <span className="text-muted-foreground">/{total}</span>
          </span>
        )}
      </div>
    </div>
  );
}

function StepActionNode({ action }: { action: ChecklistAction }) {
  if (isActionLink(action)) {
    return (
      <Link
        href={action.href}
        className={cn(buttonVariants({ variant: "ghost", size: "sm" }), "text-primary")}
      >
        {action.label}
        <span aria-hidden="true">→</span>
      </Link>
    );
  }
  return <>{action}</>;
}

/** A getting-started checklist for fresh, empty apps. Renders a progress ring,
 *  the list of first steps (done = filled check, pending = open circle + CTA),
 *  and a reward header once all steps are complete. */
export function SetupChecklist({
  steps,
  title,
  description,
  doneLabel,
  className,
}: SetupChecklistProps) {
  const total = steps.length;
  const done = steps.filter((s) => s.done).length;
  const allDone = total > 0 && done === total;

  return (
    <Card className={cn("gap-0 overflow-hidden py-0", className)}>
      <div className="flex items-center gap-4 border-b border-border px-5 py-4 sm:px-6">
        <ProgressRing done={done} total={total} />
        <div className="min-w-0 space-y-0.5">
          <h2 className="text-base font-semibold tracking-tight">
            {allDone ? (doneLabel ?? "Всё готово 🎉") : (title ?? "С чего начать")}
          </h2>
          <p className="text-sm text-muted-foreground">
            {allDone ? (
              "Все первые шаги выполнены — отличный старт."
            ) : (
              <>
                {description ?? "Несколько шагов — и приложение готово к работе."}{" "}
                <span className="font-medium text-foreground">
                  {done}/{total} выполнено
                </span>
              </>
            )}
          </p>
        </div>
      </div>

      <ol className="divide-y divide-border px-5 sm:px-6">
        {steps.map((step, i) => (
          <li
            key={i}
            className="flex items-center gap-3 py-3.5 sm:gap-4"
          >
            <span
              aria-hidden="true"
              className={cn(
                "grid size-6 shrink-0 place-items-center rounded-full border transition-colors",
                step.done
                  ? "border-primary bg-primary text-primary-foreground"
                  : "border-muted-foreground/30 text-transparent",
              )}
            >
              <CheckIcon />
            </span>
            <div className="min-w-0 flex-1 space-y-0.5">
              <p
                className={cn(
                  "text-sm font-medium leading-snug",
                  step.done && "text-muted-foreground line-through decoration-muted-foreground/40",
                )}
              >
                {step.label}
              </p>
              {step.description ? (
                <p className="text-xs text-muted-foreground">{step.description}</p>
              ) : null}
            </div>
            <div className="shrink-0">
              {step.done ? (
                <span className="text-xs font-medium text-muted-foreground">Готово</span>
              ) : step.action ? (
                <StepActionNode action={step.action} />
              ) : null}
            </div>
          </li>
        ))}
      </ol>
    </Card>
  );
}
