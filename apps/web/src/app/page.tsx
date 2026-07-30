import type { LucideIcon } from "lucide-react";
import {
  ArrowRight,
  BarChart3,
  Bot,
  CheckCircle2,
  CreditCard,
  Database,
  Layers3,
  MessageSquare,
  Settings,
  Smartphone,
  Waypoints,
  Zap,
} from "lucide-react";
import { getLocale } from "next-intl/server";
import Link from "next/link";

import { LocaleSwitcher } from "@/components/LocaleSwitcher";
import { BrandMark } from "@/components/marketing/BrandMark";

type Copy = {
  nav: [string, string, string, string];
  signIn: string;
  launch: string;
  badge: string;
  heroBefore: string;
  heroAccent: string;
  heroAfter: string;
  heroLead: string;
  heroCta: string;
  heroAlt: string;
  prompt: string;
  generate: string;
  partners: string;
  workflowBadge: string;
  workflowTitle: string;
  workflowLead: string;
  featuresBadge: string;
  featuresTitle: string;
  featuresLead: string;
  integrationsBadge: string;
  integrationsTitle: string;
  integrationsLead: string;
  pricingBadge: string;
  pricingTitle: string;
  pricingLead: string;
  footerLead: string;
};

const COPY: Record<"ru" | "en", Copy> = {
  ru: {
    nav: ["Возможности", "Как это работает", "Интеграции", "Тарифы"],
    signIn: "Войти",
    launch: "Открыть студию",
    badge: "Конструктор и публикация приложений через диалог",
    heroBefore: "Создавайте Mini Apps для",
    heroAccent: "MAX",
    heroAfter: "просто общаясь с агентом",
    heroLead:
      "Опишите продукт обычными словами. Агент соберёт интерфейс, backend и базу данных, подключит нужные сервисы и доведёт приложение до запуска в MAX.",
    heroCta: "Начать бесплатно",
    heroAlt: "Посмотреть сценарий",
    prompt:
      "Создай мини-апп для заказа пиццы с каталогом, корзиной и оплатой по СБП...",
    generate: "Создать",
    partners: "ПОДДЕРЖИВАЕМ ИНТЕГРАЦИИ ДЛЯ РЕАЛЬНОГО БИЗНЕСА",
    workflowBadge: "Сценарий работы",
    workflowTitle: "Без кода. Только диалог.",
    workflowLead:
      "Агент проводит от идеи до опубликованного мини-приложения и сохраняет состояние каждого шага.",
    featuresBadge: "Готовый продукт",
    featuresTitle: "Всё необходимое для бизнеса внутри MAX",
    featuresLead:
      "На выходе не макет, а работающий продукт с данными, оплатой, аналитикой и возможностью дальнейших правок.",
    integrationsBadge: "Интеграции",
    integrationsTitle: "Подключается к вашему стеку",
    integrationsLead:
      "Платежи, учёт, CRM и аналитика настраиваются из защищённого кабинета без ручного редактирования кода.",
    pricingBadge: "Тарифы",
    pricingTitle: "Понятная стоимость на каждом этапе",
    pricingLead:
      "Начните с первой сборки, затем выберите режим для регулярных обновлений и постоянной эксплуатации.",
    footerLead:
      "Создание и запуск полноценных Mini Apps для MAX с помощью продуктового AI-агента.",
  },
  en: {
    nav: ["Features", "How it Works", "Integrations", "Pricing"],
    signIn: "Sign In",
    launch: "Launch App",
    badge: "Introducing conversational app deployment",
    heroBefore: "Build Mini-Apps for",
    heroAccent: "Max Messenger",
    heroAfter: "Just by Chatting",
    heroLead:
      "Empower your brand with fully functional interactive mini-apps. Describe what you need to our AI agent, and watch it deploy directly inside MAX with full integration support.",
    heroCta: "Start Building Free",
    heroAlt: "Watch Product Flow",
    prompt:
      "Создай мини-апп для заказа пиццы с каталогом, корзиной и оплатой по СБП...",
    generate: "Generate",
    partners: "SUPPORTING WORLD-CLASS INTEGRATIONS",
    workflowBadge: "Workflow",
    workflowTitle: "Zero Code. Just Conversation.",
    workflowLead:
      "Our visual AI agent guides you from a raw idea to a fully deployed mini-app while preserving every build step.",
    featuresBadge: "Engineered for performance",
    featuresTitle: "Complete Toolbox for Messenger Commerce",
    featuresLead:
      "Deploy production applications where your users chat, with payments, data, analytics and ongoing agent editing.",
    integrationsBadge: "Integrations",
    integrationsTitle: "Connect Seamlessly with Your Stack",
    integrationsLead:
      "Sync catalogues, record analytics, trigger payment invoices and handle CRM flows from one secure hub.",
    pricingBadge: "Pricing plans",
    pricingTitle: "Predictable Pricing. Unlimited Scaling",
    pricingLead:
      "Start with your first build, then choose the operating mode that matches your daily usage.",
    footerLead:
      "Production Mini Apps for MAX, generated and operated through a product AI agent.",
  },
};

