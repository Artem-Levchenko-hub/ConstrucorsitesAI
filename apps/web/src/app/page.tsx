import type { LucideIcon } from "lucide-react";
import {
  ArrowRight,
  BarChart3,
  Bot,
  Check,
  ChevronRight,
  CircleDollarSign,
  Code2,
  Database,
  ExternalLink,
  Globe2,
  MessageSquareText,
  PackageSearch,
  PanelsTopLeft,
  Plug,
  Rocket,
  ShieldCheck,
  Smartphone,
  Store,
  UsersRound,
  Webhook,
} from "lucide-react";
import { getLocale } from "next-intl/server";
import Link from "next/link";

import { LocaleSwitcher } from "@/components/LocaleSwitcher";
import { BrandMark } from "@/components/marketing/BrandMark";
import { Reveal } from "@/components/marketing/Reveal";

const copy = {
  ru: {
    nav: ["Возможности", "Как работает", "Интеграции", "Запуск"],
    login: "Войти",
    navCta: "Открыть MAX Studio",
    eyebrow: "MAX Mini Apps · уже доступно",
    heroA: "Превратите идею в",
    heroB: "готовое приложение",
    heroLead:
      "Omnia собирает интерфейс, сервер и базу данных, подключает MAX-бота, бизнес-сервисы и доводит проект до постоянного HTTPS-адреса.",
    heroCta: "Создать приложение",
    heroAlt: "Посмотреть, что входит",
    proof: ["Без найма разработчиков", "Код остаётся у вас", "Запуск по шагам"],
    prompt: "Создай приложение лояльности для кофейни в MAX",
    promptHint: "Опишите продукт обычными словами…",
    agent: "Omnia собирает приложение",
    agentSteps: [
      "Проектирую мобильный сценарий",
      "Создаю страницы и данные",
      "Проверяю production-сборку",
    ],
    available: "Доступно сейчас",
    soon: "В разработке",
    directionsKicker: "Один продукт — один понятный сценарий",
    directionsTitle: "Не универсальный чат, а отдельные продуктовые студии",
    directionsLead:
      "Каждое направление получает собственный бриф, набор проверок и маршрут публикации. Сейчас полностью открыт конструктор приложений для MAX.",
    workflowKicker: "От задачи до запуска",
    workflowTitle: "Техническую работу берём на себя",
    workflowLead:
      "Вы отвечаете за продуктовые решения. Omnia пишет код, хранит состояние сборки и показывает только те шаги, где действительно нужен владелец.",
    integrationsKicker: "Integration Hub",
    integrationsTitle: "Подключения без правок сгенерированного кода",
    integrationsLead:
      "Платежи, CRM, учёт, доставка и аналитика подключаются через защищённый кабинет. Секреты шифруются и не попадают в исходники приложения.",
    resultKicker: "Что остаётся у вас",
    resultTitle: "Полноценный цифровой продукт, а не одноразовый прототип",
    resultLead:
      "Редактируйте через агента, возвращайтесь к прошлым версиям, подключайте домен или свою VPS. Состояние проекта хранится на сервере и не сбрасывается после обновления страницы.",
    launchKicker: "Готовность к MAX",
    launchTitle: "Один маршрут до работающего мини-приложения",
    launchLead:
      "Студия проверяет сборку, бота, HTTPS, webhook, URL в кабинете MAX, обязательные данные и политики. На выходе — понятный статус и рабочая ссылка.",
    finalTitle: "Начните с описания продукта",
    finalLead:
      "Регистрация нужна сразу: она защищает бесплатный лимит и связывает проекты с ООО, ИП или самозанятым владельцем MAX-бота.",
    finalCta: "Перейти в MAX Studio",
    footer: "Продуктовая среда для создания и запуска MAX Mini Apps.",
  },
  en: {
    nav: ["Capabilities", "How it works", "Integrations", "Launch"],
    login: "Sign in",
    navCta: "Open MAX Studio",
    eyebrow: "MAX Mini Apps · available now",
    heroA: "Turn an idea into a",
    heroB: "production application",
    heroLead:
      "Omnia builds the interface, backend and database, connects a MAX bot and business services, then takes the project to a permanent HTTPS address.",
    heroCta: "Build an application",
    heroAlt: "See what is included",
    proof: ["No development team", "You own the code", "Guided launch"],
    prompt: "Build a loyalty application for a coffee shop in MAX",
    promptHint: "Describe the product in plain language…",
    agent: "Omnia is building the application",
    agentSteps: [
      "Designing the mobile flow",
      "Creating screens and data",
      "Validating the production build",
    ],
    available: "Available now",
    soon: "In development",
    directionsKicker: "One product, one clear workflow",
    directionsTitle: "Focused product studios instead of a generic chat",
    directionsLead:
      "Each direction gets a dedicated brief, quality checks and publishing route. The MAX application builder is fully available today.",
    workflowKicker: "From brief to launch",
    workflowTitle: "We handle the technical work",
    workflowLead:
      "You make product decisions. Omnia writes code, persists build state and only asks for actions that truly require the owner.",
    integrationsKicker: "Integration Hub",
    integrationsTitle: "Connections without editing generated code",
    integrationsLead:
      "Payments, CRM, inventory, delivery and analytics are connected through a secure dashboard. Secrets are encrypted and never added to source code.",
    resultKicker: "What you keep",
    resultTitle: "A complete digital product, not a disposable prototype",
    resultLead:
      "Edit with the agent, restore previous versions, connect a domain or your own VPS. Project state is server-side and survives every page refresh.",
    launchKicker: "MAX readiness",
    launchTitle: "One route to a working mini application",
    launchLead:
      "The studio validates the build, bot, HTTPS, webhook, MAX dashboard URL, required business details and policies. You get a clear status and a working link.",
    finalTitle: "Start by describing the product",
    finalLead:
      "Registration is required up front to protect the free limit and associate projects with a company, sole proprietor or self-employed MAX bot owner.",
    finalCta: "Go to MAX Studio",
    footer: "A product environment for building and launching MAX Mini Apps.",
  },
} as const;

