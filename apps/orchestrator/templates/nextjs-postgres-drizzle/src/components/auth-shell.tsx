import * as React from "react";
import { Check, ShieldCheck, Sparkles, Zap } from "lucide-react";

import { share } from "@/app/omnia-share";
import { brandName, brandTokens, panelGradient } from "@/lib/brand";

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
 * Self-contained on purpose: the drizzle template ships NO shadcn token system
 * (no `--primary` / `--background` CSS vars) and no `omnia/` kit, so unlike the
 * nextjs-entities AuthShell this one depends on nothing but Tailwind + lucide +
 * `share.accent`. The brand accent is pinned as a local `--brand` / `--brand-fg`
 * pair on the root and read through Tailwind arbitrary values, so the whole
 * screen (form inputs' focus ring + the submit button) wears the project's
 * colour with zero per-app model cost. Server-component-safe (no hooks, no
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

function Showcase({ mode, accent }: { mode: "signin" | "signup"; accent: string }) {
  const headline =
    mode === "signup" ? "Создайте аккаунт за секунды" : "С возвращением";
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
        <span className="text-lg font-semibold tracking-tight">{brandName()}</span>
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
  return (
    <div
      className="mb-8 flex items-center gap-2.5 rounded-2xl px-4 py-3 text-white lg:hidden"
      style={{ background: panelGradient(accent) }}
    >
      <span className="grid size-8 place-items-center rounded-lg bg-white/15 ring-1 ring-inset ring-white/25 [&_svg]:size-4">
        <Sparkles />
      </span>
      <span className="truncate text-sm font-semibold tracking-tight">{brandName()}</span>
    </div>
  );
}

export function AuthShell({ mode, title, subtitle, children }: AuthShellProps) {
  const accent = share.accent || "#6366f1";
  return (
    <main
      className="grid min-h-screen bg-white text-zinc-900 lg:grid-cols-[1.05fr_1fr] xl:grid-cols-[1.1fr_1fr]"
      style={brandTokens(accent)}
    >
      <Showcase mode={mode} accent={accent} />

      <div className="flex items-center justify-center px-6 py-12 sm:px-10">
        <div className="fade-up w-full max-w-sm">
          <MobileBrand accent={accent} />
          <header className="mb-6 space-y-1">
            <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
            {subtitle ? (
              <p className="text-sm text-zinc-500">{subtitle}</p>
            ) : null}
          </header>
          {children}
        </div>
      </div>
    </main>
  );
}

/** A labelled text input, styled premium + brand-aware focus. Server-safe. */
export function AuthField({
  label,
  hint,
  ...props
}: { label: string; hint?: string } & React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <label className="block space-y-1.5">
      <span className="text-sm font-medium text-zinc-700">{label}</span>
      <input
        {...props}
        className="w-full rounded-lg border border-zinc-300 bg-white px-3 py-2.5 text-sm text-zinc-900 shadow-sm transition placeholder:text-zinc-400 focus:border-[var(--brand)] focus:outline-none focus:ring-2 focus:ring-[color-mix(in_oklab,var(--brand),transparent_70%)]"
      />
      {hint ? <span className="block text-xs text-zinc-500">{hint}</span> : null}
    </label>
  );
}

/** The on-brand primary submit button. Server-safe. */
export function AuthSubmit({ children }: { children: React.ReactNode }) {
  return (
    <button
      type="submit"
      className="w-full rounded-lg bg-[var(--brand)] px-4 py-2.5 text-sm font-semibold text-[var(--brand-fg)] shadow-sm transition hover:brightness-95 focus:outline-none focus:ring-2 focus:ring-[color-mix(in_oklab,var(--brand),transparent_55%)] focus:ring-offset-2"
    >
      {children}
    </button>
  );
}

/** Re-export so generated code can compose its own proof rows if it wants to. */
export function AuthProof({ text }: { text: string }) {
  return (
    <span className="flex items-start gap-2 text-sm text-zinc-600">
      <Check className="mt-0.5 size-4 shrink-0 text-emerald-600" />
      {text}
    </span>
  );
}
