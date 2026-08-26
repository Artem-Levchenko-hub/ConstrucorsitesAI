import {
  ArrowRight,
  Bot,
  Box,
  Check,
  ChevronDown,
  CircleDollarSign,
  Cloud,
  Code2,
  Database,
  ExternalLink,
  Globe2,
  LockKeyhole,
  MessageSquareText,
  PlugZap,
  Rocket,
  ShieldCheck,
  Smartphone,
  Store,
  Waypoints,
} from "lucide-react";
import Link from "next/link";

import { BrandMark } from "@/components/marketing/BrandMark";

const directions: Array<{
  icon: typeof Smartphone;
  title: string;
  text: string;
  state: string;
  href?: string;
}> = [
  {
    icon: Smartphone,
    title: "Приложения для MAX",
    text: "Мини-приложение, бот, платежи, данные и публикация — в одном сценарии.",
    state: "Доступно",
    href: "/max/product",
  },
  {
    icon: Globe2,
    title: "Веб-приложения",
    text: "Личные кабинеты, внутренние сервисы и продукты с авторизацией.",
    state: "В разработке",
  },
  {
    icon: Waypoints,
    title: "Лендинги",
    text: "Маркетинговые страницы, формы заявок, аналитика и собственный домен.",
    state: "В разработке",
  },
  {
    icon: Code2,
    title: "Обычные приложения",
    text: "Самостоятельные продукты с backend, базой данных и API.",
    state: "В разработке",
  },
];

const steps = [
  ["01", "Опишите продукт", "Расскажите агенту задачу обычными словами. Он задаст только необходимые вопросы."],
  ["02", "Проверьте сборку", "Интерфейс, backend и база данных появляются в живом мобильном превью."],
  ["03", "Подключите сервисы", "Платежи, CRM, учёт и аналитику можно авторизовать из защищённого кабинета."],
  ["04", "Опубликуйте", "Omnia подготовит HTTPS, контейнер, webhook и проверит готовность запуска."],
  ["05", "Управляйте после запуска", "Меняйте приложение через агента, следите за версиями и откатывайте обновления."],
] as const;

const integrations = [
  ["ЮKassa", "Оплата и возвраты"],
  ["MAX Bot API", "Бот и webhook"],
  ["iiko", "Каталог и заказы"],
  ["r_keeper", "Меню и касса"],
  ["Битрикс24", "Лиды и сделки"],
  ["amoCRM", "CRM-сценарии"],
  ["МойСклад", "Товары и остатки"],
  ["Яндекс Метрика", "События и конверсии"],
] as const;

const faqs = [
  ["Это макет или настоящее приложение?", "Настоящее приложение: исходный код, backend, рабочая база данных, HTTPS-адрес и управляемый контейнер."],
  ["Нужно ли уметь программировать?", "Нет. Требования, правки и публикация выполняются через интерфейс студии и диалог с агентом."],
  ["Что потребуется для запуска в MAX?", "Подтверждённый аккаунт владельца, MAX-бот от организации, ИП или самозанятого и его токен."],
  ["Можно использовать свою VPS?", "Да. Укажите IP и доступ — студия проверит сервер, развернёт проект и покажет результат диагностики."],
] as const;

const todayUpdates = [
  [
    Waypoints,
    "Real Hero planner",
    "Флагманская planner-модель оценивает загруженное фото и предлагает static, motion или video до запуска генерации.",
  ],
  [
    Rocket,
    "Настоящий render",
    "Подтверждённый video-план собран Seedance в 5-секундный MP4, проверен в preview и применён к dev-сайту.",
  ],
  [
    Smartphone,
    "Пять понятных шагов",
    "Фото → задача → план → preview → apply теперь разделены, а быстрый upload больше не теряет подтверждение прав.",
  ],
  [
    ShieldCheck,
    "Responsive и retry",
    "MAX и Hero-путь проверены реальными кликами без overflow; retry сохраняет выбранный формат и не применяет результат сам.",
  ],
] as const;

