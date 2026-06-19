/**
 * Default landing for a freshly provisioned fullstack project.
 *
 * AI replaces this file when the user sends their first prompt. Until then it is
 * the FIRST frame the iframe renders in the Omnia workspace (and the fallback a
 * stranger sees on a shared `/p/<slug>` before generation), so it has to read
 * like a real product — not a bare shadcn paragraph on a grey field.
 *
 * Self-contained on purpose: the drizzle template ships no `omnia/` kit and no
 * `--primary` token system, so the whole screen wears the project's colour
 * through `share.accent` (pinned as `--brand` / `--brand-fg`) with zero per-app
 * model cost — exactly like the split-screen auth chrome it sits beside.
 * Server-component-safe: no hooks, no client JS, CSS-only motion, reduced-motion
 * aware.
 */
import type { ReactNode } from "react";
import {
  ArrowRight,
  Database,
  Smartphone,
  Sparkles,
  Wand2,
  ShieldCheck,
} from "lucide-react";

import { share } from "@/app/omnia-share";
import { brandName, brandTokens } from "@/lib/brand";

interface Feature {
  icon: ReactNode;
  title: string;
  body: string;
}

const FEATURES: Feature[] = [
  {
    icon: <ShieldCheck />,
    title: "Личный кабинет",
    body: "Вход, регистрация и доступ под защитой — данные каждого пользователя только его.",
  },
  {
    icon: <Database />,
    title: "Postgres из коробки",
    body: "Таблицы, связи и миграции готовы. AI добавит сущности по вашему промпту.",
  },
  {
    icon: <Smartphone />,
    title: "Работает везде",
    body: "Адаптивный интерфейс на телефоне, планшете и компьютере — без настройки.",
  },
];