const workflow = [
  {
    number: "01",
    ru: ["Опишите приложение", "Расскажите, для кого продукт, что должен уметь и какое действие пользователь выполняет чаще всего."],
    en: ["Describe your App", "Tell the agent who the product is for, what it should do and which action matters most."],
  },
  {
    number: "02",
    ru: ["Проверьте живую сборку", "Агент создаёт страницы, данные и backend. Результат сразу появляется в мобильном превью."],
    en: ["Review the Live Build", "The agent creates screens, data and backend while the result appears in the mobile preview."],
  },
  {
    number: "03",
    ru: ["Опубликуйте в MAX", "Подключите бота и сервисы. Студия подготовит HTTPS, webhook и проверит готовность к запуску."],
    en: ["Deploy to MAX", "Connect the bot and services. The studio prepares HTTPS, webhook and validates launch readiness."],
  },
] as const;

const features: Array<{
  Icon: LucideIcon;
  ru: [string, string];
  en: [string, string];
}> = [
  {
    Icon: Zap,
    ru: ["Быстрая публикация", "Сборка, обновления и постоянный HTTPS-адрес без ручной настройки инфраструктуры."],
    en: ["Instant Deploy", "Build, update and publish to a permanent HTTPS address without manual infrastructure work."],
  },
  {
    Icon: CreditCard,
    ru: ["Платежи", "ЮKassa, СБП и другие способы оплаты подключаются через защищённые настройки."],
    en: ["Native Payments", "Connect payment providers and secure local payment methods from protected settings."],
  },
  {
    Icon: Database,
    ru: ["Backend и данные", "Каталоги, пользователи, заказы и история действий сохраняются в рабочей базе."],
    en: ["Backend & Data", "Catalogues, users, orders and activity history are stored in a production database."],
  },
  {
    Icon: Waypoints,
    ru: ["Разработка через диалог", "Меняйте сценарии, тексты и интерфейс обычными сообщениями без найма разработчика."],
    en: ["Conversational Agent", "Change flows, copy and interface through normal messages without hiring a developer."],
  },
  {
    Icon: BarChart3,
    ru: ["Аналитика", "События, конверсии и воронки отправляются в Метрику и другие системы аналитики."],
    en: ["Advanced Analytics", "Events, conversions and funnels are sent to your preferred analytics platform."],
  },
  {
    Icon: Settings,
    ru: ["Внешние API", "Подключайте CRM, учёт, доставку и собственные системы через готовые коннекторы."],
    en: ["Custom API Gateways", "Connect CRM, inventory, delivery and internal systems through managed connectors."],
  },
];

