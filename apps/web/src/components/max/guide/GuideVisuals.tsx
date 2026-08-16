import {
  Activity,
  ArrowRight,
  Bot,
  Check,
  ChevronRight,
  CircleAlert,
  Cloud,
  Copy,
  ExternalLink,
  FileCheck2,
  LayoutGrid,
  Plug,
  Rocket,
  ShieldCheck,
  Smartphone,
  Sparkles,
  Webhook,
} from "lucide-react";
import type { ReactNode } from "react";

const ink = "#171716";
const paper = "#fcfbf7";
const canvas = "#f5f3ee";
const line = "#d8d4cb";
const accent = "#f15a38";
const muted = "#8d887f";
const success = "#248a4b";

function Marker({ number, className }: { number: number; className: string }) {
  return (
    <span
      aria-hidden="true"
      className={`absolute z-20 grid size-7 place-items-center rounded-full border-2 border-white bg-[#f15a38] text-xs font-bold text-white shadow-[0_5px_14px_rgba(0,0,0,.28)] ${className}`}
    >
      {number}
    </span>
  );
}

function ArrowLayer({ children }: { children: ReactNode }) {
  return (
    <svg
      aria-hidden="true"
      className="pointer-events-none absolute inset-0 z-10 h-full w-full"
      viewBox="0 0 1000 560"
      preserveAspectRatio="none"
    >
      <defs>
        <marker id="guide-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
          <path d="M0,0 L8,4 L0,8 Z" fill={accent} />
        </marker>
      </defs>
      {children}
    </svg>
  );
}

function Arrow({ d }: { d: string }) {
  return (
    <path
      d={d}
      fill="none"
      markerEnd="url(#guide-arrow)"
      stroke={accent}
      strokeDasharray="6 5"
      strokeLinecap="round"
      strokeWidth="3"
    />
  );
}

function ScreenshotFrame({
  label,
  title,
  children,
}: {
  label: string;
  title: string;
  children: ReactNode;
}) {
  return (
    <figure className="overflow-hidden rounded-[14px] border border-[#d8d4cb] bg-white shadow-[0_22px_70px_rgba(23,23,22,.10)]">
      <figcaption className="flex items-center justify-between gap-4 border-b border-[#d8d4cb] bg-[#fcfbf7] px-4 py-3 sm:px-5">
        <div className="min-w-0">
          <p className="font-mono text-[9px] uppercase tracking-[.16em] text-[#f15a38]">{label}</p>
          <p className="mt-1 truncate text-xs font-semibold text-[#171716]">{title}</p>
        </div>
        <div className="flex gap-1.5" aria-hidden="true">
          <span className="size-2 rounded-full bg-[#c63d35]" />
          <span className="size-2 rounded-full bg-[#e8a127]" />
          <span className="size-2 rounded-full bg-[#248a4b]" />
        </div>
      </figcaption>
      <div className="relative aspect-[1000/560] min-h-[330px] overflow-hidden bg-[#f5f3ee] sm:min-h-0">
        {children}
      </div>
    </figure>
  );
}

function Sidebar({ active }: { active: string }) {
  const items = [
    ["Проекты", LayoutGrid],
    ["Редактор", Smartphone],
    ["Интеграции", Plug],
    ["MAX и приложение", Bot],
    ["Публикация", Rocket],
  ] as const;

  return (
    <div className="hidden h-full w-[19%] shrink-0 border-r border-[#d8d4cb] bg-[#fcfbf7] p-[2.2%] sm:block">
      <div className="flex items-center gap-2 text-[11px] font-semibold">
        <span className="grid size-6 place-items-center rounded-md bg-[#171716] text-white">✣</span>
        Omnia
      </div>
      <p className="mt-[22%] font-mono text-[7px] uppercase tracking-[.18em] text-[#aaa59b]">MAX Studio</p>
      <div className="mt-[8%] space-y-1">
        {items.map(([item, Icon]) => (
          <div
            key={item}
            className={`flex items-center gap-2 rounded-md px-2 py-2 text-[8px] ${active === item ? "bg-[#ece8df] font-semibold text-[#171716]" : "text-[#8d887f]"}`}
          >
            <Icon className={`size-3 ${active === item ? "text-[#f15a38]" : ""}`} />
            {item}
          </div>
        ))}
      </div>
    </div>
  );
}

