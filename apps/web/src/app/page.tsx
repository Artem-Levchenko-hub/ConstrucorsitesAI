import Link from "next/link";
import {
  ArrowRight,
  Play,
  Check,
  Globe,
  Database,
  Server,
  Code2,
  Zap,
  GitBranch,
  FileCode,
  Layers,
  Cpu,
} from "lucide-react";
import { getTranslations } from "next-intl/server";

import { Reveal } from "@/components/marketing/Reveal";
import { WordReveal } from "@/components/marketing/WordReveal";
import { LocaleSwitcher } from "@/components/LocaleSwitcher";

export async function generateMetadata() {
  const t = await getTranslations("meta");
  return {
    title: t("landingTitle"),
    description: t("description"),
  };
}

export default async function LandingPage() {
  const tNav = await getTranslations("landing.nav");
  const tHero = await getTranslations("landing.hero");

  return (
    <div className="min-h-svh bg-bg-base text-label-1 font-sans antialiased">
      <Nav tNav={tNav} tHero={tHero} />
      <main>
        <Hero tHero={tHero} />
        <LiveDemoStrip />
        <StackCarousel />
        <WorkspaceShowcase />
        <CaseStudies />
        <Pricing />
        <Faq />
      </main>
      <Footer />
    </div>
  );
}

type NavTranslations = Awaited<ReturnType<typeof getTranslations<"landing.nav">>>;
type HeroTranslations = Awaited<ReturnType<typeof getTranslations<"landing.hero">>>;

function Nav({
  tNav,
  tHero,
}: {
  tNav: NavTranslations;
  tHero: HeroTranslations;
}) {
  void tHero; // passed from parent, only tNav used here

  return (
    <header className="sticky top-0 z-50 material-thin border-b border-separator">
      <div className="max-w-7xl mx-auto px-6 lg:px-12 h-16 flex items-center justify-between">
        <Link
          href="/"
          className="flex items-center gap-2.5 font-semibold tracking-tight"
        >
          <span className="inline-block h-7 w-7 rounded-sm bg-accent" />
          <span className="text-[17px]">
            Omnia<span className="text-label-2">.AI</span>
          </span>
        </Link>

        <nav className="hidden md:flex items-center gap-7 text-[14px] text-label-2">
          <a href="#stacks" className="hover:text-label-1 transition-colors">
            {tNav("stacks")}
          </a>
          <a href="#workspace" className="hover:text-label-1 transition-colors">
            {tNav("workspace")}
          </a>
          <a href="#cases" className="hover:text-label-1 transition-colors">
            {tNav("examples")}
          </a>
          <a href="#pricing" className="hover:text-label-1 transition-colors">
            {tNav("pricing")}
          </a>
          <a href="#faq" className="hover:text-label-1 transition-colors">
            {tNav("faq")}
          </a>
        </nav>

        <div className="flex items-center gap-3">
          <LocaleSwitcher />
          <Link
            href="/login"
            className="text-[14px] text-label-2 hover:text-label-1 transition-colors"
          >
            {tNav("login")}
          </Link>
          <Link
            href="/register"
            className="inline-flex items-center gap-1.5 px-4 h-9 rounded-full bg-accent text-accent-fg text-[14px] font-medium hover:bg-accent-hover active:scale-[0.98] transition-transform"
          >
            {tNav("createProject")}
            <ArrowRight className="h-3.5 w-3.5" strokeWidth={2} />
          </Link>
        </div>
      </div>
    </header>
  );
}