const integrations: Array<{
  Icon: LucideIcon;
  name: string;
  ru: string;
  en: string;
}> = [
  { Icon: CreditCard, name: "ЮKassa / СБП", ru: "Приём онлайн-платежей", en: "Online payments" },
  { Icon: Database, name: "PostgreSQL", ru: "Рабочая база данных", en: "Production database" },
  { Icon: BarChart3, name: "Яндекс Метрика", ru: "События и конверсии", en: "Events and conversions" },
  { Icon: Database, name: "Supabase", ru: "Auth и облачные таблицы", en: "Auth and cloud tables" },
  { Icon: Layers3, name: "amoCRM / Битрикс24", ru: "Лиды и сделки", en: "Leads and deals" },
  { Icon: Bot, name: "MAX Bot API", ru: "Бот, webhook и события", en: "Bot, webhook and events" },
  { Icon: Zap, name: "iiko / r_keeper", ru: "Каталог и заказы", en: "Catalogues and orders" },
  { Icon: Settings, name: "REST API", ru: "Ваши внутренние сервисы", en: "Internal systems" },
];

const plans = [
  {
    ru: ["Старт", "Первая сборка и проверка идеи.", "0 ₽", "за регистрацию"],
    en: ["Sandbox", "Perfect for validating the product flow.", "$0", "/start"],
    popular: false,
    featuresRu: ["1 MAX-приложение", "Первая AI-сборка", "Мобильное превью", "Базовая поддержка"],
    featuresEn: ["1 MAX app", "First AI build", "Mobile preview", "Basic support"],
  },
  {
    ru: ["Studio", "Для запуска и регулярных обновлений.", "По тарифу", "по использованию"],
    en: ["Pro Studio", "For launch and ongoing iterations.", "$49", "/month"],
    popular: true,
    featuresRu: ["Активные MAX-приложения", "Продуктовый AI-агент", "Интеграции и платежи", "Публикация и webhook", "Приоритетная поддержка"],
    featuresEn: ["Active MAX apps", "Product AI agent", "Integrations and payments", "Publishing and webhook", "Priority support"],
  },
  {
    ru: ["Enterprise", "Для команд и корпоративных систем.", "Индивидуально", ""],
    en: ["Enterprise Suite", "For teams and corporate systems.", "Custom", ""],
    popular: false,
    featuresRu: ["Собственная VPS", "Гарантия доступности", "Корпоративные API", "Персональное сопровождение"],
    featuresEn: ["Dedicated VPS", "Availability SLA", "Corporate APIs", "Solution support"],
  },
] as const;

function SectionHeading({
  badge,
  title,
  lead,
}: {
  badge: string;
  title: string;
  lead: string;
}) {
  return (
    <div className="mx-auto flex max-w-[1312px] flex-col items-center text-center">
      <span className="rounded-full border border-[#1d4f91] bg-[#0d1729] px-3 py-1.5 text-[12px] font-medium uppercase tracking-[0.01em] text-[#3b82f6]">
        {badge}
      </span>
      <h2 className="font-display mt-4 text-[32px] font-bold leading-[1.1] tracking-[-0.03em] text-[#f8fafc] sm:text-[40px]">
        {title}
      </h2>
      <p className="mt-4 max-w-[720px] text-[16px] leading-[1.7] text-[#94a3b8] sm:text-[18px]">
        {lead}
      </p>
    </div>
  );
}

