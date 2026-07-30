import type { ComponentType } from "react";
import Link from "next/link";
import { getLocale } from "next-intl/server";
import {
  ArrowRight,
  ArrowUpRight,
  Blocks,
  Bot,
  CalendarDays,
  Check,
  CircleDot,
  ContactRound,
  CreditCard,
  Globe,
  PackageSearch,
  PanelsTopLeft,
  ShieldCheck,
  Smartphone,
  Store,
  Utensils,
} from "lucide-react";

import { LocaleSwitcher } from "@/components/LocaleSwitcher";
import { BrandMark } from "@/components/marketing/BrandMark";
import { Reveal } from "@/components/marketing/Reveal";

const COPY = {
  ru: {
    nav: {
      products: "Направления",
      process: "Как работаем",
      integrations: "Интеграции",
      login: "Войти",
      cta: "Открыть MAX Studio",
      available: "Доступно сейчас",
    },
    hero: {
      eyebrow: "Продуктовая студия Omnia",
      title: "Цифровой продукт — от идеи до запуска.",
      lead:
        "Сейчас в открытом доступе — конструктор мини-приложений и ботов для MAX. Остальные направления готовим к запуску.",
      primary: "Создать приложение для MAX",
      secondary: "Посмотреть направления",
      note: "Для ИП, ООО и самозанятых",
    },
    products: {
      eyebrow: "Направления разработки",
      title: "Один продукт — одна рабочая среда.",
      lead:
        "Мы разделили конструктор по задачам, чтобы в каждом сценарии оставались только нужные инструменты, требования и шаги запуска.",
      open: "Доступно",
      soon: "В разработке",
      enter: "Перейти в студию",
      learn: "Посмотреть направление",
    },
    max: {
      eyebrow: "MAX Mini Apps",
      title: "Не макет, а приложение, готовое к подключению.",
      lead:
        "Студия собирает интерфейс и серверную часть, помогает подключить проверенного MAX-бота, публикует HTTPS-версию и ведёт по обязательным шагам запуска.",
      cta: "Начать в MAX Studio",
      account: "Нужна регистрация и подтверждённый бизнес-профиль",
    },
    process: {
      eyebrow: "Как это устроено",
      title: "Понятный маршрут вместо бесконечного чата.",
      lead:
        "Пользователь принимает продуктовые решения. Сборка, инфраструктура и техническая проверка остаются внутри студии.",
    },
    integrations: {
      eyebrow: "Интеграционный слой",
      title: "Подключения будут жить отдельно от сгенерированного кода.",
      lead:
        "Секреты не попадут в браузер или промпт. Студия подключит сервис, проверит доступ, предложит сопоставление данных и покажет журнал обмена.",
      status: "В плане интеграций",
    },
    principles: {
      eyebrow: "Принципы продукта",
      title: "Меньше магии. Больше проверяемых состояний.",
    },
    footer:
      "Продуктовая студия для запуска цифровых сервисов. Сейчас доступно направление MAX.",
  },
  en: {
    nav: {
      products: "Products",
      process: "Workflow",
      integrations: "Integrations",
      login: "Sign in",
      cta: "Open MAX Studio",
      available: "Available now",
    },
    hero: {
      eyebrow: "Omnia product studio",
      title: "A digital product, from idea to launch.",
      lead:
        "MAX mini apps and bots are available now. The other product studios are being prepared for release.",
      primary: "Build for MAX",
      secondary: "Explore products",
      note: "For registered businesses and self-employed professionals",
    },
    products: {
      eyebrow: "Product studios",
      title: "One product type. One focused workspace.",
      lead:
        "Each studio keeps only the tools, requirements and launch steps that belong to its product.",
      open: "Available",
      soon: "In development",
      enter: "Open studio",
      learn: "View product",
    },
    max: {
      eyebrow: "MAX Mini Apps",
      title: "A launch-ready application, not a mockup.",
      lead:
        "The studio builds the interface and backend, connects a verified MAX bot, publishes an HTTPS version and guides you through every required launch step.",
      cta: "Start in MAX Studio",
      account: "Registration and a verified business profile are required",
    },
    process: {
      eyebrow: "How it works",
      title: "A clear route instead of an endless chat.",
      lead:
        "You make product decisions. The studio takes care of implementation, infrastructure and technical checks.",
    },
    integrations: {
      eyebrow: "Integration layer",
      title: "Connections stay outside generated application code.",
      lead:
        "Secrets never enter the browser or a prompt. The studio connects each service, verifies access, maps data and exposes a readable sync log.",
      status: "Integration roadmap",
    },
    principles: {
      eyebrow: "Product principles",
      title: "Less magic. More verifiable states.",
    },
    footer:
      "A product studio for launching digital services. MAX is available now.",
  },
} as const;