export function ProjectCreationVisual() {
  return (
    <ScreenshotFrame label="Снимок 01" title="Мои приложения → Новый проект">
      <div className="flex h-full text-[#171716]">
        <Sidebar active="Проекты" />
        <div className="min-w-0 flex-1">
          <div className="flex h-[13%] items-center justify-between border-b border-[#d8d4cb] bg-[#fcfbf7] px-[4%]">
            <span className="font-mono text-[8px] uppercase tracking-[.18em] text-[#8d887f]">MAX Studio</span>
            <button className="flex items-center gap-1.5 rounded-md bg-[#f15a38] px-3 py-2 text-[8px] font-semibold text-white">
              <span className="text-xs leading-none">+</span> Новый проект
            </button>
          </div>
          <div className="p-[5%]">
            <p className="font-mono text-[7px] uppercase tracking-[.17em] text-[#f15a38]">Рабочее пространство</p>
            <h3 className="mt-2 text-[20px] font-semibold tracking-[-.04em]">Мои приложения</h3>
            <div className="mt-[5%] grid grid-cols-2 gap-[3%]">
              <div className="overflow-hidden rounded-lg border border-[#d8d4cb] bg-[#fcfbf7]">
                <div className="grid aspect-[16/8] place-items-center bg-[#ece8df]">
                  <div className="w-[22%] rounded-md border-[3px] border-[#171716] bg-white p-1.5">
                    <div className="h-4 rounded bg-[#f15a38]" />
                    <div className="mt-1 h-1 rounded bg-[#ece8df]" />
                  </div>
                </div>
                <div className="p-[5%] text-[9px] font-semibold">Кофе рядом</div>
              </div>
              <button className="grid place-items-center rounded-lg border border-dashed border-[#c9c4b9] bg-[#fcfbf7] text-center">
                <span className="text-[9px] font-semibold"><span className="mx-auto mb-2 grid size-7 place-items-center rounded-md border border-[#d8d4cb] text-[#f15a38]">+</span>Новый проект</span>
              </button>
            </div>
          </div>
        </div>
        <div className="absolute bottom-[5%] right-[4%] top-[9%] z-[5] w-[48%] rounded-xl border border-[#d8d4cb] bg-[#fcfbf7] shadow-[0_18px_50px_rgba(23,23,22,.2)]">
          <div className="border-b border-[#d8d4cb] p-[5%]">
            <p className="font-mono text-[7px] uppercase tracking-[.16em] text-[#f15a38]">Новый MAX-проект</p>
            <h4 className="mt-1 text-[15px] font-semibold">Что создаём?</h4>
          </div>
          <div className="space-y-[4%] p-[5%] text-[8px]">
            <label className="block font-medium">Название<div className="mt-1.5 rounded-md border border-[#d8d4cb] bg-white px-2 py-2 text-[#6d6962]">Кофе рядом</div></label>
            <label className="block font-medium">Что пользователь сможет делать?<div className="mt-1.5 h-12 rounded-md border border-[#d8d4cb] bg-white p-2 font-normal text-[#6d6962]">Получать баллы, выбирать награды и оформлять заказ</div></label>
            <div>
              <p className="font-medium">Тип приложения</p>
              <div className="mt-1.5 grid grid-cols-2 gap-1.5">
                <div className="rounded-md border border-[#f15a38] bg-[#f15a38]/5 p-2">Лояльность <Check className="float-right size-3 text-[#f15a38]" /></div>
                <div className="rounded-md border border-[#d8d4cb] p-2">Каталог и заказы</div>
              </div>
            </div>
            <button className="float-right flex items-center gap-1.5 rounded-md bg-[#f15a38] px-3 py-2 font-semibold text-white"><Sparkles className="size-3" />Создать проект</button>
          </div>
        </div>
      </div>
      <ArrowLayer>
        <Arrow d="M835 60 C835 90 800 96 765 115" />
        <Arrow d="M920 230 C860 235 835 245 790 270" />
        <Arrow d="M700 505 C740 500 790 490 830 455" />
      </ArrowLayer>
      <Marker number={1} className="right-[12%] top-[5%]" />
      <Marker number={2} className="right-[4%] top-[36%]" />
      <Marker number={3} className="bottom-[4%] right-[31%]" />
    </ScreenshotFrame>
  );
}