function ProductPreview() {
  return (
    <div className="relative mx-auto mt-14 max-w-[1120px] px-4 sm:px-8">
      <div className="overflow-hidden rounded-[14px] border border-white/15 bg-[#191b20] shadow-[0_40px_100px_rgba(0,0,0,.28)]">
        <div className="flex h-11 items-center justify-between border-b border-white/10 px-4 text-[10px] text-white/45">
          <span className="flex gap-1.5">
            <i className="size-2 rounded-full bg-white/25" />
            <i className="size-2 rounded-full bg-white/25" />
            <i className="size-2 rounded-full bg-white/25" />
          </span>
          <span className="font-mono">studio.omnia.ru/max/coffee</span>
          <span className="rounded-md border border-white/10 px-2 py-1">Сохранено</span>
        </div>
        <div className="grid min-h-[460px] md:grid-cols-[300px_1fr_250px]">
          <section className="border-r border-white/10 p-5">
            <p className="omnia-kicker text-white/35">Продуктовый агент</p>
            <div className="mt-8 space-y-4 text-[13px] leading-5">
              <div className="rounded-[10px] bg-white/[.06] p-3 text-white/70">
                Создай приложение кофейни: каталог, программа лояльности и заказ к выдаче.
              </div>
              <div className="border-l-2 border-[#4f81f7] pl-3 text-white/55">
                Собираю каталог и сценарий заказа. Затем подключу профиль MAX и проверю мобильную навигацию.
              </div>
              <div className="space-y-2 pt-3 text-[11px] text-white/35">
                <p className="flex items-center gap-2"><Check className="size-3.5 text-[#5ac77e]" /> Схема данных создана</p>
                <p className="flex items-center gap-2"><Check className="size-3.5 text-[#5ac77e]" /> Экран каталога готов</p>
                <p className="flex items-center gap-2"><span className="size-3 animate-pulse rounded-full bg-[#4f81f7]" /> Настраиваю корзину</p>
              </div>
            </div>
            <div className="mt-16 rounded-[8px] border border-white/10 p-3 text-[11px] text-white/30">
              Напишите, что изменить…
            </div>
          </section>
          <section className="grid place-items-center bg-[#121519] p-8">
            <div className="w-[230px] rounded-[32px] border-[7px] border-[#0d0d0c] bg-[#1c1e23] p-3 shadow-2xl">
              <div className="mx-auto mb-4 h-1 w-14 rounded-full bg-black/20" />
              <div className="rounded-[16px] bg-[#1c1e23] p-5 text-white">
                <p className="text-xs text-white/55">Доброе утро</p>
                <h3 className="mt-1 text-xl font-semibold">Кофе рядом</h3>
                <button className="mt-5 rounded-full bg-[#4f81f7] px-4 py-2 text-[11px] font-semibold text-[#121519]">Заказать</button>
              </div>
              <div className="mt-3 grid grid-cols-2 gap-2">
                {["Капучино", "Флэт уайт", "Раф", "Матча"].map((item, index) => (
                  <div key={item} className="rounded-[12px] bg-[#191b20] p-3 text-white shadow-sm">
                    <div className={`mb-4 aspect-square rounded-[8px] ${index % 2 ? "bg-[#6a95fa]" : "bg-[#4f81f7]"}`} />
                    <p className="text-[10px] font-semibold">{item}</p>
                  </div>
                ))}
              </div>
            </div>
          </section>
          <section className="hidden border-l border-white/10 p-5 md:block">
            <p className="omnia-kicker text-white/35">Готовность</p>
            <p className="mt-3 text-3xl font-semibold">4/7</p>
            <div className="mt-5 h-1.5 rounded-full bg-white/10">
              <div className="h-full w-4/7 rounded-full bg-[#4f81f7]" />
            </div>
            <div className="mt-7 space-y-4 text-[12px] text-white/45">
              {["Приложение собрано", "MAX-бот проверен", "Интеграции", "Публикация"].map((item, index) => (
                <p key={item} className="flex items-center gap-2">
                  <span className={`grid size-5 place-items-center rounded-full border ${index < 2 ? "border-[#5ac77e] text-[#5ac77e]" : "border-white/20"}`}>
                    {index < 2 && <Check className="size-3" />}
                  </span>
                  {item}
                </p>
              ))}
            </div>
            <button className="mt-9 w-full rounded-[8px] bg-[#4f81f7] py-3 text-xs font-semibold text-[#121519]">Продолжить настройку</button>
          </section>
        </div>
      </div>
    </div>
  );
}