function Hero({ tHero }: { tHero: HeroTranslations }) {
  return (
    <section className="relative max-w-7xl mx-auto px-6 lg:px-12 pt-20 lg:pt-28 pb-20 lg:pb-24 overflow-hidden">
      <div className="hero-glow left-[-10%] top-[6%] h-[460px] w-[460px]" aria-hidden />
      <div className="hero-glow right-[-8%] top-[26%] h-[400px] w-[400px] [animation-delay:-7s]" aria-hidden />
      <div className="relative z-10 grid lg:grid-cols-12 gap-12 lg:gap-16 items-center">
        <div className="lg:col-span-7 space-y-7">
          <Reveal>
            <div className="inline-flex items-center gap-2 px-3 h-7 rounded-full border border-separator text-[12px] font-mono text-label-2 tabular-nums">
              <span className="inline-block h-1.5 w-1.5 rounded-full bg-system-green" />
              {tHero("badge")}
            </div>
          </Reveal>

          <h1 className="text-[clamp(40px,5.8vw,76px)] leading-[0.98] tracking-[-0.035em] font-semibold text-balance">
            <WordReveal text={tHero("line1")} className="block" />
            <WordReveal
              text={tHero("line2")}
              className="block"
              baseDelay={0.18}
            />
            <WordReveal
              text={tHero("line3")}
              className="block text-accent"
              baseDelay={0.36}
            />
          </h1>

          <Reveal delay={0.12}>
            <p className="text-[17px] leading-[1.55] text-label-2 max-w-xl">
              {tHero("subtitle")}
            </p>
          </Reveal>

          <Reveal delay={0.2}>
            <div className="flex flex-wrap gap-3 pt-2">
              <Link
                href="/register"
                className="inline-flex items-center gap-2 h-12 px-6 rounded-full bg-accent text-accent-fg font-medium hover:bg-accent-hover active:scale-[0.98] transition-transform"
              >
                {tHero("ctaPrimary")}
                <ArrowRight className="h-4 w-4" strokeWidth={2} />
              </Link>
              <a
                href="#demo"
                className="inline-flex items-center gap-2 h-12 px-6 rounded-full border border-separator-solid text-label-1 hover:border-label-3 active:scale-[0.98] transition-transform"
              >
                <Play className="h-4 w-4" strokeWidth={1.75} />
                {tHero("ctaDemo")}
              </a>
            </div>
          </Reveal>

          <Reveal delay={0.28}>
            <div className="pt-6 text-[11px] font-mono uppercase tracking-[0.15em] text-label-3 tabular-nums">
              beeline · яндекс edu · skyeng · tinkoff · x5
            </div>
          </Reveal>
        </div>

        <Reveal delay={0.18} className="lg:col-span-5">
          <WorkspaceMockCard />
        </Reveal>
      </div>
    </section>
  );
}