export function BuilderVisual() {
  return (
    <ScreenshotFrame label="Снимок 02" title="Редактор → чат, превью и панель запуска">
      <div className="flex h-full text-[#171716]">
        <Sidebar active="Редактор" />
        <div className="flex min-w-0 flex-1 flex-col bg-[#fcfbf7]">
          <div className="flex h-[13%] items-center justify-between border-b border-[#d8d4cb] px-[3%]">
            <div><p className="text-[10px] font-semibold">Кофе рядом</p><p className="mt-1 text-[7px] text-[#248a4b]">● Сохранено на сервере</p></div>
            <button className="rounded-md bg-[#f15a38] px-3 py-2 text-[8px] font-semibold text-white">Опубликовать</button>
          </div>
          <div className="flex min-h-0 flex-1">
            <div className="relative w-[58%] border-r border-[#d8d4cb] p-[4%]">
              <div className="max-w-[82%] rounded-lg border border-[#d8d4cb] bg-white p-[5%] text-[8px] leading-4 text-[#6d6962]">
                <p className="font-semibold text-[#171716]">Первая версия готова</p>
                <p className="mt-2">Добавил главный экран, программу лояльности, профиль и историю наград. Проверьте интерфейс справа.</p>
                <div className="mt-3 rounded-md bg-[#f5f3ee] p-2 font-mono text-[7px]">build · typecheck clean · preview 200</div>
              </div>
              <div className="absolute inset-x-[5%] bottom-[5%] rounded-lg border border-[#d8d4cb] bg-white p-2.5">
                <p className="text-[8px] text-[#8d887f]">Например: добавь экран наград и кнопку обмена баллов…</p>
                <div className="mt-3 flex justify-end"><span className="rounded-md bg-[#f15a38] px-3 py-1.5 text-[7px] font-semibold text-white">Отправить</span></div>
              </div>
            </div>
            <div className="relative flex-1 bg-[#f5f3ee] p-[4%]">
              <p className="font-mono text-[7px] uppercase tracking-[.17em] text-[#8d887f]">Mobile WebView · Живое превью</p>
              <div className="mx-auto mt-[5%] h-[75%] w-[58%] rounded-[24px] border-[6px] border-[#171716] bg-white p-2 shadow-xl">
                <div className="rounded-xl bg-[#3b2a22] p-3 text-white">
                  <p className="text-[6px] text-white/60">Кофе рядом</p><p className="mt-1 text-[13px] font-semibold">1 250 баллов</p>
                  <div className="mt-3 h-1 rounded bg-white/20"><div className="h-full w-3/5 rounded bg-[#f15a38]" /></div>
                </div>
                <div className="mt-2 space-y-1.5">
                  {["Заказать кофе", "Мои награды", "История"].map((item) => <div key={item} className="flex items-center rounded-md border border-[#e7e3da] p-2 text-[7px] font-semibold"><span className="mr-2 size-4 rounded bg-[#ece8df]" />{item}<ChevronRight className="ml-auto size-2.5 text-[#aaa59b]" /></div>)}
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
      <ArrowLayer>
        <Arrow d="M420 500 C430 470 450 445 475 420" />
        <Arrow d="M815 185 C800 205 780 220 755 240" />
        <Arrow d="M915 60 C890 75 855 85 820 92" />
      </ArrowLayer>
      <Marker number={1} className="bottom-[3%] left-[39%]" />
      <Marker number={2} className="right-[7%] top-[25%]" />
      <Marker number={3} className="right-[4%] top-[4%]" />
    </ScreenshotFrame>
  );
}