type ProductCard = {
  href: string;
  title: string;
  description: string;
  state: "open" | "soon";
  number: string;
  Icon: ComponentType<{ className?: string; strokeWidth?: number }>;
};

const PRODUCTS_RU: ProductCard[] = [
  {
    href: "/max",
    title: "MAX Mini Apps и боты",
    description:
      "Приложение внутри MAX, бот, webhook, HTTPS-публикация и контроль готовности.",
    state: "open",
    number: "01",
    Icon: Bot,
  },
  {
    href: "/web-apps",
    title: "Веб-приложения",
    description:
      "Личные кабинеты, CRM, каталоги, сервисы с базой данных и ролями.",
    state: "soon",
    number: "02",
    Icon: Globe,
  },
  {
    href: "/landings",
    title: "Лендинги",
    description:
      "Маркетинговые страницы с формами, аналитикой, доменом и публикацией.",
    state: "soon",
    number: "03",
    Icon: PanelsTopLeft,
  },
  {
    href: "/apps",
    title: "Мобильные приложения",
    description:
      "Самостоятельные приложения для iOS и Android с отдельным сценарием релиза.",
    state: "soon",
    number: "04",
    Icon: Smartphone,
  },
];

const PRODUCTS_EN: ProductCard[] = [
  {
    ...PRODUCTS_RU[0],
    title: "MAX Mini Apps and bots",
    description:
      "An in-MAX application, bot, webhook, HTTPS publishing and launch checks.",
  },
  {
    ...PRODUCTS_RU[1],
    title: "Web applications",
    description:
      "Customer portals, CRM systems, catalogues and role-based database products.",
  },
  {
    ...PRODUCTS_RU[2],
    title: "Landing pages",
    description:
      "Marketing pages with forms, analytics, domains and publishing.",
  },
  {
    ...PRODUCTS_RU[3],
    title: "Mobile applications",
    description:
      "Standalone iOS and Android applications with a dedicated release workflow.",
  },
];

const INTEGRATIONS = [
  { name: "ЮKassa", category: "Оплата и возвраты", Icon: CreditCard },
  { name: "r_keeper / iiko", category: "Меню и заказы", Icon: Utensils },
  { name: "YCLIENTS", category: "Онлайн-запись", Icon: CalendarDays },
  { name: "amoCRM / Битрикс24", category: "Лиды и клиенты", Icon: ContactRound },
  { name: "МойСклад / 1С", category: "Товары и остатки", Icon: PackageSearch },
  { name: "CDEK / доставка", category: "Расчёт и статусы", Icon: Store },
] as const;