function WorkspaceMockCard() {
  return (
    <div className="rounded-xl overflow-hidden border border-separator bg-bg-elevated-1 aspect-[16/11]">
      <div className="material-thin border-b border-separator h-9 flex items-center justify-between px-3 text-[11px] font-mono text-label-2">
        <div className="flex items-center gap-2">
          <span className="inline-block h-2 w-2 rounded-full bg-system-green" />
          <span className="text-label-1">cafe-polet</span>
          <span className="text-label-3">/</span>
          <span>main</span>
        </div>
        <div className="hidden md:flex items-center gap-2.5 tabular-nums">
          <span>claude 4.5 · 12/50</span>
          <span className="text-label-3">·</span>
          <span>ru-1</span>
          <span className="text-label-3">·</span>
          <span className="text-accent">12 480 ₽</span>
        </div>
      </div>

      <div className="grid grid-cols-[1fr_1.6fr_0.85fr] h-[calc(100%-2.25rem-1.25rem)]">
        <div className="border-r border-separator p-3 space-y-2 text-[11px] overflow-hidden">
          <div className="rounded-md px-2 py-1.5 bg-accent text-accent-fg max-w-[80%] ml-auto">
            лендинг кофейни в питере
          </div>
          <div className="rounded-md px-2 py-1.5 bg-surface text-label-1 max-w-[92%]">
            создаю apps/cafe-spb · 7 файлов
          </div>
          <div className="flex gap-1.5 flex-wrap pt-0.5">
            <span className="px-1.5 py-0.5 rounded-sm text-[9.5px] font-mono border border-separator text-label-2">
              next.js · routes
            </span>
            <span className="px-1.5 py-0.5 rounded-sm text-[9.5px] font-mono border border-separator text-label-2">
              ui · 4 sec
            </span>
          </div>
        </div>

        <div className="bg-bg-base p-2">
          <div className="rounded-md bg-bg-elevated-1 border border-separator h-full flex flex-col overflow-hidden">
            <div className="material-ultrathin flex items-center gap-1.5 px-2.5 h-6 border-b border-separator">
              <span className="inline-block h-2 w-2 rounded-full bg-[#FF5F57]" />
              <span className="inline-block h-2 w-2 rounded-full bg-[#FEBC2E]" />
              <span className="inline-block h-2 w-2 rounded-full bg-[#28C840]" />
              <div className="flex-1 mx-2 h-4 rounded-sm bg-bg-base/60 text-[9px] font-mono text-label-3 flex items-center justify-center">
                cafe-polet.omnia.app
              </div>
              <span className="text-[9px] font-mono text-system-green flex items-center gap-1">
                <span className="inline-block h-1.5 w-1.5 rounded-full bg-system-green" />
                live
              </span>
            </div>
            <div className="flex-1 p-3 flex flex-col items-center justify-center gap-1.5 text-center">
              <div className="text-[14px] font-semibold tracking-tight">
                Cafe Polet
              </div>
              <div className="text-[9.5px] text-label-2">
                крафтовая обжарка · спб
              </div>
              <div className="mt-1 px-2.5 h-5 inline-flex items-center rounded-full bg-accent text-accent-fg text-[9px] font-medium">
                забронировать
              </div>
            </div>
          </div>
        </div>

        <div className="border-l border-separator p-2 space-y-1 text-[10px]">
          {[
            { v: "v1.2", label: "hero", active: true },
            { v: "v1.1", label: "palette" },
            { v: "v1.0", label: "initial" },
          ].map((s) => (
            <div
              key={s.v}
              className={
                s.active
                  ? "rounded-md px-2 py-1 bg-surface-2 border-l-[3px] border-accent pl-1.5"
                  : "rounded-md px-2 py-1"
              }
            >
              <div className="font-mono text-[10px] tabular-nums text-label-1">
                {s.v}
              </div>
              <div className="text-label-2 text-[9px]">{s.label}</div>
            </div>
          ))}
        </div>
      </div>

      <div className="border-t border-separator h-5 flex items-center px-3 gap-2 text-[10px] font-mono text-label-2 tabular-nums">
        <span className="inline-block h-1.5 w-1.5 rounded-full bg-system-green" />
        <span>building</span>
        <span className="text-label-3">·</span>
        <span>app/page.tsx</span>
        <span className="text-label-3">·</span>
        <span>686 lines</span>
        <span className="text-label-3 ml-auto">next.js · postgres</span>
      </div>
    </div>
  );
}

function LiveDemoStrip() {
  return (
    <section id="demo" className="border-y border-separator bg-bg-elevated-1">
      <div className="max-w-3xl mx-auto px-6 py-20">
        <div className="rounded-xl border border-separator bg-bg-base p-6 font-mono text-[13px] leading-[1.75]">
          <div className="text-label-2">
            <span className="text-label-3">$</span> omnia &gt;{" "}
            <span className="text-accent">Юзер</span>: лендинг для крафтовой
            кофейни в Питере
          </div>
          <div className="text-label-2 mt-1">
            <span className="text-label-3">$</span> omnia &gt; создаю
            apps/cafe-spb…{" "}
            <span className="text-label-3 tabular-nums">[██████░░] 78%</span>
          </div>
          <div className="text-label-2">
            <span className="text-label-3">$</span> omnia &gt;{" "}
            <span className="text-system-green">✓</span> 7 файлов · 686 строк
          </div>
          <div className="text-label-2">
            <span className="text-label-3">$</span> omnia &gt;{" "}
            <span className="text-system-green">✓</span> deploy готов →{" "}
            <span className="text-accent">cafe-spb.omnia.app</span>
            <span className="inline-block w-1.5 h-3.5 bg-label-1 ml-1 align-middle animate-pulse" />
          </div>
        </div>
        <div className="mt-8 flex flex-wrap justify-center gap-2 text-[12px] font-mono tabular-nums text-label-2">
          {[
            "~45 секунд до live preview",
            "9 стеков на выбор",
            "1 клик до prod",
          ].map((c) => (
            <span
              key={c}
              className="px-3 h-7 inline-flex items-center rounded-full border border-separator"
            >
              {c}
            </span>
          ))}
        </div>
      </div>
    </section>
  );
}