export function IntegrationVisual() {
  return (
    <ScreenshotFrame label="Снимок 03" title="Интеграции → подключение сервиса и MAX-бота">
      <div className="flex h-full text-[#171716]">
        <Sidebar active="Интеграции" />
        <div className="min-w-0 flex-1 p-[4%]">
          <p className="font-mono text-[7px] uppercase tracking-[.17em] text-[#f15a38]">Подключения</p>
          <h3 className="mt-2 text-[20px] font-semibold tracking-[-.04em]">Интеграции приложения</h3>
          <p className="mt-1 text-[8px] text-[#8d887f]">Секреты хранятся зашифрованно и не попадают в код проекта.</p>
          <div className="mt-[5%] grid grid-cols-2 gap-[3%]">
            {[
              ["MAX Bot API", "Точка входа, сообщения и webhook", Bot, "Подключить", true],
              ["ЮKassa", "Платежи, возвраты и статусы", ShieldCheck, "Подключить", false],
              ["Битрикс24", "Лиды и сделки", Plug, "Подключить", false],
              ["Яндекс Метрика", "События и конверсии", Activity, "Подключить", false],
            ].map(([title, copy, Icon, action, hot]) => {
              const ItemIcon = Icon as typeof Bot;
              return (
                <div key={String(title)} className={`rounded-lg border bg-[#fcfbf7] p-[5%] ${hot ? "border-[#f15a38]/60 shadow-[0_8px_24px_rgba(241,90,56,.12)]" : "border-[#d8d4cb]"}`}>
                  <div className="flex items-center justify-between"><span className="grid size-7 place-items-center rounded-md bg-[#ece8df]"><ItemIcon className="size-3.5 text-[#f15a38]" /></span><span className="rounded-full bg-[#f5f3ee] px-2 py-1 text-[6px] text-[#8d887f]">Не подключено</span></div>
                  <p className="mt-[7%] text-[10px] font-semibold">{String(title)}</p><p className="mt-1 text-[7px] text-[#8d887f]">{String(copy)}</p>
                  <button className={`mt-[7%] rounded-md px-3 py-1.5 text-[7px] font-semibold ${hot ? "bg-[#f15a38] text-white" : "border border-[#d8d4cb]"}`}>{String(action)}</button>
                </div>
              );
            })}
          </div>
          <div className="absolute bottom-[7%] right-[5%] w-[38%] rounded-lg border border-[#d8d4cb] bg-white p-[3%] shadow-xl">
            <div className="flex items-center gap-2"><Bot className="size-4 text-[#f15a38]" /><p className="text-[10px] font-semibold">Подключить MAX-бота</p></div>
            <p className="mt-2 text-[7px] leading-3 text-[#8d887f]">Вставьте токен из MAX для партнёров. Токен будет проверен через API.</p>
            <div className="mt-2 rounded-md border border-[#d8d4cb] px-2 py-2 font-mono text-[7px] text-[#aaa59b]">Введите токен бота</div>
            <button className="mt-2 rounded-md bg-[#f15a38] px-3 py-1.5 text-[7px] font-semibold text-white">Проверить и сохранить</button>
          </div>
        </div>
      </div>
      <ArrowLayer>
        <Arrow d="M555 280 C590 280 620 285 655 300" />
        <Arrow d="M875 485 C850 470 825 455 805 430" />
      </ArrowLayer>
      <Marker number={1} className="left-[54%] top-[45%]" />
      <Marker number={2} className="bottom-[4%] right-[10%]" />
    </ScreenshotFrame>
  );
}