export default async function LandingPage() {
  const locale = await getLocale();
  const copy = locale === "en" ? COPY.en : COPY.ru;
  const products = locale === "en" ? PRODUCTS_EN : PRODUCTS_RU;

  return (
    <div
      data-marketing
      className="min-h-svh bg-[#f2f0e9] text-[#171815] antialiased"
    >
      <MarketingNav copy={copy.nav} />

      <main>
        <section className="border-b border-[#171815]">
          <div className="mx-auto grid min-h-[calc(100svh-4rem)] max-w-[1500px] lg:grid-cols-[minmax(0,1.45fr)_minmax(360px,0.55fr)]">
            <div className="flex flex-col px-5 py-12 sm:px-8 lg:border-r lg:border-[#171815] lg:px-12 lg:py-16 xl:px-16">
              <Reveal>
                <p className="marketing-kicker">{copy.hero.eyebrow}</p>
              </Reveal>
              <div className="my-auto py-14 lg:py-16">
                <Reveal delay={0.05}>
                  <h1 className="max-w-[980px] text-[clamp(52px,6.4vw,104px)] font-semibold leading-[0.88] tracking-[-0.06em]">
                    {copy.hero.title}
                  </h1>
                </Reveal>
                <Reveal delay={0.12}>
                  <p className="mt-8 max-w-2xl text-[18px] leading-7 text-[#4f514c] sm:text-[20px] sm:leading-8">
                    {copy.hero.lead}
                  </p>
                </Reveal>
                <Reveal delay={0.18}>
                  <div className="mt-8 flex flex-col gap-5 border-t border-[#171815] pt-6 sm:flex-row sm:items-center sm:justify-between">
                  <div className="flex flex-wrap gap-3">
                    <Link
                      href="/max"
                      className="marketing-button marketing-button-primary"
                    >
                      {copy.hero.primary}
                      <ArrowRight className="h-4 w-4" />
                    </Link>
                    <a
                      href="#products"
                      className="marketing-button marketing-button-secondary"
                    >
                      {copy.hero.secondary}
                    </a>
                  </div>
                  <span className="flex items-center gap-2 text-sm text-[#595b56]">
                    <ShieldCheck className="h-4 w-4" />
                    {copy.hero.note}
                  </span>
                  </div>
                </Reveal>
              </div>
            </div>

            <div className="flex flex-col bg-[#1a1b18] p-5 text-[#f2f0e9] sm:p-8 lg:p-10">
              <div className="flex items-center justify-between border-b border-white/20 pb-5 text-xs uppercase tracking-[0.14em] text-white/50">
                <span>MAX Studio</span>
                <span>{copy.nav.available}</span>
              </div>
              <div className="my-auto py-14">
                <div className="mb-10 flex h-16 w-16 items-center justify-center border border-white/25 bg-[#315bd7]">
                  <Bot className="h-7 w-7" strokeWidth={1.6} />
                </div>
                <p className="max-w-md text-[36px] font-medium leading-[1.02] tracking-[-0.04em] sm:text-[44px]">
                  Бриф. Сборка. Бот. Публикация.
                </p>
                <div className="mt-12 space-y-0 border-t border-white/20">
                  {[
                    "Интерфейс и серверная логика",
                    "MAX Bridge и проверка initData",
                    "Webhook и постоянный HTTPS-адрес",
                    "Контроль обязательных шагов запуска",
                  ].map((item, index) => (
                    <div
                      key={item}
                      className="grid grid-cols-[32px_1fr] gap-3 border-b border-white/20 py-4 text-sm"
                    >
                      <span className="font-mono text-white/35">0{index + 1}</span>
                      <span>{item}</span>
                    </div>
                  ))}
                </div>
              </div>
              <Link
                href="/max"
                className="flex items-center justify-between border-t border-white/20 pt-5 text-sm font-medium"
              >
                <span>{copy.nav.cta}</span>
                <ArrowUpRight className="h-4 w-4" />
              </Link>
            </div>
          </div>
        </section>

        <section id="products" className="border-b border-[#171815]">
          <div className="mx-auto max-w-[1500px] px-5 py-20 sm:px-8 lg:px-12 lg:py-28 xl:px-16">
            <Reveal>
              <SectionIntro
                eyebrow={copy.products.eyebrow}
                title={copy.products.title}
                lead={copy.products.lead}
              />
            </Reveal>
            <div className="mt-14 grid border-l border-t border-[#171815] md:grid-cols-2">
              {products.map(({ href, title, description, state, number, Icon }) => (
                <Reveal key={href}>
                  <Link
                    href={href}
                    className="group flex min-h-[320px] flex-col border-b border-r border-[#171815] p-6 transition-colors hover:bg-[#e7e4db] sm:p-8"
                  >
                    <div className="flex items-start justify-between gap-6">
                      <span className="font-mono text-xs text-[#6a6c66]">{number}</span>
                      <span
                        className={
                          state === "open"
                            ? "product-state bg-[#315bd7] text-white"
                            : "product-state border border-[#a8aaa3] text-[#555751]"
                        }
                      >
                        {state === "open" ? copy.products.open : copy.products.soon}
                      </span>
                    </div>
                    <div className="mt-auto">
                      <Icon
                        className="mb-7 h-8 w-8 transition-transform group-hover:-translate-y-1"
                        strokeWidth={1.45}
                      />
                      <div className="flex items-end justify-between gap-6">
                        <div>
                          <h2 className="text-[30px] font-semibold tracking-[-0.04em] sm:text-[38px]">
                            {title}
                          </h2>
                          <p className="mt-3 max-w-lg text-[15px] leading-6 text-[#5d5f59]">
                            {description}
                          </p>
                        </div>
                        <ArrowUpRight className="h-5 w-5 shrink-0 transition-transform group-hover:translate-x-1 group-hover:-translate-y-1" />
                      </div>
                    </div>
                  </Link>
                </Reveal>
              ))}
            </div>
          </div>
        </section>

        <section className="border-b border-[#171815] bg-[#315bd7] text-white">
          <div className="mx-auto grid max-w-[1500px] lg:grid-cols-2">
            <div className="px-5 py-20 sm:px-8 lg:border-r lg:border-white/30 lg:px-12 lg:py-28 xl:px-16">
              <Reveal>
                <p className="marketing-kicker text-white/60">{copy.max.eyebrow}</p>
                <h2 className="mt-7 max-w-3xl text-[clamp(44px,5vw,76px)] font-semibold leading-[0.93] tracking-[-0.055em]">
                  {copy.max.title}
                </h2>
                <p className="mt-8 max-w-xl text-lg leading-7 text-white/75">
                  {copy.max.lead}
                </p>
                <Link
                  href="/max"
                  className="marketing-button mt-10 border-white bg-white text-[#171815] hover:bg-transparent hover:text-white"
                >
                  {copy.max.cta}
                  <ArrowRight className="h-4 w-4" />
                </Link>
                <p className="mt-5 flex items-center gap-2 text-sm text-white/60">
                  <ShieldCheck className="h-4 w-4" />
                  {copy.max.account}
                </p>
              </Reveal>
            </div>
            <div className="grid grid-cols-2">
              {[
                ["01", "Продуктовый бриф", "Вопросы зависят от типа приложения."],
                ["02", "Рабочая сборка", "Мобильное превью и проверяемый build."],
                ["03", "MAX-бот", "Проверка токена и защищённый webhook."],
                ["04", "Публикация", "HTTPS-адрес и контроль готовности."],
              ].map(([number, title, text]) => (
                <div
                  key={number}
                  className="flex min-h-[260px] flex-col border-b border-r border-white/30 p-6 sm:p-8"
                >
                  <span className="font-mono text-xs text-white/50">{number}</span>
                  <div className="mt-auto">
                    <h3 className="text-xl font-medium tracking-[-0.025em]">{title}</h3>
                    <p className="mt-2 text-sm leading-5 text-white/60">{text}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section id="process" className="border-b border-[#171815]">
          <div className="mx-auto max-w-[1500px] px-5 py-20 sm:px-8 lg:px-12 lg:py-28 xl:px-16">
            <Reveal>
              <SectionIntro
                eyebrow={copy.process.eyebrow}
                title={copy.process.title}
                lead={copy.process.lead}
              />
            </Reveal>
            <div className="mt-14 grid border-t border-[#171815] lg:grid-cols-3">
              {[
                {
                  title: "Вы выбираете результат",
                  text: "Тип продукта, бизнес-сценарий, функции, контент и визуальный характер.",
                  Icon: CircleDot,
                },
                {
                  title: "Студия ведёт сборку",
                  text: "История не перезапускается после обновления страницы, а статусы берутся с сервера.",
                  Icon: Blocks,
                },
                {
                  title: "Запуск проверяется фактами",
                  text: "Готовность build, HTTPS, бота и webhook подтверждается реальными ответами систем.",
                  Icon: Check,
                },
              ].map(({ title, text, Icon }) => (
                <div
                  key={title}
                  className="border-b border-[#171815] py-8 lg:border-r lg:px-8 first:lg:pl-0 last:lg:border-r-0"
                >
                  <Icon className="h-6 w-6" strokeWidth={1.5} />
                  <h3 className="mt-12 text-2xl font-semibold tracking-[-0.035em]">
                    {title}
                  </h3>
                  <p className="mt-3 max-w-sm text-[15px] leading-6 text-[#5d5f59]">
                    {text}
                  </p>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section
          id="integrations"
          className="border-b border-[#171815] bg-[#dedbd1]"
        >
          <div className="mx-auto max-w-[1500px] px-5 py-20 sm:px-8 lg:px-12 lg:py-28 xl:px-16">
            <Reveal>
              <SectionIntro
                eyebrow={copy.integrations.eyebrow}
                title={copy.integrations.title}
                lead={copy.integrations.lead}
              />
            </Reveal>
            <div className="mt-14 grid border-l border-t border-[#171815] sm:grid-cols-2 lg:grid-cols-3">
              {INTEGRATIONS.map(({ name, category, Icon }) => (
                <div
                  key={name}
                  className="min-h-[190px] border-b border-r border-[#171815] p-6"
                >
                  <div className="flex items-start justify-between">
                    <Icon className="h-6 w-6" strokeWidth={1.5} />
                    <span className="text-[10px] uppercase tracking-[0.14em] text-[#666862]">
                      {copy.integrations.status}
                    </span>
                  </div>
                  <div className="mt-14">
                    <h3 className="text-xl font-semibold tracking-[-0.025em]">{name}</h3>
                    <p className="mt-1 text-sm text-[#61635e]">{category}</p>
                  </div>
                </div>
              ))}
            </div>
            <p className="mt-6 max-w-3xl text-sm leading-6 text-[#585a55]">
              Сначала выпускаем ЮKassa и ресторанный контур r_keeper / iiko.
              OAuth, лицензии поставщика и локальные агенты останутся обязательными
              там, где этого требует сам сервис.
            </p>
          </div>
        </section>

        <section className="bg-[#1a1b18] text-[#f2f0e9]">
          <div className="mx-auto grid max-w-[1500px] lg:grid-cols-[0.72fr_1.28fr]">
            <div className="px-5 py-20 sm:px-8 lg:border-r lg:border-white/20 lg:px-12 lg:py-28 xl:px-16">
              <p className="marketing-kicker text-white/45">
                {copy.principles.eyebrow}
              </p>
              <h2 className="mt-7 text-[42px] font-semibold leading-[0.98] tracking-[-0.05em] sm:text-[56px]">
                {copy.principles.title}
              </h2>
            </div>
            <div className="divide-y divide-white/20">
              {[
                [
                  "01",
                  "Никаких секретов в коде приложения",
                  "Токены и ключи хранятся зашифрованно в интеграционном слое.",
                ],
                [
                  "02",
                  "Никаких ложных галочек",
                  "Шаг считается готовым только после ответа API, build или HTTPS-проверки.",
                ],
                [
                  "03",
                  "Никаких универсальных обещаний",
                  "Если нужны договор, лицензия или действие в кабинете поставщика — студия показывает это заранее.",
                ],
              ].map(([number, title, text]) => (
                <div
                  key={number}
                  className="grid gap-5 px-5 py-8 sm:grid-cols-[48px_1fr] sm:px-8 lg:px-12"
                >
                  <span className="font-mono text-xs text-white/35">{number}</span>
                  <div>
                    <h3 className="text-xl font-medium">{title}</h3>
                    <p className="mt-2 max-w-xl text-sm leading-6 text-white/50">
                      {text}
                    </p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </section>
      </main>

      <MarketingFooter copy={copy.footer} />
    </div>
  );
}

function MarketingNav({ copy }: { copy: (typeof COPY)["ru"]["nav"] | (typeof COPY)["en"]["nav"] }) {
  return (
    <header className="sticky top-0 z-50 border-b border-[#171815] bg-[#f2f0e9]/95 backdrop-blur">
      <div className="mx-auto flex h-16 max-w-[1500px] items-center justify-between px-5 sm:px-8 lg:px-12 xl:px-16">
        <BrandMark />
        <nav className="hidden items-center gap-8 text-sm lg:flex">
          <a href="#products" className="hover:opacity-55">
            {copy.products}
          </a>
          <a href="#process" className="hover:opacity-55">
            {copy.process}
          </a>
          <a href="#integrations" className="hover:opacity-55">
            {copy.integrations}
          </a>
        </nav>
        <div className="flex items-center gap-3">
          <div className="hidden sm:block">
            <LocaleSwitcher />
          </div>
          <Link
            href="/login"
            className="hidden text-sm hover:opacity-55 sm:inline-flex"
          >
            {copy.login}
          </Link>
          <Link
            href="/max"
            className="inline-flex h-9 items-center gap-2 border border-[#171815] bg-[#171815] px-4 text-sm font-medium text-white transition-colors hover:bg-[#315bd7]"
          >
            <span className="sm:hidden">MAX Studio</span>
            <span className="hidden sm:inline">{copy.cta}</span>
            <ArrowUpRight className="h-3.5 w-3.5" />
          </Link>
        </div>
      </div>
    </header>
  );
}

function SectionIntro({
  eyebrow,
  title,
  lead,
}: {
  eyebrow: string;
  title: string;
  lead: string;
}) {
  return (
    <div className="grid gap-8 lg:grid-cols-[0.7fr_1.3fr] lg:gap-16">
      <p className="marketing-kicker">{eyebrow}</p>
      <div>
        <h2 className="max-w-4xl text-[clamp(40px,5vw,72px)] font-semibold leading-[0.94] tracking-[-0.055em]">
          {title}
        </h2>
        <p className="mt-7 max-w-2xl text-[17px] leading-7 text-[#5d5f59]">
          {lead}
        </p>
      </div>
    </div>
  );
}

function MarketingFooter({ copy }: { copy: string }) {
  return (
    <footer className="border-t border-[#171815] bg-[#f2f0e9]">
      <div className="mx-auto grid max-w-[1500px] gap-10 px-5 py-12 sm:px-8 md:grid-cols-[1fr_auto] lg:px-12 xl:px-16">
        <div>
          <BrandMark />
          <p className="mt-5 max-w-md text-sm leading-6 text-[#61635e]">{copy}</p>
        </div>
        <div className="grid grid-cols-2 gap-x-12 gap-y-3 text-sm">
          <Link href="/max">MAX Studio</Link>
          <Link href="/legal/terms">Условия</Link>
          <Link href="/max/register">Регистрация</Link>
          <Link href="/legal/privacy">Политика данных</Link>
          <Link href="/login">Войти</Link>
          <Link href="/legal/refunds">Оплата и возвраты</Link>
        </div>
        <div className="border-t border-[#171815] pt-5 text-xs text-[#696b65] md:col-span-2">
          © 2026 Omnia · Россия
        </div>
      </div>
    </footer>
  );
}
