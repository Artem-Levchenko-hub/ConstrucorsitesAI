import {
  ArrowRight,
  BarChart3,
  Bot,
  Check,
  ChevronRight,
  CircleDollarSign,
  Cloud,
  Code2,
  Database,
  ExternalLink,
  KeyRound,
  MessageSquareText,
  PackageCheck,
  PlugZap,
  Rocket,
  ShieldCheck,
  Smartphone,
  Store,
  Webhook,
} from "lucide-react";
import Link from "next/link";

import { BrandMark } from "@/components/marketing/BrandMark";

function MiniAppPhone() {
  return (
    <div className="mx-auto w-[260px] rounded-[38px] border-[8px] border-[#0d0d0c] bg-[#f5eee0] p-3 shadow-[0_30px_70px_rgba(0,0,0,.22)]">
      <div className="mx-auto h-1 w-16 rounded-full bg-black/18" />
      <div className="mt-3 overflow-hidden rounded-[22px] bg-[#fcfbf7]">
        <div className="bg-[#3b2a22] p-5 text-white">
          <p className="text-[10px] text-white/55">Кофе рядом</p>
          <h3 className="mt-1 text-xl font-semibold">1250 баллов</h3>
          <p className="mt-3 text-[10px] text-white/60">До бесплатного напитка 3 покупки</p>
          <div className="mt-2 h-1.5 rounded-full bg-white/15"><div className="h-full w-3/5 rounded-full bg-[#f15a38]" /></div>
        </div>
        <div className="space-y-2 p-3">
          {["Заказать кофе", "Мои награды", "Персональные акции"].map((item, index) => (
            <div key={item} className="flex items-center gap-3 rounded-[12px] border border-[#e7e3da] bg-white p-3">
              <span className={`grid size-8 place-items-center rounded-[8px] ${index === 0 ? "bg-[#f15a38] text-white" : "bg-[#ece8df] text-[#6d6962]"}`}>
                {index === 0 ? <Store className="size-4" /> : index === 1 ? <PackageCheck className="size-4" /> : <CircleDollarSign className="size-4" />}
              </span>
              <span className="text-[11px] font-semibold text-[#171716]">{item}</span>
              <ChevronRight className="ml-auto size-3 text-[#aaa59b]" />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

const automation = [
  [Code2, "Собирает приложение", "Создаёт интерфейс, backend, данные и проверки для мобильного MAX WebView."],
  [Bot, "Подключает бота", "Проверяет токен, настраивает webhook и показывает реальные статусы API."],
  [PlugZap, "Встраивает сервисы", "Подключает платежи, CRM, учёт, доставку и аналитику из кабинета."],
  [Cloud, "Публикует", "Разворачивает контейнер, выдаёт HTTPS и проверяет доступность после релиза."],
] as const;

export default function MaxProductPage() {
  return (
    <main className="min-h-screen bg-[#f5f3ee] text-[#171716]">
      <section data-graphite-shell className="bg-[#171716]">
        <header className="mx-auto flex h-18 max-w-[1320px] items-center justify-between px-5 sm:px-8">
          <div className="flex items-center gap-3">
            <BrandMark inverse />
            <span className="h-5 w-px bg-white/18" />
            <span className="text-sm font-medium text-white/62">MAX Studio</span>
          </div>
          <nav className="hidden items-center gap-7 text-[13px] text-white/55 lg:flex">
            <a href="#about">Возможности</a>
            <a href="#bot">MAX-бот</a>
            <a href="#integrations">Интеграции</a>
            <a href="#launch">Запуск</a>
            <Link href="/max/guide">Руководство</Link>
          </nav>
          <div className="flex items-center gap-2.5">
            <Link href="/login?next=/max" className="hidden px-3 py-2 text-[13px] text-white/60 sm:block">Войти</Link>
            <Link href="/max/register" className="omnia-button omnia-button-primary min-h-9 px-4 text-[13px]">Начать</Link>
          </div>
        </header>

        <div className="mx-auto grid max-w-[1320px] gap-14 px-5 pb-24 pt-18 sm:px-8 lg:grid-cols-[1.15fr_.85fr] lg:items-center lg:pb-32 lg:pt-24">
          <div>
            <p className="omnia-kicker text-[#f15a38]">Omnia / MAX Studio</p>
            <h1 className="mt-6 max-w-[760px] text-[58px] font-semibold leading-[.94] tracking-[-.06em] sm:text-[78px] lg:text-[96px]">
              MAX Studio
            </h1>
            <p className="mt-6 max-w-[660px] text-[25px] leading-[1.2] tracking-[-.025em] text-white/78 sm:text-[32px]">
              Готовое мини-приложение для бизнеса — от задачи до публикации.
            </p>
            <p className="mt-6 max-w-[620px] text-base leading-7 text-white/48">
              Агент собирает приложение, подключает бота и сервисы, готовит юридические экраны,
              публикует и продолжает обслуживать продукт после запуска.
            </p>
            <div className="mt-9 flex flex-col gap-3 sm:flex-row">
              <Link href="/max/register" className="omnia-button omnia-button-primary min-h-12 px-6">Создать приложение <ArrowRight className="size-4" /></Link>
              <Link href="/max/guide" className="omnia-button min-h-12 border border-white/20 px-6 text-white hover:bg-white/5">Открыть руководство</Link>
            </div>
          </div>
          <div className="relative min-h-[470px]">
            <div className="absolute inset-0 rounded-full bg-[#f15a38]/10 blur-3xl" />
            <div className="relative grid h-full place-items-center">
              <MiniAppPhone />
            </div>
          </div>
        </div>
      </section>

      <section id="about" className="mx-auto max-w-[1320px] px-5 py-24 sm:px-8 lg:py-32">
        <div className="grid gap-12 lg:grid-cols-[1fr_.8fr] lg:items-center">
          <div>
            <p className="omnia-kicker text-[#f15a38]">MAX Mini App</p>
            <h2 className="mt-4 max-w-[700px] text-[40px] font-semibold leading-[1.04] tracking-[-.045em] sm:text-[58px]">Приложение открывается внутри мессенджера</h2>
            <p className="mt-6 max-w-[620px] text-base leading-7 text-[#6d6962]">
              Пользователь нажимает кнопку в боте и получает полноценный интерфейс: каталог,
              профиль, заказ, оплату, запись или программу лояльности. Устанавливать отдельное
              приложение не требуется.
            </p>
            <div className="mt-10 grid gap-px overflow-hidden rounded-[12px] border border-[#d8d4cb] bg-[#d8d4cb] sm:grid-cols-2">
              {[
                [Smartphone, "Мобильный интерфейс", "Адаптирован для WebView и навигации MAX."],
                [Database, "Данные бизнеса", "Каталог, клиенты, заказы и история действий."],
                [CircleDollarSign, "Платежи", "ЮKassa, СБП и контроль статусов операций."],
                [BarChart3, "Аналитика", "События, воронки и конверсии после запуска."],
              ].map(([Icon, title, text]) => {
                const ItemIcon = Icon as typeof Smartphone;
                return (
                  <article key={String(title)} className="bg-[#fcfbf7] p-6">
                    <ItemIcon className="size-5 text-[#f15a38]" />
                    <h3 className="mt-8 font-semibold">{String(title)}</h3>
                    <p className="mt-2 text-sm leading-6 text-[#6d6962]">{String(text)}</p>
                  </article>
                );
              })}
            </div>
          </div>
          <MiniAppPhone />
        </div>
      </section>

      <section id="bot" data-graphite-shell className="bg-[#171716] px-5 py-24 sm:px-8 lg:py-32">
        <div className="mx-auto max-w-[1320px]">
          <div className="grid gap-12 lg:grid-cols-[.8fr_1.2fr]">
            <div>
              <p className="omnia-kicker text-[#f15a38]">Связь с MAX</p>
              <h2 className="mt-4 text-[40px] font-semibold leading-[1.04] tracking-[-.045em] sm:text-[56px]">Зачем нужен MAX-бот</h2>
              <p className="mt-6 text-base leading-7 text-white/48">
                Бот — официальный владелец точки входа в MAX. Он показывает кнопку приложения,
                получает события и отправляет пользователю сервисные сообщения.
              </p>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              {[
                [Bot, "Открывает Mini App", "Кнопка бота ведёт на постоянный HTTPS-адрес приложения."],
                [Webhook, "Получает события", "Backend принимает события MAX через защищённый webhook."],
                [MessageSquareText, "Отправляет уведомления", "Статус заказа, напоминания и полезные сервисные сообщения."],
                [ShieldCheck, "Подтверждает владельца", "Организация, ИП или самозанятый проходят проверку на стороне MAX."],
              ].map(([Icon, title, text]) => {
                const ItemIcon = Icon as typeof Bot;
                return (
                  <article key={String(title)} className="rounded-[12px] border border-white/14 p-6">
                    <ItemIcon className="size-5 text-[#f15a38]" />
                    <h3 className="mt-10 text-lg font-semibold">{String(title)}</h3>
                    <p className="mt-2 text-sm leading-6 text-white/45">{String(text)}</p>
                  </article>
                );
              })}
            </div>
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-[1320px] px-5 py-24 sm:px-8 lg:py-32">
        <p className="omnia-kicker text-[#f15a38]">Что делает студия</p>
        <div className="mt-4 grid gap-8 border-b border-[#d8d4cb] pb-12 lg:grid-cols-2">
          <h2 className="text-[40px] font-semibold leading-[1.04] tracking-[-.045em] sm:text-[56px]">Технические шаги<br />выполняются автоматически</h2>
          <p className="max-w-[560px] text-base leading-7 text-[#6d6962] lg:justify-self-end">Пользователь принимает продуктовые решения и предоставляет доступы только там, где внешний сервис не имеет публичного API.</p>
        </div>
        <div className="mt-4 divide-y divide-[#d8d4cb]">
          {automation.map(([Icon, title, text], index) => (
            <article key={title} className="grid gap-4 py-8 sm:grid-cols-[64px_300px_1fr]">
              <span className="grid size-10 place-items-center rounded-[8px] bg-[#ece8df]"><Icon className="size-5 text-[#f15a38]" /></span>
              <h3 className="text-xl font-semibold">{String(index + 1).padStart(2, "0")}. {title}</h3>
              <p className="max-w-[620px] text-sm leading-6 text-[#6d6962]">{text}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="border-y border-[#d8d4cb] bg-[#fcfbf7] px-5 py-24 sm:px-8 lg:py-32">
        <div className="mx-auto max-w-[1120px]">
          <div className="text-center">
            <p className="omnia-kicker text-[#f15a38]">Один ручной шаг</p>
            <h2 className="mt-4 text-[40px] font-semibold tracking-[-.045em] sm:text-[54px]">Что делается в кабинете MAX</h2>
            <p className="mx-auto mt-5 max-w-[650px] text-base leading-7 text-[#6d6962]">MAX пока не предоставляет публичный API для создания бота и вставки URL. Студия даёт точную ссылку и проверяет результат.</p>
          </div>
          <div className="mt-14 grid gap-4 md:grid-cols-3">
            {[
              ["01", "Создать бота", "Организация, ИП или самозанятый создаёт бота в платформе MAX для партнёров."],
              ["02", "Скопировать токен", "Токен вставляется в Omnia один раз и хранится в зашифрованном виде."],
              ["03", "Вставить URL", "После публикации готовый HTTPS-адрес добавляется к кнопке запуска Mini App."],
            ].map(([number, title, text]) => (
              <article key={number} className="omnia-card p-7">
                <span className="font-mono text-[10px] text-[#f15a38]">{number}</span>
                <h3 className="mt-10 text-xl font-semibold">{title}</h3>
                <p className="mt-3 text-sm leading-6 text-[#6d6962]">{text}</p>
              </article>
            ))}
          </div>
          <a href="https://business.max.ru/" target="_blank" rel="noreferrer" className="omnia-button omnia-button-secondary mx-auto mt-8 flex w-fit">
            Открыть платформу MAX для партнёров <ExternalLink className="size-4" />
          </a>
        </div>
      </section>

      <section id="integrations" data-graphite-shell className="bg-[#171716] px-5 py-24 sm:px-8 lg:py-32">
        <div className="mx-auto max-w-[1320px]">
          <div className="grid gap-10 lg:grid-cols-2">
            <div>
              <p className="omnia-kicker text-[#f15a38]">Готовые подключения</p>
              <h2 className="mt-4 text-[40px] font-semibold leading-[1.04] tracking-[-.045em] sm:text-[56px]">Интеграции без редактирования кода</h2>
            </div>
            <p className="max-w-[560px] text-base leading-7 text-white/48 lg:justify-self-end">Авторизуйте сервис один раз для бизнеса. После этого подключение можно использовать в новых проектах без повторного ввода секретов.</p>
          </div>
          <div className="mt-12 grid gap-px overflow-hidden rounded-[12px] border border-white/14 bg-white/14 sm:grid-cols-2 lg:grid-cols-4">
            {["ЮKassa / СБП", "iiko / r_keeper", "Битрикс24 / amoCRM", "МойСклад / 1С", "Yclients", "CDEK", "Яндекс Метрика", "Собственный REST API"].map((item, index) => (
              <div key={item} className="min-h-[130px] bg-[#171716] p-6">
                <div className="flex items-center justify-between"><PlugZap className="size-4 text-[#f15a38]" /><span className="font-mono text-[9px] text-white/25">{String(index + 1).padStart(2, "0")}</span></div>
                <p className="mt-8 text-sm font-semibold">{item}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section id="launch" className="mx-auto max-w-[1320px] px-5 py-24 sm:px-8 lg:py-32">
        <div className="grid gap-12 lg:grid-cols-[1fr_.9fr]">
          <div>
            <p className="omnia-kicker text-[#f15a38]">После публикации</p>
            <h2 className="mt-4 text-[40px] font-semibold leading-[1.04] tracking-[-.045em] sm:text-[56px]">Приложение остаётся управляемым</h2>
            <p className="mt-6 max-w-[600px] text-base leading-7 text-[#6d6962]">У каждой публикации есть версия, журнал и health-check. Новые правки проходят ту же проверяемую цепочку и не запускают генерацию повторно после обновления страницы.</p>
          </div>
          <div className="omnia-card overflow-hidden">
            <div className="flex items-center justify-between border-b border-[#d8d4cb] p-5">
              <div><p className="omnia-kicker text-[#8d887f]">Production</p><p className="mt-1 font-semibold">coffee-miniapp.ru</p></div>
              <span className="rounded-full bg-[#248a4b]/10 px-3 py-1 text-xs font-medium text-[#248a4b]">Работает</span>
            </div>
            <div className="grid grid-cols-3 divide-x divide-[#d8d4cb]">
              {[["99.98%", "Uptime"], ["v.12", "Версия"], ["142 ms", "Ответ"]].map(([value, label]) => (
                <div key={label} className="p-5"><p className="text-xl font-semibold">{value}</p><p className="mt-1 text-[10px] text-[#8d887f]">{label}</p></div>
              ))}
            </div>
            <div className="border-t border-[#d8d4cb] p-5 text-xs text-[#6d6962]">
              <p className="flex items-center gap-2"><Check className="size-4 text-[#248a4b]" /> Контейнер активен всегда</p>
              <p className="mt-3 flex items-center gap-2"><Check className="size-4 text-[#248a4b]" /> Webhook отвечает</p>
              <p className="mt-3 flex items-center gap-2"><Check className="size-4 text-[#248a4b]" /> Последний backup 8 минут назад</p>
            </div>
          </div>
        </div>
      </section>

      <section className="px-5 pb-5 sm:px-8 sm:pb-8">
        <div data-graphite-shell className="mx-auto max-w-[1320px] rounded-[14px] bg-[#171716] px-6 py-20 text-center">
          <Rocket className="mx-auto size-7 text-[#f15a38]" />
          <h2 className="mx-auto mt-6 max-w-[760px] text-[40px] font-semibold leading-[1.04] tracking-[-.045em] sm:text-[58px]">Соберите первое приложение для MAX</h2>
          <p className="mx-auto mt-5 max-w-[560px] text-sm leading-6 text-white/48">Аккаунт привязывается к владельцу бизнеса, поэтому бесплатные генерации нельзя абузить созданием дублей.</p>
          <Link href="/max/register" className="omnia-button omnia-button-primary mt-8 min-h-12 px-6">Начать <ArrowRight className="size-4" /></Link>
        </div>
      </section>

      <footer className="px-5 py-12 sm:px-8">
        <div className="mx-auto flex max-w-[1320px] flex-col gap-8 border-t border-[#d8d4cb] pt-10 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-center gap-3"><BrandMark /><span className="h-5 w-px bg-[#d8d4cb]" /><span className="text-sm text-[#6d6962]">MAX Studio</span></div>
          <div className="flex flex-wrap gap-5 text-xs text-[#6d6962]">
            <Link href="/legal/privacy">Конфиденциальность</Link>
            <Link href="/legal/terms">Условия</Link>
            <Link href="/requisites">Реквизиты</Link>
            <Link href="/security">Безопасность</Link>
          </div>
        </div>
      </footer>
    </main>
  );
}
