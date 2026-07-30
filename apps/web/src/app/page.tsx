import type { LucideIcon } from "lucide-react";
import {
  ArrowRight,
  ArrowUpRight,
  BarChart3,
  Blocks,
  Bot,
  Check,
  CircleDollarSign,
  Code2,
  Database,
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

const RU = {
  nav: {
    result: "Что получите",
    products: "Направления",
    process: "Как работает",
    integrations: "Интеграции",
    login: "Войти",
    cta: "Создать MAX-приложение",
  },
  hero: {
    badge: "MAX Mini Apps · доступно сейчас",
    title: "Полноценное приложение для MAX — без команды разработки",
    lead:
      "Опишите задачу обычными словами. Omnia соберёт интерфейс и серверную часть, подключит бота, опубликует HTTPS-версию и проведёт до запуска.",
    primary: "Начать создание",
    secondary: "Посмотреть результат",
    owner: "Для ООО, ИП и самозанятых",
  },
  result: {
    eyebrow: "Результат на выходе",
    title: "Не картинка и не прототип. Работающий цифровой сервис.",
    lead:
      "Внутри одного проекта остаются код, данные, публикация, бот и подключения к бизнес-системам.",
  },
  products: {
    eyebrow: "Направления Omnia",
    title: "Отдельный билдер под каждый тип продукта",
    lead:
      "Сейчас открыт MAX Studio. Остальные направления получат собственные сценарии, а не один универсальный чат.",
    open: "Работает",
    soon: "В разработке",
  },
  process: {
    eyebrow: "Путь до запуска",
    title: "Понятные этапы и проверяемые статусы",
    lead:
      "Вы принимаете продуктовые решения. Студия берёт на себя реализацию, инфраструктуру и технические проверки.",
  },
  integrations: {
    eyebrow: "Integration Hub",
    title: "Платежи, CRM, ресторанные и учётные системы",
    lead:
      "Подключения настраиваются отдельно от сгенерированного кода. Секреты шифруются, а доступ проверяется реальным запросом к сервису.",
  },
  final: {
    eyebrow: "MAX Studio уже доступна",
    title: "Начните с задачи. Технические шаги студия соберёт в один маршрут.",
    cta: "Создать приложение",
  },
} as const;

const EN = {
  nav: {
    result: "Deliverable",
    products: "Products",
    process: "Workflow",
    integrations: "Integrations",
    login: "Sign in",
    cta: "Build a MAX app",
  },
  hero: {
    badge: "MAX Mini Apps · available now",
    title: "A complete MAX application — without a development team",
    lead:
      "Describe the product in plain language. Omnia builds the interface and backend, connects the bot, publishes an HTTPS version and guides the launch.",
    primary: "Start building",
    secondary: "See the deliverable",
    owner: "For companies, sole proprietors and self-employed professionals",
  },
  result: {
    eyebrow: "The deliverable",
    title: "Not an image or a prototype. A working digital service.",
    lead:
      "Code, data, publishing, bot configuration and business connections stay in one project.",
  },
  products: {
    eyebrow: "Omnia product studios",
    title: "A focused builder for each product type",
    lead:
      "MAX Studio is open now. Other products will receive dedicated workflows instead of one generic chat.",
    open: "Available",
    soon: "In development",
  },
  process: {
    eyebrow: "Launch workflow",
    title: "Clear stages and verifiable states",
    lead:
      "You make product decisions. The studio takes care of implementation, infrastructure and technical checks.",
  },
  integrations: {
    eyebrow: "Integration Hub",
    title: "Payments, CRM, restaurant and inventory systems",
    lead:
      "Connections live outside generated code. Secrets are encrypted and credentials are verified against the actual provider.",
  },
  final: {
    eyebrow: "MAX Studio is available now",
    title: "Start with the product. The studio turns technical steps into one route.",
    cta: "Build an application",
  },
} as const;

type Product = {
  href: string;
  title: string;
  description: string;
  active: boolean;
  Icon: LucideIcon;
};

const PRODUCTS_RU: Product[] = [
  {
    href: "/max",
    title: "MAX Mini Apps и боты",
    description:
      "Интерфейс внутри MAX, база данных, бот, webhook, HTTPS и контроль запуска.",
    active: true,
    Icon: Bot,
  },
  {
    href: "/web-apps",
    title: "Веб-приложения",
    description:
      "Личные кабинеты, CRM, каталоги и сервисы с ролями и базой данных.",
    active: false,
    Icon: Globe2,
  },
  {
    href: "/landings",
    title: "Лендинги",
    description:
      "Маркетинговые страницы, формы, аналитика, домены и публикация.",
    active: false,
    Icon: PanelsTopLeft,
  },
  {
    href: "/apps",
    title: "Мобильные приложения",
    description:
      "Самостоятельные продукты для iOS и Android с отдельным релизом.",
    active: false,
    Icon: Smartphone,
  },
];

const PRODUCTS_EN: Product[] = [
  {
    ...PRODUCTS_RU[0],
    title: "MAX Mini Apps and bots",
    description:
      "In-MAX interface, database, bot, webhook, HTTPS and launch checks.",
  },
  {
    ...PRODUCTS_RU[1],
    title: "Web applications",
    description:
      "Customer portals, CRM systems and role-based database products.",
  },
  {
    ...PRODUCTS_RU[2],
    title: "Landing pages",
    description:
      "Marketing pages, forms, analytics, domains and publishing.",
  },
  {
    ...PRODUCTS_RU[3],
    title: "Mobile applications",
    description:
      "Standalone iOS and Android products with a dedicated release workflow.",
  },
];

const DELIVERABLES_RU = [
  {
    title: "Интерфейс и код",
    text: "Полноценный проект, который можно продолжать развивать.",
    Icon: Code2,
  },
  {
    title: "Сервер и данные",
    text: "Backend, база данных, роли и бизнес-логика — когда они нужны продукту.",
    Icon: Database,
  },
  {
    title: "Публикация",
    text: "Постоянный HTTPS-адрес, deployment и проверка доступности.",
    Icon: Rocket,
  },
  {
    title: "Интеграции",
    text: "Бот и проверенные подключения к платежам, CRM, аналитике и учётным системам.",
    Icon: Plug,
  },
] as const;

const DELIVERABLES_EN = [
  {
    title: "Interface and code",
    text: "A complete project that can keep evolving after launch.",
    Icon: Code2,
  },
  {
    title: "Backend and data",
    text: "Backend, database, roles and product logic whenever the service needs them.",
    Icon: Database,
  },
  {
    title: "Publishing",
    text: "A permanent HTTPS address, deployment and availability checks.",
    Icon: Rocket,
  },
  {
    title: "Integrations",
    text: "Bot and verified connections to payments, CRM, analytics and inventory systems.",
    Icon: Plug,
  },
] as const;

const PROCESS_RU = [
  {
    step: "01",
    title: "Опишите продукт",
    text: "Студия уточнит аудиторию, сценарий, функции и визуальный характер.",
    Icon: MessageSquareText,
  },
  {
    step: "02",
    title: "Следите за сборкой",
    text: "Код и рабочее мобильное превью появляются прямо в проекте.",
    Icon: Blocks,
  },
  {
    step: "03",
    title: "Подключите сервисы",
    text: "MAX-бот, ЮKassa, CRM и учётные системы проверяются через API.",
    Icon: Webhook,
  },
  {
    step: "04",
    title: "Запустите",
    text: "Публикация, HTTPS и обязательные шаги подтверждаются фактами.",
    Icon: Check,
  },
] as const;

const PROCESS_EN = [
  {
    step: "01",
    title: "Describe the product",
    text: "The studio clarifies the audience, workflow, features and visual direction.",
    Icon: MessageSquareText,
  },
  {
    step: "02",
    title: "Follow the build",
    text: "Code and a working mobile preview appear directly in the project.",
    Icon: Blocks,
  },
  {
    step: "03",
    title: "Connect services",
    text: "The MAX bot, YooKassa, CRM and business systems are verified through their APIs.",
    Icon: Webhook,
  },
  {
    step: "04",
    title: "Launch",
    text: "Publishing, HTTPS and mandatory steps are confirmed by actual system state.",
    Icon: Check,
  },
] as const;

const INTEGRATIONS_RU = [
  {
    name: "ЮKassa",
    task: "Проверка магазина",
    status: "Можно подключить",
    active: true,
    Icon: CircleDollarSign,
  },
  {
    name: "iikoCloud",
    task: "Проверка API",
    status: "Можно подключить",
    active: true,
    Icon: Store,
  },
  {
    name: "Битрикс24",
    task: "Проверка webhook",
    status: "Можно подключить",
    active: true,
    Icon: UsersRound,
  },
  {
    name: "МойСклад",
    task: "Проверка токена",
    status: "Можно подключить",
    active: true,
    Icon: PackageSearch,
  },
  {
    name: "Яндекс Метрика",
    task: "Проверка счётчика",
    status: "Можно подключить",
    active: true,
    Icon: BarChart3,
  },
  {
    name: "r_keeper",
    task: "Локальный коннектор",
    status: "Готовим",
    active: false,
    Icon: Store,
  },
] as const;

const INTEGRATIONS_EN = [
  {
    name: "YooKassa",
    task: "Store verification",
    status: "Connect now",
    active: true,
    Icon: CircleDollarSign,
  },
  {
    name: "iikoCloud",
    task: "API verification",
    status: "Connect now",
    active: true,
    Icon: Store,
  },
  {
    name: "Bitrix24",
    task: "Webhook verification",
    status: "Connect now",
    active: true,
    Icon: UsersRound,
  },
  {
    name: "MoySklad",
    task: "Token verification",
    status: "Connect now",
    active: true,
    Icon: PackageSearch,
  },
  {
    name: "Yandex Metrica",
    task: "Counter verification",
    status: "Connect now",
    active: true,
    Icon: BarChart3,
  },
  {
    name: "r_keeper",
    task: "Local connector",
    status: "In progress",
    active: false,
    Icon: Store,
  },
] as const;

export default async function LandingPage() {
  const locale = await getLocale();
  const copy = locale === "en" ? EN : RU;
  const products = locale === "en" ? PRODUCTS_EN : PRODUCTS_RU;
  const deliverables =
    locale === "en" ? DELIVERABLES_EN : DELIVERABLES_RU;
  const process = locale === "en" ? PROCESS_EN : PROCESS_RU;
  const integrations =
    locale === "en" ? INTEGRATIONS_EN : INTEGRATIONS_RU;

  return (
    <div
      data-marketing
      className="min-h-svh bg-[#10110f] text-white antialiased"
    >
      <MarketingNav copy={copy.nav} />

      <main>
        <section className="border-b border-white/[0.1]">
          <div className="mx-auto grid min-h-[calc(100svh-64px)] max-w-[1440px] lg:grid-cols-[minmax(0,0.92fr)_minmax(520px,1.08fr)]">
            <div className="flex flex-col justify-center px-5 py-12 sm:px-8 lg:border-r lg:border-white/[0.1] lg:px-12 lg:py-10 xl:px-16">
              <Reveal>
                <span className="inline-flex w-fit items-center gap-2 border border-[#315bd7]/60 bg-[#315bd7]/10 px-3 py-1.5 text-[11px] font-medium text-[#9bb1f8]">
                  <span className="h-1.5 w-1.5 rounded-full bg-[#5f82ec]" />
                  {copy.hero.badge}
                </span>
              </Reveal>
              <Reveal delay={0.05}>
                <h1 className="mt-6 max-w-3xl text-[clamp(42px,4.5vw,64px)] font-semibold leading-[0.97] tracking-[-0.052em]">
                  {copy.hero.title}
                </h1>
              </Reveal>
              <Reveal delay={0.1}>
                <p className="mt-6 max-w-2xl text-[16px] leading-7 text-white/55 sm:text-[17px]">
                  {copy.hero.lead}
                </p>
              </Reveal>
              <Reveal delay={0.15}>
                <div className="mt-7 flex flex-wrap gap-3">
                  <Link
                    href="/max"
                    className="inline-flex h-12 items-center gap-2 bg-[#315bd7] px-5 text-sm font-semibold transition-colors hover:bg-[#4169df]"
                  >
                    {copy.hero.primary}
                    <ArrowRight className="h-4 w-4" />
                  </Link>
                  <a
                    href="#result"
                    className="inline-flex h-12 items-center gap-2 border border-white/[0.16] px-5 text-sm font-medium text-white/70 transition-colors hover:border-white/30 hover:text-white"
                  >
                    {copy.hero.secondary}
                  </a>
                </div>
              </Reveal>
              <Reveal delay={0.2}>
                <div className="mt-6 flex flex-wrap gap-x-6 gap-y-3 border-t border-white/[0.1] pt-5 text-xs text-white/38">
                  <span className="flex items-center gap-2">
                    <ShieldCheck className="h-4 w-4 text-[#7897f4]" />
                    {copy.hero.owner}
                  </span>
                  <span className="flex items-center gap-2">
                    <Check className="h-4 w-4 text-emerald-400" />
                    Статусы из реальных систем
                  </span>
                </div>
              </Reveal>
            </div>

            <div className="flex items-center bg-[#151613] px-5 py-12 sm:px-8 lg:px-10 xl:px-14">
              <Reveal className="w-full" delay={0.08} y={16}>
                <ProductPreview />
              </Reveal>
            </div>
          </div>
        </section>

        <section id="result" className="border-b border-white/[0.1]">
          <div className="mx-auto max-w-[1440px] px-5 py-20 sm:px-8 lg:px-12 lg:py-24 xl:px-16">
            <SectionIntro
              eyebrow={copy.result.eyebrow}
              title={copy.result.title}
              lead={copy.result.lead}
            />
            <div className="mt-12 grid border-l border-t border-white/[0.1] sm:grid-cols-2 lg:grid-cols-4">
              {deliverables.map(({ title, text, Icon }, index) => (
                <Reveal key={title} delay={index * 0.04}>
                  <article className="flex min-h-[250px] flex-col border-b border-r border-white/[0.1] bg-[#141513] p-6">
                    <div className="flex items-center justify-between">
                      <Icon className="h-5 w-5 text-[#7897f4]" />
                      <span className="font-mono text-[10px] text-white/20">
                        0{index + 1}
                      </span>
                    </div>
                    <div className="mt-auto">
                      <h3 className="text-lg font-semibold">{title}</h3>
                      <p className="mt-3 text-sm leading-6 text-white/42">
                        {text}
                      </p>
                    </div>
                  </article>
                </Reveal>
              ))}
            </div>
          </div>
        </section>

        <section id="products" className="border-b border-white/[0.1] bg-[#151613]">
          <div className="mx-auto max-w-[1440px] px-5 py-20 sm:px-8 lg:px-12 lg:py-24 xl:px-16">
            <SectionIntro
              eyebrow={copy.products.eyebrow}
              title={copy.products.title}
              lead={copy.products.lead}
            />
            <div className="mt-12 grid gap-3 lg:grid-cols-4">
              {products.map(({ href, title, description, active, Icon }) => (
                <Link
                  key={href}
                  href={href}
                  className="group flex min-h-[300px] flex-col border border-white/[0.1] bg-[#10110f] p-5 transition-colors hover:border-white/[0.22]"
                >
                  <div className="flex items-start justify-between">
                    <span className="flex h-10 w-10 items-center justify-center border border-white/[0.1] bg-white/[0.03]">
                      <Icon className="h-5 w-5 text-[#7897f4]" />
                    </span>
                    <span
                      className={
                        active
                          ? "bg-[#315bd7] px-2 py-1 text-[10px] font-medium uppercase tracking-[0.12em]"
                          : "border border-white/[0.1] px-2 py-1 text-[10px] uppercase tracking-[0.12em] text-white/30"
                      }
                    >
                      {active ? copy.products.open : copy.products.soon}
                    </span>
                  </div>
                  <div className="mt-auto">
                    <h3 className="text-xl font-semibold tracking-[-0.025em]">
                      {title}
                    </h3>
                    <p className="mt-3 text-sm leading-6 text-white/42">
                      {description}
                    </p>
                    <div className="mt-5 flex items-center justify-between border-t border-white/[0.08] pt-4 text-xs text-white/35">
                      <span>{active ? "Открыть билдер" : "Подробнее"}</span>
                      <ArrowUpRight className="h-4 w-4 transition-transform group-hover:translate-x-0.5 group-hover:-translate-y-0.5" />
                    </div>
                  </div>
                </Link>
              ))}
            </div>
          </div>
        </section>

        <section id="process" className="border-b border-white/[0.1]">
          <div className="mx-auto max-w-[1440px] px-5 py-20 sm:px-8 lg:px-12 lg:py-24 xl:px-16">
            <SectionIntro
              eyebrow={copy.process.eyebrow}
              title={copy.process.title}
              lead={copy.process.lead}
            />
            <div className="mt-12 grid gap-px border border-white/[0.1] bg-white/[0.1] lg:grid-cols-4">
              {process.map(({ step, title, text, Icon }) => (
                <article key={step} className="bg-[#10110f] p-6">
                  <div className="flex items-center justify-between text-white/25">
                    <span className="font-mono text-xs">{step}</span>
                    <Icon className="h-5 w-5 text-[#7897f4]" />
                  </div>
                  <h3 className="mt-16 text-lg font-semibold">{title}</h3>
                  <p className="mt-3 text-sm leading-6 text-white/42">{text}</p>
                </article>
              ))}
            </div>
          </div>
        </section>

        <section
          id="integrations"
          className="border-b border-white/[0.1] bg-[#151613]"
        >
          <div className="mx-auto max-w-[1440px] px-5 py-20 sm:px-8 lg:px-12 lg:py-24 xl:px-16">
            <div className="grid gap-12 lg:grid-cols-[0.72fr_1.28fr]">
              <div>
                <p className="marketing-kicker text-[#7897f4]">
                  {copy.integrations.eyebrow}
                </p>
                <h2 className="mt-5 max-w-2xl text-[clamp(38px,4vw,58px)] font-semibold leading-[1] tracking-[-0.045em]">
                  {copy.integrations.title}
                </h2>
                <p className="mt-6 max-w-xl text-[15px] leading-7 text-white/45">
                  {copy.integrations.lead}
                </p>
                <Link
                  href="/max"
                  className="mt-8 inline-flex items-center gap-2 text-sm font-medium text-[#9bb1f8] hover:text-white"
                >
                  Перейти в MAX Studio
                  <ArrowRight className="h-4 w-4" />
                </Link>
              </div>
              <div className="grid gap-2 sm:grid-cols-2">
                {integrations.map(({ name, task, status, active, Icon }) => (
                  <article
                    key={name}
                    className="flex items-center gap-4 border border-white/[0.1] bg-[#10110f] p-4"
                  >
                    <span className="flex h-10 w-10 shrink-0 items-center justify-center border border-white/[0.1] text-[#7897f4]">
                      <Icon className="h-[18px] w-[18px]" />
                    </span>
                    <div className="min-w-0 flex-1">
                      <h3 className="font-medium">{name}</h3>
                      <p className="mt-0.5 text-xs text-white/35">{task}</p>
                    </div>
                    <span
                      className={
                        active
                          ? "text-[10px] font-medium uppercase tracking-[0.1em] text-emerald-400"
                          : "text-[10px] uppercase tracking-[0.1em] text-white/25"
                      }
                    >
                      {status}
                    </span>
                  </article>
                ))}
              </div>
            </div>
          </div>
        </section>

        <section className="bg-[#315bd7]">
          <div className="mx-auto grid max-w-[1440px] gap-10 px-5 py-16 sm:px-8 lg:grid-cols-[1fr_auto] lg:items-end lg:px-12 lg:py-20 xl:px-16">
            <div>
              <p className="marketing-kicker text-white/60">
                {copy.final.eyebrow}
              </p>
              <h2 className="mt-5 max-w-4xl text-[clamp(38px,5vw,70px)] font-semibold leading-[0.98] tracking-[-0.05em]">
                {copy.final.title}
              </h2>
            </div>
            <Link
              href="/max"
              className="inline-flex h-12 items-center justify-center gap-2 bg-white px-6 text-sm font-semibold text-[#151613] hover:bg-[#eceff8]"
            >
              {copy.final.cta}
              <ArrowRight className="h-4 w-4" />
            </Link>
          </div>
        </section>
      </main>

      <MarketingFooter />
    </div>
  );
}

function ProductPreview() {
  return (
    <div className="border border-white/[0.14] bg-[#10110f] shadow-2xl shadow-black/30">
      <div className="flex items-center justify-between border-b border-white/[0.1] px-4 py-3">
        <div className="flex items-center gap-2">
          <span className="h-2 w-2 rounded-full bg-emerald-400" />
          <span className="text-xs font-medium text-white/70">Кофе Рядом</span>
        </div>
        <span className="text-[10px] uppercase tracking-[0.12em] text-white/25">
          MAX Studio
        </span>
      </div>
      <div className="grid gap-px bg-white/[0.1] sm:grid-cols-[0.82fr_1.18fr]">
        <div className="bg-[#151613] p-4 sm:p-5">
          <div className="mx-auto max-w-[230px] border border-white/[0.12] bg-[#f4f1e8] p-3 text-[#171815]">
            <div className="flex items-center justify-between text-[9px] font-medium">
              <span>КОФЕ РЯДОМ</span>
              <span>1 250 баллов</span>
            </div>
            <div className="mt-4 bg-[#262824] p-4 text-white">
              <p className="text-[9px] uppercase tracking-[0.14em] text-white/45">
                Персональное предложение
              </p>
              <p className="mt-2 text-xl font-semibold leading-5">
                Ваш шестой кофе — за наш счёт
              </p>
              <button
                type="button"
                className="mt-4 w-full bg-[#315bd7] px-3 py-2 text-[10px] font-semibold"
              >
                Активировать
              </button>
            </div>
            <div className="mt-3 grid grid-cols-2 gap-2">
              <div className="border border-black/10 p-2">
                <p className="text-[8px] text-black/45">Заказов</p>
                <p className="mt-1 text-sm font-semibold">12</p>
              </div>
              <div className="border border-black/10 p-2">
                <p className="text-[8px] text-black/45">Скидка</p>
                <p className="mt-1 text-sm font-semibold">10%</p>
              </div>
            </div>
          </div>
          <p className="mt-4 text-center text-[10px] text-white/25">
            Мобильное превью внутри MAX
          </p>
        </div>
        <div className="bg-[#10110f] p-5">
          <div className="flex items-start justify-between">
            <div>
              <p className="text-[10px] uppercase tracking-[0.14em] text-white/25">
                Готовность к запуску
              </p>
              <p className="mt-1 text-2xl font-semibold">86%</p>
            </div>
            <span className="border border-emerald-400/25 bg-emerald-400/[0.07] px-2 py-1 text-[9px] uppercase tracking-[0.1em] text-emerald-300">
              Сборка готова
            </span>
          </div>
          <div className="mt-4 h-1 bg-white/[0.08]">
            <div className="h-full w-[86%] bg-[#315bd7]" />
          </div>
          <div className="mt-6 space-y-0 border-t border-white/[0.1]">
            {[
              ["Приложение собрано", true],
              ["MAX-бот проверен", true],
              ["HTTPS опубликован", true],
              ["URL добавлен в MAX", false],
            ].map(([label, done]) => (
              <div
                key={String(label)}
                className="flex items-center gap-3 border-b border-white/[0.1] py-3 text-xs"
              >
                <span
                  className={
                    done
                      ? "flex h-4 w-4 items-center justify-center rounded-full border border-emerald-400/40 bg-emerald-400/10 text-emerald-300"
                      : "h-4 w-4 rounded-full border border-white/20"
                  }
                >
                  {done && <Check className="h-2.5 w-2.5" />}
                </span>
                <span className={done ? "text-white/45" : "text-white/80"}>
                  {label}
                </span>
              </div>
            ))}
          </div>
          <div className="mt-5 grid grid-cols-2 gap-2">
            <div className="border border-white/[0.1] p-3">
              <p className="text-[9px] uppercase tracking-[0.1em] text-white/25">
                Интеграции
              </p>
              <p className="mt-2 text-xs text-white/70">ЮKassa · iiko</p>
            </div>
            <div className="border border-white/[0.1] p-3">
              <p className="text-[9px] uppercase tracking-[0.1em] text-white/25">
                Публикация
              </p>
              <p className="mt-2 text-xs text-white/70">Постоянный HTTPS</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function MarketingNav({
  copy,
}: {
  copy: (typeof RU)["nav"] | (typeof EN)["nav"];
}) {
  return (
    <header className="sticky top-0 z-50 border-b border-white/[0.1] bg-[#10110f]/95 backdrop-blur-xl">
      <div className="mx-auto flex h-16 max-w-[1440px] items-center justify-between px-5 sm:px-8 lg:px-12 xl:px-16">
        <BrandMark inverse />
        <nav className="hidden items-center gap-7 text-xs text-white/50 lg:flex">
          <a href="#result" className="transition-colors hover:text-white">
            {copy.result}
          </a>
          <a href="#products" className="transition-colors hover:text-white">
            {copy.products}
          </a>
          <a href="#process" className="transition-colors hover:text-white">
            {copy.process}
          </a>
          <a href="#integrations" className="transition-colors hover:text-white">
            {copy.integrations}
          </a>
        </nav>
        <div className="flex items-center gap-2">
          <div className="hidden sm:block">
            <LocaleSwitcher />
          </div>
          <Link
            href="/login"
            className="hidden h-9 items-center px-3 text-xs text-white/50 transition-colors hover:text-white sm:flex"
          >
            {copy.login}
          </Link>
          <Link
            href="/max"
            className="inline-flex h-9 items-center gap-2 bg-[#315bd7] px-4 text-xs font-semibold text-white transition-colors hover:bg-[#4169df]"
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
    <Reveal>
      <div className="grid gap-7 lg:grid-cols-[0.65fr_1.35fr] lg:gap-12">
        <p className="marketing-kicker text-[#7897f4]">{eyebrow}</p>
        <div>
          <h2 className="max-w-4xl text-[clamp(36px,4.4vw,62px)] font-semibold leading-[1] tracking-[-0.045em]">
            {title}
          </h2>
          <p className="mt-6 max-w-2xl text-[15px] leading-7 text-white/45">
            {lead}
          </p>
        </div>
      </div>
    </Reveal>
  );
}

function MarketingFooter() {
  return (
    <footer className="border-t border-white/[0.1] bg-[#10110f]">
      <div className="mx-auto grid max-w-[1440px] gap-10 px-5 py-12 sm:px-8 md:grid-cols-[1fr_auto] lg:px-12 xl:px-16">
        <div>
          <BrandMark inverse />
          <p className="mt-5 max-w-md text-sm leading-6 text-white/35">
            Продуктовые билдеры для запуска цифровых сервисов. Сейчас доступно
            направление MAX.
          </p>
        </div>
        <div className="grid grid-cols-2 gap-x-10 gap-y-3 text-xs text-white/45">
          <Link href="/max" className="hover:text-white">
            MAX Studio
          </Link>
          <Link href="/legal/terms" className="hover:text-white">
            Условия
          </Link>
          <Link href="/max/register" className="hover:text-white">
            Регистрация
          </Link>
          <Link href="/legal/privacy" className="hover:text-white">
            Политика данных
          </Link>
          <Link href="/login" className="hover:text-white">
            Войти
          </Link>
          <Link href="/legal/refunds" className="hover:text-white">
            Оплата и возвраты
          </Link>
        </div>
        <div className="border-t border-white/[0.1] pt-5 text-xs text-white/25 md:col-span-2">
          © 2026 Omnia · Россия
        </div>
      </div>
    </footer>
  );
}