const stacks = [
  {
    name: "Next.js + Postgres",
    best: "SaaS, full-stack дашборды",
    tier: "P0",
    icon: Layers,
  },
  {
    name: "Astro Content",
    best: "блоги, контент-сайты",
    tier: "P0",
    icon: FileCode,
  },
  {
    name: "Static HTML",
    best: "лендинги, портфолио",
    tier: "P0",
    icon: Globe,
  },
  {
    name: "SvelteKit + PocketBase",
    best: "MVP с realtime",
    tier: "P1",
    icon: Zap,
  },
  { name: "Vue + Nuxt", best: "PWA, SPA с SSR", tier: "P1", icon: Code2 },
  {
    name: "HTMX + Go",
    best: "минимум JS, max скорость",
    tier: "P1",
    icon: Server,
  },
  {
    name: "FastAPI + React",
    best: "AI приложения, ML",
    tier: "P2",
    icon: Cpu,
  },
  {
    name: "Express + React",
    best: "классический Node стек",
    tier: "P2",
    icon: GitBranch,
  },
  {
    name: "Django + HTMX",
    best: "admin-heavy продукты",
    tier: "P2",
    icon: Database,
  },
];

function StackCarousel() {
  return (
    <section
      id="stacks"
      className="max-w-7xl mx-auto px-6 lg:px-12 py-24 lg:py-32"
    >
      <Reveal className="max-w-3xl mb-12">
        <div className="text-[11px] font-mono uppercase tracking-[0.18em] text-label-3 mb-3">
          02 · стеки
        </div>
        <h2 className="text-[clamp(32px,4vw,52px)] leading-[1.05] tracking-[-0.025em] font-semibold mb-4 text-balance">
          AI сам выбирает стек под идею.
        </h2>
        <p className="text-[17px] leading-[1.55] text-label-2 max-w-xl">
          9 курируемых шаблонов. Описываешь продукт — Omnia выбирает топ-3 с
          обоснованием. Хочешь иначе — override одним кликом.
        </p>
      </Reveal>
      <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
        {stacks.map((s) => (
          <div
            key={s.name}
            className="group rounded-xl border border-separator p-5 bg-bg-elevated-1 hover:border-label-3 hover:-translate-y-1 transition-[transform,border-color] duration-300"
          >
            <div className="flex items-start justify-between mb-4">
              <s.icon
                className="h-5 w-5 text-label-2 group-hover:text-accent transition-colors"
                strokeWidth={1.5}
              />
              <span className="text-[10px] font-mono uppercase tracking-wider text-label-3 tabular-nums">
                {s.tier}
              </span>
            </div>
            <div className="text-[16px] font-semibold tracking-tight mb-1.5">
              {s.name}
            </div>
            <div className="text-[13px] text-label-2 leading-[1.45]">
              {s.best}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

const hotspots = [
  { n: "01", title: "Chat", desc: "Намерения превращаются в код." },
  {
    n: "02",
    title: "Live Preview",
    desc: "Select-to-edit: клик в элемент → промпт правит точечно.",
  },
  {
    n: "03",
    title: "Versions",
    desc: "Каждое изменение — atomic snapshot. Откат за секунду.",
  },
  {
    n: "04",
    title: "TopBar",
    desc: "Модель, регион, баланс — всё в рублях.",
  },
  {
    n: "05",
    title: "Build Status",
    desc: "Реальный rebuild каждые 200 мс.",
  },
];

function WorkspaceShowcase() {
  return (
    <section
      id="workspace"
      className="max-w-7xl mx-auto px-6 lg:px-12 py-24 lg:py-32 border-t border-separator"
    >
      <Reveal className="max-w-3xl mb-12">
        <div className="text-[11px] font-mono uppercase tracking-[0.18em] text-label-3 mb-3">
          03 · рабочее место
        </div>
        <h2 className="text-[clamp(32px,4vw,52px)] leading-[1.05] tracking-[-0.025em] font-semibold mb-4 text-balance">
          3 панели. Один разговор. Полный контроль.
        </h2>
        <p className="text-[17px] leading-[1.55] text-label-2 max-w-xl">
          Chat слева. Live preview по центру. Versions справа. Управляешь
          моделью, регионом и балансом из одного места.
        </p>
      </Reveal>
      <div className="mb-12 max-w-5xl mx-auto">
        <WorkspaceMockCard />
      </div>
      <div className="grid sm:grid-cols-2 lg:grid-cols-5 gap-3">
        {hotspots.map((h) => (
          <div
            key={h.n}
            className="rounded-xl border border-separator p-5 bg-bg-elevated-1"
          >
            <div className="text-[11px] font-mono text-accent mb-2 tabular-nums">
              {h.n}
            </div>
            <div className="text-[15px] font-semibold mb-2 tracking-tight">
              {h.title}
            </div>
            <div className="text-[13px] text-label-2 leading-[1.5]">
              {h.desc}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

const cases = [
  {
    title: "Cafe Polet",
    meta: "Next.js · Postgres",
    time: "4 дня от идеи до beta",
    desc: "SaaS-дашборд для сети спб-кофеен с онлайн-бронированием и аналитикой.",
  },
  {
    title: "Crypto Edu",
    meta: "Astro · MDX",
    time: "6 часов",
    desc: "Образовательный лендинг с курсами, календарём и приёмом заявок.",
  },
  {
    title: "Inner Tools",
    meta: "FastAPI + React",
    time: "2 дня",
    desc: "Внутренний админ-инструмент с auth и ролями для команды из 12.",
  },
];

function CaseStudies() {
  return (
    <section
      id="cases"
      className="max-w-7xl mx-auto px-6 lg:px-12 py-24 lg:py-32 border-t border-separator"
    >
      <Reveal className="max-w-3xl mb-12">
        <div className="text-[11px] font-mono uppercase tracking-[0.18em] text-label-3 mb-3">
          04 · проекты
        </div>
        <h2 className="text-[clamp(32px,4vw,52px)] leading-[1.05] tracking-[-0.025em] font-semibold mb-4 text-balance">
          Что собирают на Omnia.
        </h2>
      </Reveal>
      <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
        {cases.map((c) => (
          <div
            key={c.title}
            className="group rounded-xl overflow-hidden border border-separator bg-bg-elevated-1 hover:border-label-3 hover:-translate-y-1 transition-[transform,border-color] duration-300"
          >
            <div className="aspect-[16/10] bg-bg-base border-b border-separator flex items-center justify-center">
              <div className="text-[24px] font-semibold tracking-tight text-label-3 group-hover:text-label-1 transition-colors">
                {c.title}
              </div>
            </div>
            <div className="p-5">
              <div className="text-[15px] font-semibold mb-1.5 tracking-tight">
                {c.title}
              </div>
              <div className="text-[13px] text-label-2 mb-3 leading-[1.5]">
                {c.desc}
              </div>
              <div className="text-[11px] font-mono text-label-3 tabular-nums">
                {c.meta} · {c.time}
              </div>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

const tiers = [
  {
    name: "Free",
    price: "0",
    features: [
      "1 проект",
      "static-html / astro",
      "omnia.app поддомен",
      "500 токенов / день",
      "без кода в выгрузке",
    ],
    featured: false,
    cta: "Начать",
    href: "/register",
  },
  {
    name: "Pro",
    price: "1 990",
    features: [
      "неограниченные проекты",
      "все 9 стеков",
      "свой домен",
      "оплата за токены",
      "GitHub push",
      "rollback unlimited",
    ],
    featured: true,
    cta: "Создать проект",
    href: "/register",
  },
  {
    name: "Business",
    price: "9 990",
    features: [
      "команда до 10",
      "выделенный сервер",
      "152-ФЗ-compliant",
      "приоритет поддержка",
      "SSO",
      "кастом-биллинг",
    ],
    featured: false,
    cta: "Связаться",
    href: "mailto:hello@omnia.ai",
  },
];

function Pricing() {
  return (
    <section
      id="pricing"
      className="max-w-7xl mx-auto px-6 lg:px-12 py-24 lg:py-32 border-t border-separator"
    >
      <Reveal className="max-w-2xl mb-12 text-center mx-auto">
        <div className="text-[11px] font-mono uppercase tracking-[0.18em] text-label-3 mb-3">
          05 · цены
        </div>
        <h2 className="text-[clamp(32px,4vw,52px)] leading-[1.05] tracking-[-0.025em] font-semibold mb-4 text-balance">
          Платишь за токены. Без подписок-сюрпризов.
        </h2>
      </Reveal>
      <div className="grid lg:grid-cols-3 gap-3 max-w-5xl mx-auto">
        {tiers.map((t) => (
          <div
            key={t.name}
            className={
              t.featured
                ? "rounded-2xl border border-accent bg-bg-elevated-1 p-7"
                : "rounded-2xl border border-separator bg-bg-elevated-1 p-7"
            }
          >
            <div className="text-[15px] font-semibold mb-1 tracking-tight">
              {t.name}
            </div>
            <div className="mb-6 flex items-baseline gap-1.5">
              <span className="text-[36px] font-semibold tabular-nums tracking-[-0.02em]">
                {t.price} ₽
              </span>
              <span className="text-[12px] font-mono text-label-3">/мес</span>
            </div>
            <ul className="space-y-2.5 mb-7">
              {t.features.map((f) => (
                <li
                  key={f}
                  className="flex items-start gap-2.5 text-[14px] text-label-2"
                >
                  <Check
                    className="h-4 w-4 text-system-green mt-0.5 shrink-0"
                    strokeWidth={2.25}
                  />
                  <span>{f}</span>
                </li>
              ))}
            </ul>
            <Link
              href={t.href}
              className={
                t.featured
                  ? "inline-flex items-center justify-center h-11 w-full rounded-full font-medium transition-transform active:scale-[0.98] bg-accent text-accent-fg hover:bg-accent-hover"
                  : "inline-flex items-center justify-center h-11 w-full rounded-full font-medium transition-transform active:scale-[0.98] border border-separator-solid text-label-1 hover:border-label-3"
              }
            >
              {t.cta}
            </Link>
          </div>
        ))}
      </div>
    </section>
  );
}

const faqs = [
  {
    q: "А оно правда работает?",
    a: "Да. Регистрируйся, открой /workspace и тыкнись — реальный AI генерит реальный код в реальные файлы в реальный git. Каждое изменение — atomic snapshot, откат за секунду.",
  },
  {
    q: "Что с приватностью?",
    a: "Сервера в РФ (Serverum), 152-ФЗ-compliant. Код твой — можно выгрузить в свой GitHub в любой момент. Один клик в TopBar.",
  },
  {
    q: "Как устроена оплата?",
    a: "Биллинг в рублях через ЮKassa. Платишь только за токены, без подписок-сюрпризов. Free-тариф: 500 токенов в день.",
  },
  {
    q: "Какие LLM доступны?",
    a: "Claude 4.5 Sonnet, GPT-4.1, YandexGPT 5, GigaChat. Селектор моделей прямо в TopBar — переключаешься без перезагрузки.",
  },
  {
    q: "Что если AI сгенерил мусор?",
    a: "Кнопка «откатиться» под каждым промптом. Версии живут вечно. Можно вернуться к v1.0 даже после 50-го изменения.",
  },
  {
    q: "Деплой включён?",
    a: "Да. На наш поддомен omnia.app — бесплатно. На свой домен — в Pro/Business. Билд, SSL, nginx — один клик.",
  },
];

function Faq() {
  return (
    <section
      id="faq"
      className="max-w-3xl mx-auto px-6 py-24 lg:py-32 border-t border-separator"
    >
      <Reveal className="mb-12">
        <div className="text-[11px] font-mono uppercase tracking-[0.18em] text-label-3 mb-3">
          06 · вопросы
        </div>
        <h2 className="text-[clamp(32px,4vw,52px)] leading-[1.05] tracking-[-0.025em] font-semibold mb-4 text-balance">
          Что спрашивают.
        </h2>
      </Reveal>
      <div className="divide-y divide-separator border-y border-separator">
        {faqs.map((f) => (
          <details key={f.q} className="group py-5">
            <summary className="flex items-center justify-between cursor-pointer list-none text-[16px] font-medium hover:text-accent transition-colors">
              <span>{f.q}</span>
              <ArrowRight
                className="h-4 w-4 text-label-3 group-open:rotate-90 transition-transform shrink-0 ml-3"
                strokeWidth={1.75}
              />
            </summary>
            <p className="mt-3 text-[14px] leading-[1.6] text-label-2">
              {f.a}
            </p>
          </details>
        ))}
      </div>
    </section>
  );
}

function Footer() {
  const cols: { title: string; links: { label: string; href: string }[] }[] = [
    {
      title: "Продукт",
      links: [
        { label: "Стеки", href: "#stacks" },
        { label: "Рабочее место", href: "#workspace" },
        { label: "Цены", href: "#pricing" },
        { label: "FAQ", href: "#faq" },
      ],
    },
    {
      title: "Компания",
      links: [
        { label: "О нас", href: "#" },
        { label: "Блог", href: "#" },
        { label: "Контакты", href: "mailto:hello@omnia.ai" },
        { label: "Карьера", href: "#" },
      ],
    },
    {
      title: "Юридическое",
      links: [
        { label: "Договор-оферта", href: "#" },
        { label: "Политика данных", href: "#" },
        { label: "152-ФЗ", href: "#" },
        { label: "Безопасность", href: "#" },
      ],
    },
  ];

  return (
    <footer className="border-t border-separator bg-bg-elevated-1">
      <div className="max-w-7xl mx-auto px-6 lg:px-12 py-16">
        <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-10 mb-12">
          <div>
            <Link href="/" className="flex items-center gap-2 mb-3">
              <span className="inline-block h-6 w-6 rounded-sm bg-accent" />
              <span className="text-[15px] font-semibold tracking-tight">
                Omnia.AI
              </span>
            </Link>
            <p className="text-[13px] text-label-2 leading-[1.55] max-w-xs">
              AI-конструктор сайтов и продуктов. Сервера в РФ, рубли, 152-ФЗ.
            </p>
          </div>
          {cols.map((col) => (
            <div key={col.title}>
              <div className="text-[11px] font-mono uppercase tracking-[0.15em] text-label-3 mb-3">
                {col.title}
              </div>
              <ul className="space-y-2">
                {col.links.map((l) => (
                  <li key={l.label}>
                    <a
                      href={l.href}
                      className="text-[14px] text-label-2 hover:text-label-1 transition-colors"
                    >
                      {l.label}
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
        <div className="flex flex-wrap items-center justify-between gap-3 pt-7 border-t border-separator text-[11px] font-mono uppercase tracking-[0.12em] text-label-3 tabular-nums">
          <div>© 2026 omnia.ai · spb</div>
          <div className="flex items-center gap-2">
            <span className="inline-block h-1.5 w-1.5 rounded-full bg-system-green" />
            <span>все системы работают</span>
          </div>
        </div>
      </div>
    </footer>
  );
}