export default function HomePage() {
  return (
    <main className="min-h-screen bg-[#121519] text-white">
      <section data-graphite-shell className="overflow-hidden border-b border-black bg-[#121519]">
        <header className="mx-auto flex h-18 max-w-[1320px] items-center justify-between px-5 sm:px-8">
          <BrandMark inverse />
          <nav className="hidden items-center gap-7 text-[13px] text-white/60 lg:flex">
            <a href="#products" className="hover:text-white">Продукты</a>
            <a href="#process" className="hover:text-white">Как работает</a>
            <a href="#integrations" className="hover:text-white">Интеграции</a>
            <Link href="/pricing" className="hover:text-white">Тарифы</Link>
            <Link href="/mvp" className="hover:text-white">Статус продукта</Link>
          </nav>
          <div className="flex items-center gap-2.5">
            <Link href="/login?next=/max" className="hidden px-3 py-2 text-[13px] text-white/65 hover:text-white sm:block">Войти</Link>
            <Link href="/max/register" className="omnia-button omnia-button-primary min-h-9 px-4 text-[13px]">Создать приложение</Link>
          </div>
        </header>

        <div className="mx-auto max-w-[1320px] px-5 pb-24 pt-22 text-center sm:px-8 sm:pt-28">
          <p className="omnia-kicker text-[#4f81f7]">Продуктовая AI-студия</p>
          <h1 className="mx-auto mt-6 max-w-[1000px] text-[48px] font-semibold leading-[.98] tracking-[-.055em] sm:text-[72px] lg:text-[92px]">
            Приложение для MAX
            <span className="block text-white/42">без команды разработки</span>
          </h1>
          <p className="mx-auto mt-8 max-w-[680px] text-[16px] leading-7 text-white/56 sm:text-[18px]">
            Опишите продукт обычными словами. Omnia соберёт интерфейс, backend и базу данных,
            подключит сервисы и доведёт приложение до запуска.
          </p>
          <div className="mt-9 flex flex-col items-center justify-center gap-3 sm:flex-row">
            <Link href="/max/register" className="omnia-button omnia-button-primary min-h-12 px-6">
              Начать бесплатно <ArrowRight className="size-4" />
            </Link>
            <Link href="/max/product" className="omnia-button min-h-12 border border-white/20 px-6 text-white hover:bg-white/5">
              Как устроен MAX Studio
            </Link>
          </div>
          <ProductPreview />
        </div>
      </section>

      <section className="border-b border-[#2b2d32] bg-[#191b20] px-5 py-16 sm:px-8">
        <div className="mx-auto max-w-[1320px]">
          <div className="flex flex-col gap-5 border-b border-[#2b2d32] pb-7 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <p className="omnia-kicker text-[#4f81f7]">31 июля 2026</p>
              <h2 className="mt-3 text-[32px] font-semibold tracking-[-.04em] sm:text-[42px]">
                Что сделано сегодня
              </h2>
            </div>
            <Link
              href="/otchet/"
              className="inline-flex min-h-11 items-center gap-2 text-sm font-semibold text-[#6a95fa]"
            >
              Открыть полный отчёт
              <ArrowRight className="size-4" />
            </Link>
          </div>
          <div className="grid divide-y divide-[#2b2d32] lg:grid-cols-4 lg:divide-x lg:divide-y-0">
            {todayUpdates.map(([Icon, title, text]) => (
              <article key={title} className="py-6 lg:px-6 lg:first:pl-0 lg:last:pr-0">
                <Icon className="size-5 text-[#4f81f7]" />
                <h3 className="mt-5 text-lg font-semibold">{title}</h3>
                <p className="mt-2 text-sm leading-6 text-[#9fa1b1]">{text}</p>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section id="products" className="mx-auto max-w-[1320px] px-5 py-24 sm:px-8 lg:py-32">
        <div className="grid gap-10 border-b border-[#2b2d32] pb-12 lg:grid-cols-[.75fr_1.25fr] lg:items-end">
          <div>
            <p className="omnia-kicker text-[#4f81f7]">Направления</p>
            <h2 className="mt-4 text-[38px] font-semibold leading-[1.05] tracking-[-.045em] sm:text-[52px]">Что можно создать</h2>
          </div>
          <p className="max-w-[620px] text-[16px] leading-7 text-[#9fa1b1] lg:justify-self-end">
            Один инженерный контур для разных цифровых продуктов. Сейчас открыт полный сценарий
            MAX Mini App; остальные направления появятся после закрытого тестирования.
          </p>
        </div>
        <div className="mt-8 grid gap-px overflow-hidden rounded-[12px] border border-[#2b2d32] bg-[#2b2d32] md:grid-cols-2">
          {directions.map(({ icon: Icon, title, text, state, href }, index) => {
            const body = (
              <article className="group min-h-[270px] bg-[#191b20] p-7 sm:p-9">
                <div className="flex items-start justify-between gap-6">
                  <span className={`grid size-11 place-items-center rounded-[8px] ${index === 0 ? "bg-[#4f81f7] text-[#121519]" : "bg-[#2b2d32] text-[#9fa1b1]"}`}>
                    <Icon className="size-5" />
                  </span>
                  <span className={`rounded-full border px-2.5 py-1 font-mono text-[9px] uppercase tracking-[.1em] ${index === 0 ? "border-[#4f81f7]/30 bg-[#4f81f7]/8 text-[#6a95fa]" : "border-[#2b2d32] text-[#828491]"}`}>
                    {state}
                  </span>
                </div>
                <h3 className="mt-14 text-2xl font-semibold tracking-[-.025em]">{title}</h3>
                <p className="mt-3 max-w-[460px] text-sm leading-6 text-[#9fa1b1]">{text}</p>
                {href && <span className="mt-7 inline-flex items-center gap-2 text-sm font-semibold text-[#6a95fa]">Подробнее <ArrowRight className="size-4" /></span>}
              </article>
            );
            return href ? <Link key={title} href={href}>{body}</Link> : <div key={title}>{body}</div>;
          })}
        </div>
      </section>

      <section id="process" data-graphite-shell className="bg-[#121519] px-5 py-24 sm:px-8 lg:py-32">
        <div className="mx-auto max-w-[1320px]">
          <p className="omnia-kicker text-[#4f81f7]">От идеи до продакшена</p>
          <div className="mt-4 grid gap-8 border-b border-white/15 pb-12 lg:grid-cols-2">
            <h2 className="text-[40px] font-semibold leading-[1.04] tracking-[-.045em] sm:text-[56px]">Пять шагов.<br />Без ручной сборки.</h2>
            <p className="max-w-[560px] text-base leading-7 text-white/50 lg:justify-self-end">
              Агент не отдаёт макет и не исчезает. Он ведёт проект через проверяемые этапы,
              сохраняет прогресс и продолжает с того же места после перезагрузки.
            </p>
          </div>
          <div className="divide-y divide-white/12">
            {steps.map(([number, title, text]) => (
              <article key={number} className="grid gap-4 py-8 sm:grid-cols-[72px_280px_1fr] sm:items-start">
                <span className="font-mono text-xs text-[#4f81f7]">{number}</span>
                <h3 className="text-xl font-semibold">{title}</h3>
                <p className="max-w-[620px] text-sm leading-6 text-white/48">{text}</p>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-[1320px] px-5 py-24 sm:px-8 lg:py-32">
        <div className="grid gap-12 lg:grid-cols-[1fr_1.1fr]">
          <div>
            <p className="omnia-kicker text-[#4f81f7]">На выходе</p>
            <h2 className="mt-4 max-w-[560px] text-[40px] font-semibold leading-[1.04] tracking-[-.045em] sm:text-[56px]">Полноценный продукт, а не прототип</h2>
            <p className="mt-6 max-w-[540px] text-base leading-7 text-[#9fa1b1]">
              Исходный код остаётся вашим. Приложение разворачивается в изолированном контейнере,
              получает рабочие данные, версии, мониторинг и возможность отката.
            </p>
            <Link href="/max/register" className="omnia-button omnia-button-primary mt-8">Создать MAX-приложение <ArrowRight className="size-4" /></Link>
          </div>
          <div className="grid gap-px overflow-hidden rounded-[12px] border border-[#2b2d32] bg-[#2b2d32] sm:grid-cols-2">
            {[
              [Box, "Исходный код", "Экспортируемый проект, который не привязан к закрытому редактору."],
              [Database, "Backend и данные", "Пользователи, каталог, заказы и история в рабочей базе."],
              [Cloud, "Постоянный HTTPS", "Управляемый хостинг Omnia или автоматический деплой на вашу VPS."],
              [ShieldCheck, "Эксплуатация", "Health-check, логи, версии, повторный деплой и безопасный откат."],
            ].map(([Icon, title, text]) => {
              const ItemIcon = Icon as typeof Box;
              return (
                <article key={String(title)} className="min-h-[220px] bg-[#191b20] p-7">
                  <ItemIcon className="size-5 text-[#4f81f7]" />
                  <h3 className="mt-12 text-xl font-semibold">{String(title)}</h3>
                  <p className="mt-3 text-sm leading-6 text-[#9fa1b1]">{String(text)}</p>
                </article>
              );
            })}
          </div>
        </div>
      </section>

      <section id="integrations" className="border-y border-[#2b2d32] bg-[#191b20] px-5 py-24 sm:px-8 lg:py-32">
        <div className="mx-auto max-w-[1320px]">
          <div className="grid gap-8 lg:grid-cols-2">
            <div>
              <p className="omnia-kicker text-[#4f81f7]">Интеграции</p>
              <h2 className="mt-4 text-[40px] font-semibold leading-[1.04] tracking-[-.045em] sm:text-[56px]">Сервисы бизнеса<br />подключаются из кабинета</h2>
            </div>
            <p className="max-w-[560px] text-base leading-7 text-[#9fa1b1] lg:justify-self-end">
              OAuth, защищённое хранилище ключей и автоматическая проверка соединения.
              Приложение получает только разрешённые действия — секреты не попадают в код.
            </p>
          </div>
          <div className="mt-14 grid border-l border-t border-[#2b2d32] sm:grid-cols-2 lg:grid-cols-4">
            {integrations.map(([name, text], index) => (
              <article key={name} className="min-h-[150px] border-b border-r border-[#2b2d32] p-6">
                <div className="flex items-center justify-between">
                  <PlugZap className="size-4 text-[#4f81f7]" />
                  <span className="font-mono text-[9px] text-[#828491]">{String(index + 1).padStart(2, "0")}</span>
                </div>
                <h3 className="mt-8 font-semibold">{name}</h3>
                <p className="mt-1 text-xs text-[#828491]">{text}</p>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section data-graphite-shell className="bg-[#121519] px-5 py-24 sm:px-8 lg:py-32">
        <div className="mx-auto grid max-w-[1320px] gap-14 lg:grid-cols-[.85fr_1.15fr]">
          <div>
            <p className="omnia-kicker text-[#4f81f7]">Инфраструктура</p>
            <h2 className="mt-4 text-[40px] font-semibold leading-[1.04] tracking-[-.045em] sm:text-[54px]">Запуск на Omnia<br />или на вашей VPS</h2>
            <p className="mt-6 max-w-[500px] text-sm leading-6 text-white/48">
              Для своей инфраструктуры достаточно IP, пользователя и пароля или SSH-ключа.
              Перед деплоем студия проверит сервер и покажет каждую операцию.
            </p>
          </div>
          <div className="grid gap-3">
            {[
              [Cloud, "Хостинг Omnia", "Постоянный HTTPS, обновления и мониторинг без настройки сервера."],
              [LockKeyhole, "Собственная VPS", "Проверка доступа, Docker, firewall, домен и развёртывание end-to-end."],
              [Rocket, "Управляемые версии", "Каждый релиз сохраняется; неудачную версию можно откатить."],
            ].map(([Icon, title, text]) => {
              const ItemIcon = Icon as typeof Cloud;
              return (
                <article key={String(title)} className="grid gap-4 rounded-[12px] border border-white/14 p-6 sm:grid-cols-[44px_1fr]">
                  <span className="grid size-11 place-items-center rounded-[8px] bg-white/8"><ItemIcon className="size-5 text-[#4f81f7]" /></span>
                  <div>
                    <h3 className="font-semibold">{String(title)}</h3>
                    <p className="mt-2 text-sm leading-6 text-white/45">{String(text)}</p>
                  </div>
                </article>
              );
            })}
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-[1000px] px-5 py-24 sm:px-8 lg:py-32">
        <div className="text-center">
          <p className="omnia-kicker text-[#4f81f7]">Ответы</p>
          <h2 className="mt-4 text-[40px] font-semibold tracking-[-.045em] sm:text-[52px]">Частые вопросы</h2>
        </div>
        <div className="mt-12 divide-y divide-[#2b2d32] border-y border-[#2b2d32]">
          {faqs.map(([question, answer], index) => (
            <details key={question} className="group py-1" open={index === 0}>
              <summary className="flex cursor-pointer list-none items-center justify-between gap-6 py-6 text-lg font-semibold [&::-webkit-details-marker]:hidden">
                {question}
                <ChevronDown className="size-5 shrink-0 text-[#828491] transition-transform group-open:rotate-180" />
              </summary>
              <p className="max-w-[760px] pb-6 text-sm leading-6 text-[#9fa1b1]">{answer}</p>
            </details>
          ))}
        </div>
      </section>

      <section className="px-5 pb-5 sm:px-8 sm:pb-8">
        <div data-graphite-shell className="mx-auto max-w-[1320px] rounded-[14px] bg-[#121519] px-6 py-20 text-center sm:px-12">
          <MessageSquareText className="mx-auto size-7 text-[#4f81f7]" />
          <h2 className="mx-auto mt-6 max-w-[780px] text-[40px] font-semibold leading-[1.04] tracking-[-.045em] sm:text-[58px]">Опишите приложение.<br />Остальное соберёт Omnia.</h2>
          <p className="mx-auto mt-5 max-w-[560px] text-sm leading-6 text-white/48">Регистрация займёт несколько минут. Первая сборка запускается только после подтверждения владельца.</p>
          <Link href="/max/register" className="omnia-button omnia-button-primary mt-8 min-h-12 px-6">Начать бесплатно <ArrowRight className="size-4" /></Link>
        </div>
      </section>

      <footer className="px-5 py-12 sm:px-8">
        <div className="mx-auto grid max-w-[1320px] gap-10 border-t border-[#2b2d32] pt-10 sm:grid-cols-[1fr_auto]">
          <div>
            <BrandMark />
            <p className="mt-4 max-w-[380px] text-sm leading-6 text-[#828491]">Продуктовая AI-студия для создания, публикации и эксплуатации приложений.</p>
          </div>
          <div className="grid grid-cols-2 gap-x-14 gap-y-3 text-sm text-[#9fa1b1]">
            <Link href="/max/product">MAX Studio</Link>
            <Link href="/pricing">Тарифы</Link>
            <Link href="/legal/privacy">Конфиденциальность</Link>
            <Link href="/legal/offer">Оферта</Link>
            <Link href="/requisites">Реквизиты</Link>
            <Link href="/legal/refunds">Оплата и возвраты</Link>
            <Link href="/security">Безопасность</Link>
            <a href="mailto:support@lead-generator.ru">Поддержка <ExternalLink className="ml-1 inline size-3" /></a>
          </div>
        </div>
        <div className="mx-auto mt-10 flex max-w-[1320px] justify-between border-t border-[#2b2d32] pt-5 font-mono text-[9px] uppercase tracking-[.08em] text-[#828491]">
          <span>© 2026 Omnia</span>
          <span>Сделано для работающих продуктов</span>
        </div>
      </footer>
    </main>
  );
}