export default function Home() {
  const accent = share.accent || "#6366f1";
  const name = brandName();
  const lead =
    share.tagline ||
    "Стартовый шаблон Next.js + Postgres готов. Напишите промпт слева — AI добавит страницы, таблицы и логику. Перезагрузка не нужна: всё подхватится вживую.";

  return (
    <main
      className="dark relative isolate min-h-screen overflow-hidden bg-zinc-950 text-zinc-100"
      style={brandTokens(accent)}
    >
      {/* Brand-tinted aurora over the near-black canvas (reduced-motion safe). */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 -z-10 bg-[radial-gradient(110%_80%_at_50%_-10%,color-mix(in_oklab,var(--brand),transparent_55%),transparent_60%)] opacity-70"
      />
      <div
        aria-hidden
        className="pointer-events-none absolute -right-40 top-1/4 -z-10 size-[36rem] rounded-full bg-[var(--brand)] opacity-20 blur-[140px] animate-pulse motion-reduce:animate-none"
      />
      <div
        aria-hidden
        className="pointer-events-none absolute -left-48 bottom-0 -z-10 size-[32rem] rounded-full bg-[var(--brand)] opacity-10 blur-[160px] animate-pulse [animation-delay:1.4s] motion-reduce:animate-none"
      />

      {/* Top bar */}
      <header className="mx-auto flex w-full max-w-6xl items-center justify-between px-6 py-6">
        <div className="flex items-center gap-2.5">
          <span className="grid size-9 place-items-center rounded-xl bg-[color-mix(in_oklab,var(--brand),transparent_78%)] text-[var(--brand)] ring-1 ring-inset ring-white/10">
            <Sparkles className="size-5" />
          </span>
          <span className="text-lg font-semibold tracking-tight">{name}</span>
        </div>
        <nav className="flex items-center gap-2">
          <a
            href="/signin"
            className="hidden rounded-lg px-4 py-2 text-sm font-medium text-zinc-300 transition hover:text-white sm:inline-block"
          >
            Войти
          </a>
          <a
            href="/signup"
            className="whitespace-nowrap rounded-lg bg-[var(--brand)] px-4 py-2 text-sm font-semibold text-[var(--brand-fg)] shadow-sm transition hover:brightness-110"
          >
            Создать аккаунт
          </a>
        </nav>
      </header>

      {/* Hero */}
      <section className="mx-auto flex w-full max-w-3xl flex-col items-center px-6 pb-20 pt-16 text-center sm:pt-24">
        <span className="fade-up inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/5 px-3.5 py-1.5 text-xs font-medium uppercase tracking-widest text-zinc-300 backdrop-blur-sm">
          <span className="size-1.5 rounded-full bg-[var(--brand)] shadow-[0_0_12px_var(--brand)]" />
          Готово к работе
        </span>

        <h1 className="fade-up delay-1 mt-7 text-balance text-5xl font-semibold leading-[1.05] tracking-tight sm:text-6xl">
          {name === "Omnia" ? (
            <>
              Новый проект,
              <br className="hidden sm:block" />{" "}
              <span className="bg-[linear-gradient(90deg,var(--brand),color-mix(in_oklab,var(--brand),white_55%))] bg-clip-text text-transparent">
                собранный за минуты
              </span>
            </>
          ) : (
            <>
              {name}{" "}
              <span className="bg-[linear-gradient(90deg,var(--brand),color-mix(in_oklab,var(--brand),white_55%))] bg-clip-text text-transparent">
                готов к работе
              </span>
            </>
          )}
        </h1>

        <p className="fade-up delay-2 mt-6 max-w-xl text-balance text-lg leading-relaxed text-zinc-400">
          {lead}
        </p>

        <div className="fade-up delay-3 mt-9 flex flex-col items-center gap-3 sm:flex-row">
          <a
            href="/signup"
            className="hover-lift inline-flex items-center justify-center gap-2 rounded-xl bg-[var(--brand)] px-6 py-3 text-sm font-semibold text-[var(--brand-fg)] shadow-lg shadow-[color-mix(in_oklab,var(--brand),transparent_70%)] transition hover:brightness-110"
          >
            Начать
            <ArrowRight className="size-4" />
          </a>
          <a
            href="/signin"
            className="inline-flex items-center justify-center gap-2 rounded-xl border border-white/15 bg-white/5 px-6 py-3 text-sm font-semibold text-zinc-100 backdrop-blur-sm transition hover:bg-white/10"
          >
            Войти в кабинет
          </a>
        </div>

        <p className="fade-up delay-3 mt-5 inline-flex items-center gap-1.5 text-xs text-zinc-500">
          <Wand2 className="size-3.5" />
          Напишите промпт слева — приложение растёт вживую
        </p>
      </section>

      {/* Feature trio */}
      <section className="mx-auto grid w-full max-w-5xl gap-4 px-6 pb-24 sm:grid-cols-3">
        {FEATURES.map((f, i) => (
          <div
            key={f.title}
            className={`fade-up delay-${i + 1} rounded-2xl border border-white/10 bg-white/[0.03] p-6 backdrop-blur-sm transition hover:border-white/20 hover:bg-white/[0.05]`}
          >
            <span className="grid size-10 place-items-center rounded-xl bg-[color-mix(in_oklab,var(--brand),transparent_82%)] text-[var(--brand)] ring-1 ring-inset ring-white/10 [&_svg]:size-5">
              {f.icon}
            </span>
            <h3 className="mt-4 text-base font-semibold tracking-tight text-white">
              {f.title}
            </h3>
            <p className="mt-1.5 text-sm leading-relaxed text-zinc-400">{f.body}</p>
          </div>
        ))}
      </section>

      {/* Footer */}
      <footer className="mx-auto flex w-full max-w-6xl items-center justify-between border-t border-white/5 px-6 py-6 text-xs text-zinc-500">
        <span>Создано на Omnia.AI</span>
        <code className="font-mono text-zinc-600">nextjs-postgres-drizzle</code>
      </footer>
    </main>
  );
}