const products: Array<{
  title: string;
  titleEn: string;
  text: string;
  textEn: string;
  href: string;
  active: boolean;
  Icon: LucideIcon;
}> = [
  {
    title: "MAX Mini Apps и боты",
    titleEn: "MAX Mini Apps and bots",
    text: "Мобильный интерфейс, backend, бот, webhook и публикация.",
    textEn: "Mobile interface, backend, bot, webhook and publishing.",
    href: "/max",
    active: true,
    Icon: Bot,
  },
  {
    title: "Веб-приложения",
    titleEn: "Web applications",
    text: "Личные кабинеты, CRM, каталоги и внутренние сервисы.",
    textEn: "Portals, CRM systems, catalogues and internal tools.",
    href: "/web-apps",
    active: false,
    Icon: Globe2,
  },
  {
    title: "Лендинги",
    titleEn: "Landing pages",
    text: "Маркетинговые страницы, формы, аналитика и домены.",
    textEn: "Marketing pages, forms, analytics and domains.",
    href: "/landings",
    active: false,
    Icon: PanelsTopLeft,
  },
  {
    title: "Мобильные приложения",
    titleEn: "Mobile applications",
    text: "Продукты для iOS и Android с отдельным релизом.",
    textEn: "Standalone iOS and Android products.",
    href: "/apps",
    active: false,
    Icon: Smartphone,
  },
];

const workflow = [
  {
    number: "01",
    title: "Опишите задачу",
    titleEn: "Describe the product",
    text: "Короткий бриф помогает агенту понять аудиторию, сценарий и обязательные функции.",
    textEn: "A short brief gives the agent the audience, core flow and required features.",
    Icon: MessageSquareText,
  },
  {
    number: "02",
    title: "Проверьте сборку",
    titleEn: "Review the build",
    text: "Следите за работой в реальном времени, тестируйте мобильное превью и просите изменения.",
    textEn: "Follow the work live, test the mobile preview and request changes.",
    Icon: Code2,
  },
  {
    number: "03",
    title: "Запустите в MAX",
    titleEn: "Launch in MAX",
    text: "Подключите бота и сервисы. Студия опубликует HTTPS-версию и активирует webhook.",
    textEn: "Connect the bot and services. The studio publishes HTTPS and activates the webhook.",
    Icon: Rocket,
  },
];

const outcomes = [
  { title: "Интерфейс и код", titleEn: "Interface and code", Icon: Code2 },
  { title: "Backend и данные", titleEn: "Backend and data", Icon: Database },
  { title: "HTTPS и домен", titleEn: "HTTPS and domain", Icon: Globe2 },
  { title: "MAX-бот и webhook", titleEn: "MAX bot and webhook", Icon: Webhook },
  { title: "История версий", titleEn: "Version history", Icon: ShieldCheck },
  { title: "Бизнес-интеграции", titleEn: "Business integrations", Icon: Plug },
];