export function LaunchVisual() {
  const steps = [
    ["Приложение заполнено", true],
    ["Юридические данные", true],
    ["MAX-бот подключён", true],
    ["Production опубликован", false],
    ["URL добавлен в MAX", false],
  ] as const;
  return (
    <ScreenshotFrame label="Снимок 04" title="Публикация → мастер готовности">
      <div className="flex h-full text-[#171716]">
        <Sidebar active="Публикация" />
        <div className="flex min-w-0 flex-1 bg-[#f5f3ee]">
          <div className="min-w-0 flex-1 p-[5%]">
            <p className="font-mono text-[7px] uppercase tracking-[.17em] text-[#f15a38]">Release</p>
            <h3 className="mt-2 text-[20px] font-semibold tracking-[-.04em]">Публикация в MAX</h3>
            <div className="mt-[6%] rounded-lg border border-[#d8d4cb] bg-[#fcfbf7] p-[5%]">
              <div className="flex items-center justify-between"><div><p className="text-[9px] font-semibold">Production</p><p className="mt-1 text-[7px] text-[#8d887f]">Постоянный HTTPS-адрес и проверка контейнера</p></div><Cloud className="size-5 text-[#f15a38]" /></div>
              <div className="mt-[5%] grid grid-cols-3 gap-2 text-[7px]">
                <div className="rounded-md bg-[#f5f3ee] p-2"><p className="text-[#8d887f]">Версия</p><p className="mt-1 font-semibold">v.12</p></div>
                <div className="rounded-md bg-[#f5f3ee] p-2"><p className="text-[#8d887f]">Health</p><p className="mt-1 font-semibold">готов</p></div>
                <div className="rounded-md bg-[#f5f3ee] p-2"><p className="text-[#8d887f]">Webhook</p><p className="mt-1 font-semibold">ожидает URL</p></div>
              </div>
              <button className="mt-[6%] flex items-center gap-1.5 rounded-md bg-[#f15a38] px-3 py-2 text-[8px] font-semibold text-white"><Rocket className="size-3" />Опубликовать</button>
            </div>
          </div>
          <aside className="w-[36%] border-l border-[#d8d4cb] bg-[#fcfbf7] p-[4%]">
            <div className="flex items-center justify-between"><p className="text-[11px] font-semibold">Запуск в MAX</p><span className="text-[9px] font-semibold">60%</span></div>
            <div className="mt-2 h-1.5 rounded-full bg-[#e7e3da]"><div className="h-full w-3/5 rounded-full bg-[#f15a38]" /></div>
            <div className="mt-[7%] border-l-2 border-[#f15a38] pl-3"><p className="font-mono text-[6px] uppercase tracking-[.14em] text-[#8d887f]">Шаг 4 из 5</p><p className="mt-1 text-[9px] font-semibold">Опубликуйте приложение</p><p className="mt-1 text-[7px] leading-3 text-[#8d887f]">Получите постоянный HTTPS-адрес.</p></div>
            <div className="mt-[7%] space-y-1 border-y border-[#e7e3da] py-2">
              {steps.map(([step, done], index) => <div key={step} className={`flex items-center gap-2 rounded-md px-2 py-1.5 text-[7px] ${index === 3 ? "bg-[#f15a38]/8 font-semibold" : ""}`}><span className={`grid size-4 place-items-center rounded-full border ${done ? "border-[#248a4b]/40 bg-[#248a4b]/10 text-[#248a4b]" : index === 3 ? "border-[#f15a38] bg-[#f15a38] text-white" : "border-[#d8d4cb] text-[#8d887f]"}`}>{done ? <Check className="size-2.5" /> : index + 1}</span>{step}</div>)}
            </div>
            <div className="mt-[7%] grid grid-cols-2 gap-1.5"><button className="rounded-md border border-[#d8d4cb] px-2 py-2 text-[7px]">Приложение</button><button className="rounded-md border border-[#d8d4cb] px-2 py-2 text-[7px]">MAX-бот</button></div>
          </aside>
        </div>
      </div>
      <ArrowLayer>
        <Arrow d="M525 390 C510 410 495 425 470 442" />
        <Arrow d="M865 230 C845 250 830 270 815 292" />
      </ArrowLayer>
      <Marker number={1} className="bottom-[17%] left-[49%]" />
      <Marker number={2} className="right-[7%] top-[35%]" />
    </ScreenshotFrame>
  );
}

