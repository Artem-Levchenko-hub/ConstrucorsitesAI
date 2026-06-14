import * as React from "react";
import { Check, ShieldCheck, Sparkles, Zap } from "lucide-react";

import { cn } from "@/lib/utils";
import { share } from "@/app/omnia-share";

/**
 * Premium split-screen auth chrome — a branded showcase panel beside a clean
 * form. This is the FIRST screen a stranger sees after opening a shared
 * `/p/<slug>` link (the viral entry point: «коллега открыл → за секунды завёл
 * аккаунт → пользуется»), so it has to read like a real product, not a lone
 * card on a grey field.
 *
 * Mobbin pattern (Clay, Mintlify, Typeform, Runway): one side carries the brand
 * — a brand-tinted gradient, the product wordmark, a value proposition and a few
 * proof bullets — the other side carries a quiet, focused form. On < lg the
 * panel collapses to a compact brand band above the form so mobile still lands
 * branded.
 *
 * Brand-aware for free: the gradient is derived from `share.accent` and the
 * wordmark / tagline from `share.title` / `share.tagline` (services/share_meta.py
 * rewrites these per project), so every generated app's auth screen wears its
 * own colours with zero per-app model cost. Server-component-safe (no hooks, no
 * client JS) — the only motion is CSS, reduced-motion aware.
 */

export interface AuthShellProps {
  /** Drives the showcase headline + which proof bullets lead. */
  mode: "signin" | "signup";
  /** Form heading, e.g. «Вход» / «Регистрация» (rendered as the <h1>). */
  title: React.ReactNode;
  /** The cross-link line under the title, e.g. «Нет аккаунта? Зарегистрироваться». */
  subtitle?: React.ReactNode;
  /** The form (and any error banner). Sits in a centered max-w-sm column. */
  children: React.ReactNode;
}

interface ProofPoint {
  icon: React.ReactNode;
  text: string;
}

const PROOF: ProofPoint[] = [
  { icon: <Zap />, text: "Готовое рабочее приложение — без установки и настройки" },
  { icon: <ShieldCheck />, text: "Личный кабинет: ваши данные под защитой и только ваши" },
  { icon: <Sparkles />, text: "Работает на телефоне, планшете и компьютере" },
];

/** A deep, brand-tinted gradient for the showcase panel. `accent` is a hex from
 *  the project's share payload; we ride it from a light top to a near-black
 *  bottom so light/dark accents both stay legible under white text. */
function panelGradient(accent: string): string {
  return [
    "linear-gradient(155deg,",
    `color-mix(in oklab, ${accent}, white 10%) 0%,`,
    `${accent} 34%,`,
    `color-mix(in oklab, ${accent}, #060810 86%) 100%)`,
  ].join(" ");
}

function Showcase({ mode, accent }: { mode: "signin" | "signup"; accent: string }) {
  const brand = share.title && share.title !== "Omnia project" ? share.title : "Omnia";
  const headline =
    mode === "signup"
      ? "Создайте аккаунт за секунды"
      : "С возвращением";
  const lead =
    share.tagline ||
    (mode === "signup"
      ? "Присоединяйтесь — всё уже работает из коробки."
      : "Войдите, чтобы продолжить работу.");

  return (
    <div
      className="relative isolate hidden flex-col justify-between overflow-hidden p-10 text-white lg:flex xl:p-14"
      style={{ background: panelGradient(accent) }}
    >
      {/* Legibility scrim + two soft brand glows (slow float, reduced-motion safe). */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 bg-[radial-gradient(120%_90%_at_50%_120%,rgba(0,0,0,0.45),transparent_60%)]"
      />
      <div
        aria-hidden
        className="pointer-events-none absolute -right-24 -top-24 size-80 rounded-full bg-white/15 blur-3xl animate-pulse motion-reduce:animate-none"
      />
      <div
        aria-hidden
        className="pointer-events-none absolute -bottom-28 -left-20 size-72 rounded-full bg-white/10 blur-3xl animate-pulse [animation-delay:1.2s] motion-reduce:animate-none"
      />

      {/* Wordmark */}
      <div className="relative flex items-center gap-2.5">
        <span className="grid size-9 place-items-center rounded-xl bg-white/15 ring-1 ring-inset ring-white/25 backdrop-blur-sm">
          <Sparkles className="size-5" />
        </span>
        <span className="text-lg font-semibold tracking-tight">{brand}</span>
      </div>

      {/* Value proposition + proof bullets */}
      <div className="relative max-w-md">
        <h2 className="text-3xl font-semibold leading-tight tracking-tight text-balance xl:text-4xl">
          {headline}
        </h2>
        <p className="mt-3 text-base leading-relaxed text-white/80">{lead}</p>

        <ul className="mt-9 space-y-4">
          {PROOF.map((p) => (
            <li key={p.text} className="flex items-start gap-3">
              <span className="mt-0.5 grid size-7 shrink-0 place-items-center rounded-lg bg-white/15 ring-1 ring-inset ring-white/20 [&_svg]:size-4">
                {p.icon}
              </span>
              <span className="text-sm leading-relaxed text-white/90">{p.text}</span>
            </li>
          ))}
        </ul>
      </div>

      <p className="relative text-xs text-white/55">Создано на Omnia.AI</p>
    </div>
  );
}

/** Compact branded band shown above the form on mobile (the full Showcase is
 *  desktop-only). Keeps the brand present without eating the small viewport. */
function MobileBrand({ accent }: { accent: string }) {
  const brand = share.title && share.title !== "Omnia project" ? share.title : "Omnia";
  return (
    <div
      className="mb-8 flex items-center gap-2.5 rounded-2xl px-4 py-3 text-white lg:hidden"
      style={{ background: panelGradient(accent) }}
    >
      <span className="grid size-8 place-items-center rounded-lg bg-white/15 ring-1 ring-inset ring-white/25 [&_svg]:size-4">
        <Sparkles />
      </span>
      <span className="truncate text-sm font-semibold tracking-tight">{brand}</span>
    </div>
  );
}

export function AuthShell({ mode, title, subtitle, children }: AuthShellProps) {
  const accent = share.accent || "#6366f1";
  return (
    <main className="grid min-h-screen lg:grid-cols-[1.05fr_1fr] xl:grid-cols-[1.1fr_1fr]">
      <Showcase mode={mode} accent={accent} />

      <div className="flex items-center justify-center bg-background px-6 py-12 sm:px-10">
        <div className="fade-up w-full max-w-sm">
          <MobileBrand accent={accent} />
          <header className="mb-6 space-y-1">
            <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
            {subtitle ? (
              <p className="text-sm text-muted-foreground">{subtitle}</p>
            ) : null}
          </header>
          {children}
        </div>
      </div>
    </main>
  );
}

/** Re-export so generated code can compose its own proof rows if it wants to,
 *  e.g. a check-marked feature list inside the form column. */
export function AuthProof({ text, className }: { text: string; className?: string }) {
  return (
    <span className={cn("flex items-start gap-2 text-sm text-muted-foreground", className)}>
      <Check className="mt-0.5 size-4 shrink-0 text-success" />
      {text}
    </span>
  );
}