const integrations = [
  { name: "ЮKassa", Icon: CircleDollarSign },
  { name: "iiko / r_keeper", Icon: Store },
  { name: "Битрикс24 / amoCRM", Icon: UsersRound },
  { name: "МойСклад / 1С", Icon: PackageSearch },
  { name: "Метрика", Icon: BarChart3 },
  { name: "MAX API", Icon: Bot },
];

function SectionHeading({
  kicker,
  title,
  lead,
}: {
  kicker: string;
  title: string;
  lead: string;
}) {
  return (
    <div className="mx-auto max-w-3xl text-center">
      <p className="text-xs font-semibold uppercase tracking-[0.2em] text-blue-400">
        {kicker}
      </p>
      <h2 className="font-display mt-4 text-3xl font-semibold leading-tight tracking-[-0.035em] text-slate-50 sm:text-4xl lg:text-5xl">
        {title}
      </h2>
      <p className="mt-5 text-base leading-7 text-slate-400 sm:text-lg">{lead}</p>
    </div>
  );
}

function AgentWindow({ t }: { t: (typeof copy)["ru"] | (typeof copy)["en"] }) {
  return (
    <div className="relative mx-auto mt-14 max-w-5xl px-3 sm:px-6">
      <div className="absolute -inset-14 -z-10 bg-[radial-gradient(circle,rgba(59,130,246,0.18),transparent_62%)] blur-2xl" />
      <div className="overflow-hidden rounded-2xl border border-[#263150] bg-[#0f121f] shadow-[0_34px_100px_rgba(0,0,0,0.5)]">
        <div className="flex h-12 items-center justify-between border-b border-[#1e243f] px-4">
          <div className="flex items-center gap-1.5" aria-hidden>
            <span className="h-2.5 w-2.5 rounded-full bg-[#ef4444]/70" />
            <span className="h-2.5 w-2.5 rounded-full bg-[#e8c547]/70" />
            <span className="h-2.5 w-2.5 rounded-full bg-[#10b981]/70" />
          </div>
          <span className="text-xs text-slate-500">omnia / max-studio</span>
          <span className="h-2 w-2 rounded-full bg-emerald-400 shadow-[0_0_12px_rgba(16,185,129,.8)]" />
        </div>
        <div className="grid min-h-[410px] md:grid-cols-[1fr_280px]">
          <div className="p-5 sm:p-8">
            <div className="ml-auto max-w-md rounded-2xl rounded-br-md bg-blue-500 px-5 py-4 text-sm leading-6 text-white">
              {t.prompt}
            </div>
            <div className="mt-6 max-w-lg rounded-2xl rounded-bl-md border border-[#1e243f] bg-[#13172a] p-5">
              <div className="flex items-center gap-3">
                <span className="grid h-9 w-9 place-items-center rounded-lg bg-blue-500/15 text-blue-400">
                  <Bot className="h-4 w-4" />
                </span>
                <div>
                  <p className="text-sm font-semibold text-white">{t.agent}</p>
                  <p className="mt-0.5 text-xs text-slate-500">3 / 3</p>
                </div>
              </div>
              <div className="mt-5 space-y-3">
                {t.agentSteps.map((step) => (
                  <div key={step} className="flex items-center gap-3 text-sm text-slate-300">
                    <span className="grid h-5 w-5 place-items-center rounded-full bg-emerald-400/15 text-emerald-400">
                      <Check className="h-3 w-3" />
                    </span>
                    {step}
                  </div>
                ))}
              </div>
            </div>
            <div className="mt-7 flex items-center gap-3 rounded-xl border border-[#263150] bg-[#080a10] p-3">
              <span className="flex-1 px-2 text-sm text-slate-600">{t.promptHint}</span>
              <span className="grid h-9 w-9 place-items-center rounded-lg bg-blue-500 text-white">
                <ArrowRight className="h-4 w-4" />
              </span>
            </div>
          </div>
          <div className="hidden border-l border-[#1e243f] bg-[#13172a] p-5 md:block">
            <div className="flex items-center justify-between text-xs text-slate-500">
              <span>Live preview</span>
              <span>390 × 844</span>
            </div>
            <div className="mx-auto mt-5 h-[330px] max-w-[190px] overflow-hidden rounded-[28px] border-4 border-[#293352] bg-[#f8fafc] p-3 shadow-xl">
              <div className="mx-auto h-1.5 w-12 rounded-full bg-slate-900" />
              <div className="mt-5 h-16 rounded-xl bg-blue-500 p-3">
                <div className="h-2 w-20 rounded bg-white/80" />
                <div className="mt-2 h-1.5 w-12 rounded bg-white/45" />
              </div>
              <div className="mt-3 grid grid-cols-2 gap-2">
                {[0, 1, 2, 3].map((item) => (
                  <div key={item} className="rounded-lg border border-slate-200 bg-white p-2">
                    <div className="h-10 rounded bg-slate-100" />
                    <div className="mt-2 h-1.5 rounded bg-slate-300" />
                    <div className="mt-1 h-1.5 w-2/3 rounded bg-slate-200" />
                  </div>
                ))}
              </div>
              <div className="mt-3 h-8 rounded-lg bg-slate-900" />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default async function HomePage() {
  const locale = await getLocale();
  const isEn = locale === "en";
  const t = isEn ? copy.en : copy.ru;

  return (
    <div data-marketing className="min-h-svh bg-[#080a10] text-slate-50">
      <header className="sticky top-0 z-50 border-b border-[#1e243f]/80 bg-[#080a10]/85 backdrop-blur-xl">
        <div className="mx-auto flex h-20 max-w-[1200px] items-center justify-between gap-5 px-5 sm:px-8">
          <BrandMark inverse />
          <nav className="hidden items-center gap-7 lg:flex">
            {[
              ["#capabilities", t.nav[0]],
              ["#workflow", t.nav[1]],
              ["#integrations", t.nav[2]],
              ["#launch", t.nav[3]],
            ].map(([href, label]) => (
              <a key={href} href={href} className="text-sm text-slate-400 transition hover:text-white">
                {label}
              </a>
            ))}
          </nav>
          <div className="flex items-center gap-2">
            <LocaleSwitcher />
            <Link href="/login" className="hidden px-3 py-2 text-sm text-slate-300 transition hover:text-white sm:block">
              {t.login}
            </Link>
            <Link href="/max" className="marketing-button marketing-button-primary min-h-10 px-4">
              <span className="hidden sm:inline">{t.navCta}</span>
              <ArrowRight className="h-4 w-4" />
            </Link>
          </div>
        </div>
      </header>

      <main>
        <section className="studio-grid studio-glow overflow-hidden border-b border-[#1e243f] pb-24 pt-24 sm:pt-32">
          <div className="mx-auto max-w-[1080px] px-5 text-center sm:px-8">
            <Reveal>
              <span className="inline-flex items-center gap-2 rounded-full border border-blue-400/25 bg-blue-400/[0.07] px-3 py-1.5 text-xs font-medium text-blue-300">
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
                {t.eyebrow}
              </span>
              <h1 className="font-display mx-auto mt-7 max-w-5xl text-5xl font-semibold leading-[0.98] tracking-[-0.05em] sm:text-6xl lg:text-[76px]">
                {t.heroA}{" "}
                <span className="text-blue-400">{t.heroB}</span>
              </h1>
              <p className="mx-auto mt-7 max-w-3xl text-lg leading-8 text-slate-400 sm:text-xl">
                {t.heroLead}
              </p>
              <div className="mt-9 flex flex-col justify-center gap-3 sm:flex-row">
                <Link href="/max" className="marketing-button marketing-button-primary min-h-12 px-6">
                  {t.heroCta}
                  <ArrowRight className="h-4 w-4" />
                </Link>
                <a href="#capabilities" className="marketing-button marketing-button-secondary min-h-12 px-6">
                  {t.heroAlt}
                  <ChevronRight className="h-4 w-4" />
                </a>
              </div>
              <div className="mt-8 flex flex-wrap justify-center gap-x-6 gap-y-2 text-xs text-slate-500">
                {t.proof.map((item) => (
                  <span key={item} className="inline-flex items-center gap-2">
                    <Check className="h-3.5 w-3.5 text-emerald-400" />
                    {item}
                  </span>
                ))}
              </div>
            </Reveal>
            <Reveal delay={120}>
              <AgentWindow t={t} />
            </Reveal>
          </div>
        </section>

        <section id="capabilities" className="border-b border-[#1e243f] py-24 sm:py-32">
          <div className="mx-auto max-w-[1200px] px-5 sm:px-8">
            <Reveal>
              <SectionHeading kicker={t.directionsKicker} title={t.directionsTitle} lead={t.directionsLead} />
            </Reveal>
            <div className="mt-14 grid gap-4 md:grid-cols-2">
              {products.map(({ Icon, ...product }, index) => (
                <Reveal key={product.href} delay={index * 60}>
                  <Link
                    href={product.href}
                    className={`group flex min-h-56 flex-col rounded-2xl border p-7 transition ${
                      product.active
                        ? "border-blue-400/35 bg-[#0f121f] hover:border-blue-400/65"
                        : "border-[#1e243f] bg-[#0b0e17] hover:bg-[#0f121f]"
                    }`}
                  >
                    <div className="flex items-start justify-between">
                      <span className={`grid h-11 w-11 place-items-center rounded-xl ${product.active ? "bg-blue-500 text-white" : "bg-[#13172a] text-slate-500"}`}>
                        <Icon className="h-5 w-5" />
                      </span>
                      <span className={`rounded-full border px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wider ${product.active ? "border-emerald-400/25 bg-emerald-400/[0.08] text-emerald-300" : "border-[#293352] text-slate-600"}`}>
                        {product.active ? t.available : t.soon}
                      </span>
                    </div>
                    <h3 className="font-display mt-8 text-2xl font-semibold">{isEn ? product.titleEn : product.title}</h3>
                    <p className="mt-2 max-w-md text-sm leading-6 text-slate-500">{isEn ? product.textEn : product.text}</p>
                    <ArrowRight className={`mt-auto h-4 w-4 transition-transform group-hover:translate-x-1 ${product.active ? "text-blue-400" : "text-slate-700"}`} />
                  </Link>
                </Reveal>
              ))}
            </div>
          </div>
        </section>

        <section id="workflow" className="bg-[#0b0e17] py-24 sm:py-32">
          <div className="mx-auto max-w-[1200px] px-5 sm:px-8">
            <Reveal>
              <SectionHeading kicker={t.workflowKicker} title={t.workflowTitle} lead={t.workflowLead} />
            </Reveal>
            <div className="mt-16 grid gap-4 lg:grid-cols-3">
              {workflow.map(({ Icon, ...item }, index) => (
                <Reveal key={item.number} delay={index * 70}>
                  <article className="studio-card relative h-full min-h-72 overflow-hidden p-7">
                    <span className="font-mono text-xs text-blue-400">{item.number}</span>
                    <Icon className="mt-14 h-7 w-7 text-slate-300" />
                    <h3 className="font-display mt-6 text-xl font-semibold">{isEn ? item.titleEn : item.title}</h3>
                    <p className="mt-3 text-sm leading-6 text-slate-500">{isEn ? item.textEn : item.text}</p>
                    <span className="absolute -right-6 -top-10 font-display text-[150px] font-bold leading-none text-white/[0.018]">{item.number}</span>
                  </article>
                </Reveal>
              ))}
            </div>
          </div>
        </section>

        <section className="border-y border-[#1e243f] py-24 sm:py-32">
          <div className="mx-auto grid max-w-[1200px] gap-14 px-5 sm:px-8 lg:grid-cols-[0.8fr_1.2fr] lg:items-start">
            <Reveal>
              <div className="lg:sticky lg:top-32">
                <p className="text-xs font-semibold uppercase tracking-[0.2em] text-blue-400">{t.resultKicker}</p>
                <h2 className="font-display mt-4 text-3xl font-semibold leading-tight tracking-[-0.035em] sm:text-4xl">{t.resultTitle}</h2>
                <p className="mt-5 text-base leading-7 text-slate-400">{t.resultLead}</p>
              </div>
            </Reveal>
            <div className="grid gap-px overflow-hidden rounded-2xl border border-[#1e243f] bg-[#1e243f] sm:grid-cols-2">
              {outcomes.map(({ Icon, ...item }, index) => (
                <Reveal key={item.title} delay={index * 40}>
                  <div className="flex min-h-32 items-center gap-4 bg-[#0f121f] p-6">
                    <span className="grid h-10 w-10 shrink-0 place-items-center rounded-lg bg-blue-400/[0.08] text-blue-400">
                      <Icon className="h-5 w-5" />
                    </span>
                    <span className="font-display text-base font-semibold">{isEn ? item.titleEn : item.title}</span>
                  </div>
                </Reveal>
              ))}
            </div>
          </div>
        </section>

        <section id="integrations" className="bg-[#0b0e17] py-24 sm:py-32">
          <div className="mx-auto max-w-[1200px] px-5 sm:px-8">
            <Reveal>
              <SectionHeading kicker={t.integrationsKicker} title={t.integrationsTitle} lead={t.integrationsLead} />
            </Reveal>
            <div className="mt-14 grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
              {integrations.map(({ name, Icon }, index) => (
                <Reveal key={name} delay={index * 40}>
                  <div className="studio-card flex min-h-32 flex-col items-center justify-center gap-4 p-4 text-center">
                    <Icon className="h-6 w-6 text-blue-400" />
                    <span className="text-xs font-medium text-slate-300">{name}</span>
                  </div>
                </Reveal>
              ))}
            </div>
          </div>
        </section>

        <section id="launch" className="border-y border-[#1e243f] py-24 sm:py-32">
          <div className="mx-auto grid max-w-[1200px] gap-12 px-5 sm:px-8 lg:grid-cols-2 lg:items-center">
            <Reveal>
              <div>
                <p className="text-xs font-semibold uppercase tracking-[0.2em] text-blue-400">{t.launchKicker}</p>
                <h2 className="font-display mt-4 text-3xl font-semibold leading-tight tracking-[-0.035em] sm:text-5xl">{t.launchTitle}</h2>
                <p className="mt-5 max-w-xl text-base leading-7 text-slate-400">{t.launchLead}</p>
                <Link href="/max" className="marketing-button marketing-button-primary mt-8">
                  {t.heroCta}
                  <ArrowRight className="h-4 w-4" />
                </Link>
              </div>
            </Reveal>
            <Reveal delay={80}>
              <div className="studio-card p-4 sm:p-6">
                {[
                  ["01", isEn ? "Application build" : "Рабочая сборка приложения"],
                  ["02", isEn ? "Verified MAX bot" : "Проверенный MAX-бот"],
                  ["03", isEn ? "Permanent HTTPS version" : "Постоянная HTTPS-версия"],
                  ["04", isEn ? "Active webhook" : "Активный webhook"],
                  ["05", isEn ? "MAX dashboard URL" : "URL добавлен в кабинете MAX"],
                ].map(([number, label], index) => (
                  <div key={number} className="flex items-center gap-4 border-b border-[#1e243f] px-2 py-4 last:border-0">
                    <span className="font-mono text-xs text-slate-600">{number}</span>
                    <span className="flex-1 text-sm text-slate-300">{label}</span>
                    <span className={`grid h-6 w-6 place-items-center rounded-full ${index < 4 ? "bg-emerald-400/10 text-emerald-400" : "border border-[#334166] text-slate-600"}`}>
                      {index < 4 ? <Check className="h-3.5 w-3.5" /> : null}
                    </span>
                  </div>
                ))}
              </div>
            </Reveal>
          </div>
        </section>

        <section className="studio-grid studio-glow py-24 text-center sm:py-32">
          <Reveal>
            <div className="mx-auto max-w-3xl px-5 sm:px-8">
              <h2 className="font-display text-4xl font-semibold tracking-[-0.04em] sm:text-6xl">{t.finalTitle}</h2>
              <p className="mx-auto mt-6 max-w-2xl text-base leading-7 text-slate-400">{t.finalLead}</p>
              <Link href="/max" className="marketing-button marketing-button-primary mt-9 min-h-12 px-7">
                {t.finalCta}
                <ArrowRight className="h-4 w-4" />
              </Link>
            </div>
          </Reveal>
        </section>
      </main>

      <footer className="border-t border-[#1e243f] bg-[#080a10]">
        <div className="mx-auto grid max-w-[1200px] gap-10 px-5 py-12 sm:px-8 md:grid-cols-[1fr_auto]">
          <div>
            <BrandMark inverse />
            <p className="mt-4 max-w-sm text-sm leading-6 text-slate-500">{t.footer}</p>
          </div>
          <div className="grid grid-cols-2 gap-x-10 gap-y-3 text-sm text-slate-500 sm:grid-cols-3">
            <Link href="/legal/privacy" className="hover:text-white">{isEn ? "Privacy" : "Конфиденциальность"}</Link>
            <Link href="/legal/terms" className="hover:text-white">{isEn ? "Terms" : "Условия"}</Link>
            <Link href="/legal/personal-data" className="hover:text-white">{isEn ? "Personal data" : "Персональные данные"}</Link>
            <Link href="/legal/cookies" className="hover:text-white">Cookies</Link>
            <Link href="/legal/refunds" className="hover:text-white">{isEn ? "Refunds" : "Оплата и возвраты"}</Link>
            <a href="mailto:support@lead-generator.ru" className="inline-flex items-center gap-1 hover:text-white">
              Support
              <ExternalLink className="h-3 w-3" />
            </a>
          </div>
        </div>
      </footer>
    </div>
  );
}