export function PartnerVisual() {
  return (
    <ScreenshotFrame label="Снимок 05" title="MAX для партнёров → Расширенные настройки">
      <div className="flex h-full bg-[#f4f5f7] text-[#15171a]">
        <div className="hidden w-[22%] border-r border-black/10 bg-white p-[3%] sm:block">
          <div className="flex items-center gap-2 text-[11px] font-semibold"><span className="grid size-6 place-items-center rounded-full bg-[#171716] text-white">M</span>MAX для бизнеса</div>
          <div className="mt-[24%] space-y-1 text-[8px] text-[#727780]">
            <p className="rounded-md px-2 py-2">Профиль</p><p className="rounded-md bg-[#eef0f3] px-2 py-2 font-semibold text-[#15171a]">Чат-боты</p><p className="rounded-md px-2 py-2">Мини-приложения</p>
          </div>
        </div>
        <div className="min-w-0 flex-1 p-[5%]">
          <div className="flex items-center justify-between"><div><p className="text-[8px] text-[#727780]">Чат-боты / Кофе рядом</p><h3 className="mt-1 text-[18px] font-semibold">Расширенные настройки</h3></div><span className="rounded-full bg-[#e7f6ec] px-2.5 py-1 text-[7px] font-semibold text-[#1e7e45]">Бот создан</span></div>
          <div className="mt-[5%] rounded-xl bg-white p-[5%] shadow-[0_8px_30px_rgba(0,0,0,.07)]">
            <div className="flex items-center justify-between"><div><p className="text-[10px] font-semibold">Токен бота</p><p className="mt-1 text-[7px] text-[#727780]">Используется сервером для Bot API.</p></div><button className="flex items-center gap-1 rounded-md border border-black/10 px-2 py-1.5 text-[7px]"><Copy className="size-3" />Копировать</button></div>
            <div className="mt-2 rounded-md border border-black/10 bg-[#f7f8fa] px-3 py-2 font-mono text-[7px] text-[#727780]">••••••••••••••••••••••••</div>
            <div className="my-[5%] h-px bg-black/10" />
            <label className="block text-[10px] font-semibold">Ссылка на мини-приложение</label>
            <div className="mt-2 flex gap-2"><div className="min-w-0 flex-1 truncate rounded-md border-2 border-[#f15a38] bg-white px-3 py-2 font-mono text-[7px]">https://app-42.lead-generator.ru</div><button className="rounded-md border border-black/10 px-3 text-[7px]">Проверить</button></div>
            <div className="mt-[4%]"><p className="text-[9px] font-semibold">Кнопка запуска</p><div className="mt-2 flex gap-2 text-[7px]"><button className="rounded-full border-2 border-[#f15a38] bg-[#fff3ef] px-3 py-1.5 font-semibold text-[#c84528]">Открыть</button><button className="rounded-full border border-black/10 px-3 py-1.5">Старт</button><button className="rounded-full border border-black/10 px-3 py-1.5">Играть</button></div></div>
            <div className="mt-[5%] flex justify-end"><button className="rounded-md bg-[#15171a] px-4 py-2 text-[8px] font-semibold text-white">Сохранить</button></div>
          </div>
        </div>
      </div>
      <ArrowLayer>
        <Arrow d="M870 160 C835 170 795 185 750 210" />
        <Arrow d="M890 338 C850 340 815 344 770 350" />
        <Arrow d="M720 480 C750 475 790 466 825 450" />
      </ArrowLayer>
      <Marker number={1} className="right-[8%] top-[21%]" />
      <Marker number={2} className="right-[6%] top-[56%]" />
      <Marker number={3} className="bottom-[6%] right-[27%]" />
    </ScreenshotFrame>
  );
}