export default async function HomePage() {
  const locale = await getLocale();
  const lang = locale.startsWith("en") ? "en" : "ru";
  const t = COPY[lang];

  return (
    <div data-marketing className="min-h-svh bg-[#080a10] text-[#f8fafc]">
      <header className="h-20 border-b border-[#111626] bg-[#080a10]">
        <div className="mx-auto flex h-full max-w-[1440px] items-center justify-between px-5 sm:px-8 lg:px-16">
          <BrandMark inverse label="MaxStudio" />
          <nav className="hidden items-center gap-8 text-[14px] text-[#94a3b8] lg:flex">
            <a href="#features" className="transition-colors hover:text-white">{t.nav[0]}</a>
            <a href="#how-it-works" className="transition-colors hover:text-white">{t.nav[1]}</a>
            <a href="#integrations" className="transition-colors hover:text-white">{t.nav[2]}</a>
            <a href="#pricing" className="transition-colors hover:text-white">{t.nav[3]}</a>
          </nav>
          <div className="flex items-center gap-3 sm:gap-4">
            <div className="hidden sm:block">
              <LocaleSwitcher />
            </div>
            <Link href="/login" className="hidden text-[14px] text-[#94a3b8] transition-colors hover:text-white sm:block">
              {t.signIn}
            </Link>
            <Link href="/max/register" className="rounded-lg bg-gradient-to-r from-[#3b82f6] to-[#8b5cf6] px-5 py-2.5 text-[14px] font-semibold text-white">
              <span className="sm:hidden">{lang === "ru" ? "Начать" : "Start"}</span>
              <span className="hidden sm:inline">{t.launch}</span>
            </Link>
          </div>
        </div>
      </header>

      <main>
        <section className="relative min-h-[751px] overflow-hidden border-b border-[#0d1220] px-5 py-20 sm:px-8 lg:px-16 lg:py-[100px]">
          <div className="pointer-events-none absolute left-1/2 top-[-220px] h-[600px] w-[900px] -translate-x-1/2 rounded-full bg-[radial-gradient(circle,rgba(57,68,177,0.19),transparent_67%)]" />
          <div className="relative mx-auto flex max-w-[1000px] flex-col items-center text-center">
            <span className="inline-flex items-center gap-2 rounded-full border border-[#342166] bg-[#18132f] px-3.5 py-2 text-[13px] text-[#9b6cff]">
              <span className="h-1.5 w-1.5 rounded-full bg-[#8b5cf6]" />
              {t.badge}
            </span>
            <h1 className="font-display mt-6 text-[42px] font-bold leading-[1.05] tracking-[-0.045em] text-[#f8fafc] sm:text-[54px] lg:text-[64px]">
              {t.heroBefore}{" "}
              <span className="text-[#3b82f6]">{t.heroAccent}</span>{" "}
              {t.heroAfter}
            </h1>
            <p className="mt-7 max-w-[760px] text-[17px] leading-[1.65] text-[#94a3b8] sm:text-[20px]">
              {t.heroLead}
            </p>
            <div className="mt-12 flex flex-col gap-4 sm:flex-row">
              <Link href="/max/register" className="inline-flex h-[49px] items-center justify-center gap-3 rounded-lg bg-gradient-to-r from-[#3b82f6] to-[#8b5cf6] px-7 text-[16px] font-semibold text-white">
                {t.heroCta}
                <ArrowRight className="h-4 w-4" />
              </Link>
              <a href="#how-it-works" className="inline-flex h-[49px] items-center justify-center rounded-lg border border-[#202946] bg-[#13172a] px-7 text-[16px] font-semibold text-[#f8fafc]">
                {t.heroAlt}
              </a>
            </div>
            <div className="mt-12 w-full max-w-[800px] rounded-2xl border border-[#202946] bg-[#111525] p-5 text-left shadow-[0_24px_80px_rgba(0,0,0,0.24)]">
              <div className="mb-4 flex gap-4">
                <span className="h-3 w-3 rounded-full bg-[#fb4b58]" />
                <span className="h-3 w-3 rounded-full bg-[#20c997]" />
              </div>
              <div className="flex min-h-[53px] items-center gap-3 rounded-lg border border-[#26304f] bg-[#080a10] px-3">
                <MessageSquare className="h-[18px] w-[18px] shrink-0 text-[#3b82f6]" />
                <span className="min-w-0 flex-1 truncate text-[15px] text-[#d6d9e4]">{t.prompt}</span>
                <Link href="/max/register" className="rounded-md bg-[#3b82f6] px-3 py-2 text-[12px] font-semibold text-white">
                  {t.generate}
                </Link>
              </div>
            </div>
          </div>
        </section>

        <section className="flex min-h-[159px] items-center border-b border-[#0d1220] px-5 sm:px-8 lg:px-16">
          <div className="mx-auto w-full max-w-[900px]">
            <p className="text-center text-[11px] font-semibold uppercase text-[#60708d]">{t.partners}</p>
            <div className="mt-7 grid grid-cols-2 gap-5 text-center text-[15px] font-semibold text-[#60708d] sm:grid-cols-5">
              {["ЮKassa", "PostgreSQL", "amoCRM", "Яндекс Метрика", "Supabase"].map((name) => (
                <span key={name} className="inline-flex items-center justify-center gap-2">
                  <Waypoints className="h-4 w-4" />
                  {name}
                </span>
              ))}
            </div>
          </div>
        </section>

        <section id="how-it-works" className="min-h-[628px] px-5 py-24 sm:px-8 lg:px-16">
          <SectionHeading badge={t.workflowBadge} title={t.workflowTitle} lead={t.workflowLead} />
          <div className="mx-auto mt-14 grid max-w-[1312px] gap-6 lg:grid-cols-3">
            {workflow.map((item) => {
              const [title, text] = item[lang];
              return (
                <article key={item.number} className="min-h-[222px] rounded-2xl border border-[#202946] bg-[#13172a] p-8">
                  <span className="flex h-10 w-10 items-center justify-center rounded-lg bg-[#17213d] text-[17px] font-bold text-[#3b82f6]">{item.number}</span>
                  <h3 className="mt-5 text-[18px] font-semibold">{title}</h3>
                  <p className="mt-2 text-[14px] leading-[1.55] text-[#94a3b8]">{text}</p>
                </article>
              );
            })}
          </div>
        </section>

        <section id="features" className="min-h-[812px] px-5 py-24 sm:px-8 lg:px-16">
          <SectionHeading badge={t.featuresBadge} title={t.featuresTitle} lead={t.featuresLead} />
          <div className="mx-auto mt-14 grid max-w-[1312px] gap-5 md:grid-cols-2 lg:grid-cols-3">
            {features.map(({ Icon, ...item }) => {
              const [title, text] = item[lang];
              return (
                <article key={title} className="min-h-[193px] rounded-2xl border border-[#202946] bg-[#13172a] p-7">
                  <span className="flex h-12 w-12 items-center justify-center rounded-xl bg-[#211b4a] text-[#8b5cf6]">
                    <Icon className="h-6 w-6" />
                  </span>
                  <h3 className="mt-4 text-[17px] font-semibold">{title}</h3>
                  <p className="mt-2 text-[14px] leading-[1.5] text-[#94a3b8]">{text}</p>
                </article>
              );
            })}
          </div>
        </section>

        <section id="integrations" className="min-h-[560px] px-5 py-24 sm:px-8 lg:px-16">
          <SectionHeading badge={t.integrationsBadge} title={t.integrationsTitle} lead={t.integrationsLead} />
          <div className="mx-auto mt-14 grid max-w-[1312px] gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {integrations.map(({ Icon, name, ru, en }) => (
              <article key={name} className="flex min-h-[69px] items-center gap-4 rounded-xl border border-[#202946] bg-[#13172a] px-4 py-3">
                <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[#17213d] text-[#3b82f6]">
                  <Icon className="h-[18px] w-[18px]" />
                </span>
                <div className="min-w-0">
                  <h3 className="truncate text-[14px] font-semibold">{name}</h3>
                  <p className="mt-0.5 truncate text-[11px] text-[#7b89a4]">{lang === "ru" ? ru : en}</p>
                </div>
              </article>
            ))}
          </div>
        </section>

        <section id="pricing" className="min-h-[880px] px-5 py-24 sm:px-8 lg:px-16">
          <SectionHeading badge={t.pricingBadge} title={t.pricingTitle} lead={t.pricingLead} />
          <div className="mx-auto mt-14 grid max-w-[1312px] gap-6 lg:grid-cols-3">
            {plans.map((plan) => {
              const [title, description, price, suffix] = plan[lang];
              const items = lang === "ru" ? plan.featuresRu : plan.featuresEn;
              return (
                <article key={title} className={`relative min-h-[480px] rounded-2xl border bg-[#13172a] p-10 ${plan.popular ? "border-[#3b82f6]" : "border-[#202946]"}`}>
                  {plan.popular && (
                    <span className="absolute right-10 top-10 rounded-full border border-[#244a85] bg-[#10213f] px-3 py-1.5 text-[11px] font-semibold uppercase text-[#3b82f6]">
                      {lang === "ru" ? "Популярный" : "Most popular"}
                    </span>
                  )}
                  <h3 className="text-[18px] font-semibold">{title}</h3>
                  <p className="mt-3 text-[14px] text-[#94a3b8]">{description}</p>
                  <div className="mt-9 flex items-end gap-2">
                    <span className="text-[40px] font-bold tracking-[-0.04em]">{price}</span>
                    {suffix && <span className="pb-2 text-[13px] text-[#60708d]">{suffix}</span>}
                  </div>
                  <ul className="mt-8 space-y-4">
                    {items.map((feature) => (
                      <li key={feature} className="flex items-center gap-2.5 text-[13px] text-[#94a3b8]">
                        <CheckCircle2 className="h-4 w-4 shrink-0 text-[#3b82f6]" />
                        {feature}
                      </li>
                    ))}
                  </ul>
                  <Link href="/max/register" className={`mt-9 inline-flex h-12 items-center justify-center rounded-lg px-7 text-[14px] font-semibold ${plan.popular ? "bg-gradient-to-r from-[#3b82f6] to-[#8b5cf6] text-white" : "border border-[#202946] text-white"}`}>
                    {lang === "ru" ? "Начать" : "Get Started Instantly"}
                  </Link>
                </article>
              );
            })}
          </div>
        </section>
      </main>

      <footer className="min-h-[430px] border-t border-[#0d1220] px-5 py-20 sm:px-8 lg:px-16">
        <div className="mx-auto grid max-w-[1312px] gap-16 md:grid-cols-[1fr_auto_auto]">
          <div>
            <BrandMark inverse label="MaxStudio" />
            <p className="mt-6 max-w-[360px] text-[14px] leading-6 text-[#7b89a4]">{t.footerLead}</p>
          </div>
          <div>
            <h3 className="text-[13px] font-semibold">Product</h3>
            <div className="mt-5 flex flex-col gap-4 text-[13px] text-[#94a3b8]">
              <a href="#features">{t.nav[0]}</a>
              <a href="#integrations">{t.nav[2]}</a>
              <Link href="/pricing">{t.nav[3]}</Link>
              <Link href="/changelog">Changelog</Link>
            </div>
          </div>
          <div>
            <h3 className="text-[13px] font-semibold">Company</h3>
            <div className="mt-5 flex flex-col gap-4 text-[13px] text-[#94a3b8]">
              <Link href="/about">{lang === "ru" ? "О компании" : "About Us"}</Link>
              <Link href="/security">{lang === "ru" ? "Безопасность" : "Security"}</Link>
              <Link href="/contact">{lang === "ru" ? "Связаться" : "Contact Sales"}</Link>
              <Link href="/legal/privacy">{lang === "ru" ? "Конфиденциальность" : "Privacy Policy"}</Link>
              <Link href="/legal/terms">{lang === "ru" ? "Условия" : "Terms of Service"}</Link>
            </div>
          </div>
        </div>
        <div className="mx-auto mt-24 flex max-w-[1312px] flex-col gap-4 border-t border-[#111626] pt-6 text-[12px] text-[#60708d] sm:flex-row sm:items-center sm:justify-between">
          <span>© 2026 MaxStudio by Omnia. All rights reserved.</span>
          <span className="inline-flex items-center gap-2"><Smartphone className="h-3.5 w-3.5" /> MAX Mini Apps</span>
        </div>
      </footer>
    </div>
  );
}