export function DashboardVisual() {
  return (
    <ScreenshotFrame label="Снимок 06" title="После запуска → здоровье production">
      <div className="flex h-full text-[#171716]">
        <Sidebar active="Публикация" />
        <div className="min-w-0 flex-1 p-[4%]">
          <div className="flex items-end justify-between"><div><p className="font-mono text-[7px] uppercase tracking-[.17em] text-[#f15a38]">Production</p><h3 className="mt-2 text-[20px] font-semibold tracking-[-.04em]">После запуска</h3></div><a className="flex items-center gap-1 text-[8px] font-semibold text-[#c84528]">Открыть приложение <ExternalLink className="size-3" /></a></div>
          <div className="mt-[5%] grid grid-cols-4 gap-[2%]">
            {[
              [Cloud, "Контейнер", "Работает", true],
              [Activity, "Health-check", "Отвечает", true],
              [Bot, "MAX-бот", "Кофе рядом", true],
              [Webhook, "Webhook", "Активен", true],
            ].map(([Icon, title, copy, ok]) => {
              const ItemIcon = Icon as typeof Cloud;
              return <div key={String(title)} className="rounded-lg border border-[#d8d4cb] bg-[#fcfbf7] p-[8%]"><div className="flex items-center justify-between"><span className="grid size-7 place-items-center rounded-md bg-[#ece8df]"><ItemIcon className="size-3.5 text-[#f15a38]" /></span>{ok ? <Check className="size-3.5 text-[#248a4b]" /> : <CircleAlert className="size-3.5 text-[#e8a127]" />}</div><p className="mt-[14%] text-[8px] font-semibold">{String(title)}</p><p className="mt-1 text-[7px] text-[#8d887f]">{String(copy)}</p></div>;
            })}
          </div>
          <div className="mt-[4%] grid grid-cols-[1.25fr_.75fr] gap-[3%]">
            <div className="overflow-hidden rounded-lg border border-[#d8d4cb] bg-[#fcfbf7]">
              <div className="flex items-center justify-between border-b border-[#d8d4cb] p-[4%]"><div><p className="font-mono text-[6px] uppercase tracking-[.15em] text-[#8d887f]">Versions</p><p className="mt-1 text-[10px] font-semibold">История публикаций</p></div><button className="rounded-md border border-[#d8d4cb] px-2 py-1 text-[7px]">Обновить</button></div>
              {["v.12 · Production build", "v.11 · Обновление каталога", "v.10 · Первая публикация"].map((item, index) => <div key={item} className="flex items-center gap-3 border-b border-[#e7e3da] p-[3%] text-[7px]"><span className="grid size-6 place-items-center rounded-full bg-[#248a4b]/10 text-[#248a4b]"><Check className="size-3" /></span><span className="font-semibold">{item}</span><span className="ml-auto text-[#8d887f]">{index === 0 ? "сейчас" : `${index} дн.`}</span><span className="font-semibold text-[#248a4b]">done</span></div>)}
            </div>
            <div className="rounded-lg border border-[#d8d4cb] bg-[#fcfbf7] p-[7%]"><p className="font-mono text-[6px] uppercase tracking-[.15em] text-[#8d887f]">Эксплуатация</p><p className="mt-2 text-[10px] font-semibold">Без разработчика</p><div className="mt-[10%] space-y-3 text-[7px] text-[#6d6962]"><p className="flex gap-2"><ShieldCheck className="size-3 shrink-0 text-[#f15a38]" />Health-check после релиза</p><p className="flex gap-2"><Cloud className="size-3 shrink-0 text-[#f15a38]" />Всегда активный контейнер</p><p className="flex gap-2"><ArrowRight className="size-3 shrink-0 text-[#f15a38]" />Версии и откат</p></div></div>
          </div>
        </div>
      </div>
      <ArrowLayer>
        <Arrow d="M895 90 C860 100 830 110 790 130" />
        <Arrow d="M520 430 C500 410 485 392 470 365" />
      </ArrowLayer>
      <Marker number={1} className="right-[7%] top-[7%]" />
      <Marker number={2} className="bottom-[18%] left-[48%]" />
    </ScreenshotFrame>
  );
}

export function GoldenPathVisual() {
  const nodes = [
    ["1", "Аккаунт", "Email подтверждён", FileCheck2],
    ["2", "Проект", "Сборка проверена", Sparkles],
    ["3", "MAX-бот", "Токен валиден", Bot],
    ["4", "Production", "HTTPS и webhook", Rocket],
    ["5", "Запуск", "2 реальных юзера", Smartphone],
  ] as const;
  return (
    <div className="grid gap-3 md:grid-cols-5">
      {nodes.map(([number, title, copy, Icon], index) => (
        <div key={title} className="relative rounded-[12px] border border-[#d8d4cb] bg-[#fcfbf7] p-5">
          <div className="flex items-center justify-between"><span className="font-mono text-[9px] text-[#f15a38]">{number}</span><Icon className="size-4 text-[#f15a38]" /></div>
          <p className="mt-8 text-sm font-semibold">{title}</p><p className="mt-1 text-xs leading-5 text-[#8d887f]">{copy}</p>
          {index < nodes.length - 1 && <span className="absolute -right-2 top-1/2 z-10 hidden size-4 -translate-y-1/2 place-items-center rounded-full bg-[#171716] text-white md:grid"><ChevronRight className="size-3" /></span>}
        </div>
      ))}
    </div>
  );
}

export const guideColors = { ink, paper, canvas, line, accent, muted, success };
