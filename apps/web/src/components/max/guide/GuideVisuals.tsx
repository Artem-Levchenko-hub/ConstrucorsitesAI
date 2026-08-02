"use client";

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
import { useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";

const ink = "#171716";
const paper = "#fcfbf7";
const canvas = "#f5f3ee";
const line = "#d8d4cb";
const accent = "var(--color-accent)";
const muted = "#8d887f";
const success = "#248a4b";

type Callout = {
  number: number;
  target: string;
  offset: [number, number];
};

function CalloutLayer({ callouts }: { callouts: Callout[] }) {
  const arrowId = `guide-arrow-${useId().replace(/:/g, "")}`;
  const layerRef = useRef<HTMLDivElement>(null);
  const [layerSize, setLayerSize] = useState<[number, number]>([1, 1]);
  const [geometry, setGeometry] = useState<Array<{
    number: number;
    target: string;
    from: [number, number];
    to: [number, number];
    d: string;
  }>>([]);

  useLayoutEffect(() => {
    const layer = layerRef.current;
    const container = layer?.parentElement;
    if (!layer || !container) return;

    let animationFrame = 0;
    const measure = () => {
      const containerRect = container.getBoundingClientRect();
      if (containerRect.width === 0 || containerRect.height === 0) return;

      const next = callouts.flatMap(({ number, target, offset }) => {
        const targetElement = container.querySelector<HTMLElement>(`[data-guide-target="${target}"]`);
        if (!targetElement) return [];

        const targetRect = targetElement.getBoundingClientRect();
        const toX = targetRect.left - containerRect.left + targetRect.width / 2;
        const toY = targetRect.top - containerRect.top + targetRect.height / 2;
        const fromX = Math.min(
          containerRect.width - 18,
          Math.max(18, toX + (offset[0] / 100) * containerRect.width),
        );
        const fromY = Math.min(
          containerRect.height - 18,
          Math.max(18, toY + (offset[1] / 100) * containerRect.height),
        );
        const deltaX = toX - fromX;
        const d = `M${fromX} ${fromY} C${fromX + deltaX * 0.38} ${fromY} ${toX - deltaX * 0.3} ${toY} ${toX} ${toY}`;

        return [{ number, target, from: [fromX, fromY] as [number, number], to: [toX, toY] as [number, number], d }];
      });

      setLayerSize([containerRect.width, containerRect.height]);
      setGeometry(next);
    };
    const scheduleMeasure = () => {
      cancelAnimationFrame(animationFrame);
      animationFrame = requestAnimationFrame(measure);
    };
    const resizeObserver = new ResizeObserver(scheduleMeasure);
    resizeObserver.observe(container);
    callouts.forEach(({ target }) => {
      const targetElement = container.querySelector<HTMLElement>(`[data-guide-target="${target}"]`);
      if (targetElement) resizeObserver.observe(targetElement);
    });
    scheduleMeasure();
    void document.fonts?.ready.then(scheduleMeasure);

    return () => {
      cancelAnimationFrame(animationFrame);
      resizeObserver.disconnect();
    };
  }, [callouts]);

  return (
    <div ref={layerRef} aria-hidden="true" className="pointer-events-none absolute inset-0 z-20">
      <svg
        className="absolute inset-0 h-full w-full"
        viewBox={`0 0 ${layerSize[0]} ${layerSize[1]}`}
        preserveAspectRatio="none"
      >
        <defs>
          <marker
            id={arrowId}
            markerWidth="14"
            markerHeight="14"
            markerUnits="userSpaceOnUse"
            refX="8"
            refY="4"
            orient="auto"
            viewBox="0 0 8 8"
          >
            <path d="M0,0 L8,4 L0,8 Z" fill={accent} />
          </marker>
        </defs>
        {geometry.map(({ number, d }) => (
          <g key={number}>
            <path d={d} fill="none" stroke="white" strokeLinecap="round" strokeWidth="9" />
            <path
              d={d}
              fill="none"
              markerEnd={`url(#${arrowId})`}
              stroke={accent}
              strokeDasharray="8 7"
              strokeLinecap="round"
              strokeWidth="4"
            />
          </g>
        ))}
      </svg>
      {geometry.map(({ number, target, from, to }) => (
        <div key={number} aria-hidden="true">
          <span
            className="pointer-events-none absolute z-30 grid size-7 -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full border-2 border-white bg-accent text-[11px] font-bold text-white shadow-[0_5px_14px_rgba(0,0,0,.28)]"
            style={{ left: from[0], top: from[1] }}
          >
            {number}
          </span>
          <span
            data-guide-callout-for={target}
            className="pointer-events-none absolute z-30 grid size-5 -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full border-2 border-accent bg-white/95"
            style={{ left: to[0], top: to[1] }}
          >
            <span className="size-1.5 rounded-full bg-accent" />
          </span>
        </div>
      ))}
    </div>
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
          <p className="font-mono text-[9px] uppercase tracking-[.16em] text-accent">{label}</p>
          <p className="mt-1 truncate text-xs font-semibold text-[#171716]">{title}</p>
        </div>
        <div className="flex gap-1.5" aria-hidden="true">
          <span className="size-2 rounded-full bg-[#c63d35]" />
          <span className="size-2 rounded-full bg-[#e8a127]" />
          <span className="size-2 rounded-full bg-[#248a4b]" />
        </div>
      </figcaption>
      <div className="relative min-h-[330px] w-full overflow-hidden bg-[#f5f3ee] sm:aspect-[1000/560] sm:min-h-0">
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
            <Icon className={`size-3 ${active === item ? "text-accent" : ""}`} />
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
            <button data-guide-target="project-new" className="flex items-center gap-1.5 rounded-md bg-accent px-3 py-2 text-[8px] font-semibold text-white">
              <span className="text-xs leading-none">+</span> Новый проект
            </button>
          </div>
          <div className="p-[5%]">
            <p className="font-mono text-[7px] uppercase tracking-[.17em] text-accent">Рабочее пространство</p>
            <h3 className="mt-2 text-[20px] font-semibold tracking-[-.04em]">Мои приложения</h3>
            <div className="mt-[5%] grid grid-cols-2 gap-[3%]">
              <div className="overflow-hidden rounded-lg border border-[#d8d4cb] bg-[#fcfbf7]">
                <div className="grid aspect-[16/8] place-items-center bg-[#ece8df]">
                  <div className="w-[22%] rounded-md border-[3px] border-[#171716] bg-white p-1.5">
                    <div className="h-4 rounded bg-accent" />
                    <div className="mt-1 h-1 rounded bg-[#ece8df]" />
                  </div>
                </div>
                <div className="p-[5%] text-[9px] font-semibold">Кофе рядом</div>
              </div>
              <button className="grid place-items-center rounded-lg border border-dashed border-[#c9c4b9] bg-[#fcfbf7] text-center">
                <span className="text-[9px] font-semibold"><span className="mx-auto mb-2 grid size-7 place-items-center rounded-md border border-[#d8d4cb] text-accent">+</span>Новый проект</span>
              </button>
            </div>
          </div>
        </div>
        <div className="absolute bottom-[5%] right-[4%] top-[9%] z-[5] w-[48%] rounded-xl border border-[#d8d4cb] bg-[#fcfbf7] shadow-[0_18px_50px_rgba(23,23,22,.2)]">
          <div className="border-b border-[#d8d4cb] p-[5%]">
            <p className="font-mono text-[7px] uppercase tracking-[.16em] text-accent">Новый MAX-проект</p>
            <h4 className="mt-1 text-[15px] font-semibold">Что создаём?</h4>
          </div>
          <div className="space-y-[4%] p-[5%] text-[8px]">
            <label className="block font-medium">Название<div className="mt-1.5 rounded-md border border-[#d8d4cb] bg-white px-2 py-2 text-[#6d6962]">Кофе рядом</div></label>
            <label className="block font-medium">Что пользователь сможет делать?<div data-guide-target="project-description" className="mt-1.5 h-12 rounded-md border border-[#d8d4cb] bg-white p-2 font-normal text-[#6d6962]">Получать баллы, выбирать награды и оформлять заказ</div></label>
            <div>
              <p className="font-medium">Тип приложения</p>
              <div className="mt-1.5 grid grid-cols-2 gap-1.5">
                <div className="rounded-md border border-accent bg-accent/5 p-2">Лояльность <Check className="float-right size-3 text-accent" /></div>
                <div className="rounded-md border border-[#d8d4cb] p-2">Каталог и заказы</div>
              </div>
            </div>
            <button data-guide-target="project-create" className="float-right flex items-center gap-1.5 rounded-md bg-accent px-3 py-2 font-semibold text-white"><Sparkles className="size-3" />Создать проект</button>
          </div>
        </div>
      </div>
      <CalloutLayer callouts={[
        { number: 1, target: "project-new", offset: [-14, 8] },
        { number: 2, target: "project-description", offset: [20, -4] },
        { number: 3, target: "project-create", offset: [-16, 7] },
      ]} />
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
            <button data-guide-target="builder-publish" className="rounded-md bg-accent px-3 py-2 text-[8px] font-semibold text-white">Опубликовать</button>
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
                <div className="mt-3 flex justify-end"><span data-guide-target="builder-send" className="rounded-md bg-accent px-3 py-1.5 text-[7px] font-semibold text-white">Отправить</span></div>
              </div>
            </div>
            <div className="relative flex-1 bg-[#f5f3ee] p-[4%]">
              <p className="font-mono text-[7px] uppercase tracking-[.17em] text-[#8d887f]">Mobile WebView · Живое превью</p>
              <div data-guide-target="builder-preview" className="mx-auto mt-[5%] h-[75%] w-[58%] rounded-[24px] border-[6px] border-[#171716] bg-white p-2 shadow-xl">
                <div className="rounded-xl bg-[#3b2a22] p-3 text-white">
                  <p className="text-[6px] text-white/60">Кофе рядом</p><p className="mt-1 text-[13px] font-semibold">1 250 баллов</p>
                  <div className="mt-3 h-1 rounded bg-white/20"><div className="h-full w-3/5 rounded bg-accent" /></div>
                </div>
                <div className="mt-2 space-y-1.5">
                  {["Заказать кофе", "Мои награды", "История"].map((item) => <div key={item} className="flex items-center rounded-md border border-[#e7e3da] p-2 text-[7px] font-semibold"><span className="mr-2 size-4 rounded bg-[#ece8df]" />{item}<ChevronRight className="ml-auto size-2.5 text-[#aaa59b]" /></div>)}
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
      <CalloutLayer callouts={[
        { number: 1, target: "builder-send", offset: [-19, 7] },
        { number: 2, target: "builder-preview", offset: [11, -19] },
        { number: 3, target: "builder-publish", offset: [-12, 8] },
      ]} />
    </ScreenshotFrame>
  );
}

export function IntegrationVisual() {
  return (
    <ScreenshotFrame label="Снимок 03" title="Интеграции → подключение сервиса и MAX-бота">
      <div className="flex h-full text-[#171716]">
        <Sidebar active="Интеграции" />
        <div className="min-w-0 flex-1 p-[4%]">
          <p className="font-mono text-[7px] uppercase tracking-[.17em] text-accent">Подключения</p>
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
                <div key={String(title)} className={`rounded-lg border bg-[#fcfbf7] p-[5%] ${hot ? "border-accent/60 shadow-[0_8px_24px_var(--color-accent-subtle)]" : "border-[#d8d4cb]"}`}>
                  <div className="flex items-center justify-between"><span className="grid size-7 place-items-center rounded-md bg-[#ece8df]"><ItemIcon className="size-3.5 text-accent" /></span><span className="rounded-full bg-[#f5f3ee] px-2 py-1 text-[6px] text-[#8d887f]">Не подключено</span></div>
                  <p className="mt-[7%] text-[10px] font-semibold">{String(title)}</p><p className="mt-1 text-[7px] text-[#8d887f]">{String(copy)}</p>
                  <button data-guide-target={hot ? "integration-connect" : undefined} className={`mt-[7%] rounded-md px-3 py-1.5 text-[7px] font-semibold ${hot ? "bg-accent text-white" : "border border-[#d8d4cb]"}`}>{String(action)}</button>
                </div>
              );
            })}
          </div>
          <div className="absolute bottom-[7%] right-[5%] w-[38%] rounded-lg border border-[#d8d4cb] bg-white p-[3%] shadow-xl">
            <div className="flex items-center gap-2"><Bot className="size-4 text-accent" /><p className="text-[10px] font-semibold">Подключить MAX-бота</p></div>
            <p className="mt-2 text-[7px] leading-3 text-[#8d887f]">Вставьте токен из MAX для партнёров. Токен будет проверен через API.</p>
            <div data-guide-target="integration-token" className="mt-2 rounded-md border border-[#d8d4cb] px-2 py-2 font-mono text-[7px] text-[#aaa59b]">Введите токен бота</div>
            <button className="mt-2 rounded-md bg-accent px-3 py-1.5 text-[7px] font-semibold text-white">Проверить и сохранить</button>
          </div>
        </div>
      </div>
      <CalloutLayer callouts={[
        { number: 1, target: "integration-connect", offset: [25, 2] },
        { number: 2, target: "integration-token", offset: [21, 13] },
      ]} />
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
            <p className="font-mono text-[7px] uppercase tracking-[.17em] text-accent">Release</p>
            <h3 className="mt-2 text-[20px] font-semibold tracking-[-.04em]">Публикация в MAX</h3>
            <div className="mt-[6%] rounded-lg border border-[#d8d4cb] bg-[#fcfbf7] p-[5%]">
              <div className="flex items-center justify-between"><div><p className="text-[9px] font-semibold">Production</p><p className="mt-1 text-[7px] text-[#8d887f]">Постоянный HTTPS-адрес и проверка контейнера</p></div><Cloud className="size-5 text-accent" /></div>
              <div className="mt-[5%] grid grid-cols-3 gap-2 text-[7px]">
                <div className="rounded-md bg-[#f5f3ee] p-2"><p className="text-[#8d887f]">Версия</p><p className="mt-1 font-semibold">v.12</p></div>
                <div className="rounded-md bg-[#f5f3ee] p-2"><p className="text-[#8d887f]">Health</p><p className="mt-1 font-semibold">готов</p></div>
                <div className="rounded-md bg-[#f5f3ee] p-2"><p className="text-[#8d887f]">Webhook</p><p className="mt-1 font-semibold">ожидает URL</p></div>
              </div>
              <button data-guide-target="launch-publish" className="mt-[6%] flex items-center gap-1.5 rounded-md bg-accent px-3 py-2 text-[8px] font-semibold text-white"><Rocket className="size-3" />Опубликовать</button>
            </div>
          </div>
          <aside className="w-[36%] border-l border-[#d8d4cb] bg-[#fcfbf7] p-[4%]">
            <div className="flex items-center justify-between"><p className="text-[11px] font-semibold">Запуск в MAX</p><span className="text-[9px] font-semibold">60%</span></div>
            <div className="mt-2 h-1.5 rounded-full bg-[#e7e3da]"><div className="h-full w-3/5 rounded-full bg-accent" /></div>
            <div className="mt-[7%] border-l-2 border-accent pl-3"><p className="font-mono text-[6px] uppercase tracking-[.14em] text-[#8d887f]">Шаг 4 из 5</p><p className="mt-1 text-[9px] font-semibold">Опубликуйте приложение</p><p className="mt-1 text-[7px] leading-3 text-[#8d887f]">Получите постоянный HTTPS-адрес.</p></div>
            <div className="mt-[7%] space-y-1 border-y border-[#e7e3da] py-2">
              {steps.map(([step, done], index) => <div key={step} data-guide-target={index === 3 ? "launch-current-step" : undefined} className={`flex items-center gap-2 rounded-md px-2 py-1.5 text-[7px] ${index === 3 ? "bg-accent/8 font-semibold" : ""}`}><span className={`grid size-4 place-items-center rounded-full border ${done ? "border-[#248a4b]/40 bg-[#248a4b]/10 text-[#248a4b]" : index === 3 ? "border-accent bg-accent text-white" : "border-[#d8d4cb] text-[#8d887f]"}`}>{done ? <Check className="size-2.5" /> : index + 1}</span>{step}</div>)}
            </div>
            <div className="mt-[7%] grid grid-cols-2 gap-1.5"><button className="rounded-md border border-[#d8d4cb] px-2 py-2 text-[7px]">Приложение</button><button className="rounded-md border border-[#d8d4cb] px-2 py-2 text-[7px]">MAX-бот</button></div>
          </aside>
        </div>
      </div>
      <CalloutLayer callouts={[
        { number: 1, target: "launch-publish", offset: [22, 21] },
        { number: 2, target: "launch-current-step", offset: [13, -25] },
      ]} />
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
            <div className="flex items-center justify-between"><div><p className="text-[10px] font-semibold">Токен бота</p><p className="mt-1 text-[7px] text-[#727780]">Используется сервером для Bot API.</p></div><button data-guide-target="partner-copy" className="flex items-center gap-1 rounded-md border border-black/10 px-2 py-1.5 text-[7px]"><Copy className="size-3" />Копировать</button></div>
            <div className="mt-2 rounded-md border border-black/10 bg-[#f7f8fa] px-3 py-2 font-mono text-[7px] text-[#727780]">••••••••••••••••••••••••</div>
            <div className="my-[5%] h-px bg-black/10" />
            <label className="block text-[10px] font-semibold">Ссылка на мини-приложение</label>
            <div className="mt-2 flex gap-2"><div data-guide-target="partner-url" className="min-w-0 flex-1 truncate rounded-md border-2 border-accent bg-white px-3 py-2 font-mono text-[7px]">https://app-42.lead-generator.ru</div><button className="rounded-md border border-black/10 px-3 text-[7px]">Проверить</button></div>
            <div className="mt-[4%]"><p className="text-[9px] font-semibold">Кнопка запуска</p><div className="mt-2 flex gap-2 text-[7px]"><button className="rounded-full border-2 border-accent bg-[#fff3ef] px-3 py-1.5 font-semibold text-accent">Открыть</button><button className="rounded-full border border-black/10 px-3 py-1.5">Старт</button><button className="rounded-full border border-black/10 px-3 py-1.5">Играть</button></div></div>
            <div className="mt-[5%] flex justify-end"><button data-guide-target="partner-save" className="rounded-md bg-[#15171a] px-4 py-2 text-[8px] font-semibold text-white">Сохранить</button></div>
          </div>
        </div>
      </div>
      <CalloutLayer callouts={[
        { number: 1, target: "partner-copy", offset: [8, -7] },
        { number: 2, target: "partner-url", offset: [30, -4] },
        { number: 3, target: "partner-save", offset: [-15, 5] },
      ]} />
    </ScreenshotFrame>
  );
}

export function DashboardVisual() {
  return (
    <ScreenshotFrame label="Снимок 06" title="После запуска → здоровье production">
      <div className="flex h-full text-[#171716]">
        <Sidebar active="Публикация" />
        <div className="min-w-0 flex-1 p-[4%]">
          <div className="flex items-end justify-between"><div><p className="font-mono text-[7px] uppercase tracking-[.17em] text-accent">Production</p><h3 className="mt-2 text-[20px] font-semibold tracking-[-.04em]">После запуска</h3></div><a data-guide-target="dashboard-open" className="flex items-center gap-1 text-[8px] font-semibold text-accent">Открыть приложение <ExternalLink className="size-3" /></a></div>
          <div className="mt-[5%] grid grid-cols-4 gap-[2%]">
            {[
              [Cloud, "Контейнер", "Работает", true],
              [Activity, "Health-check", "Отвечает", true],
              [Bot, "MAX-бот", "Кофе рядом", true],
              [Webhook, "Webhook", "Активен", true],
            ].map(([Icon, title, copy, ok]) => {
              const ItemIcon = Icon as typeof Cloud;
              return <div key={String(title)} className="rounded-lg border border-[#d8d4cb] bg-[#fcfbf7] p-[8%]"><div className="flex items-center justify-between"><span className="grid size-7 place-items-center rounded-md bg-[#ece8df]"><ItemIcon className="size-3.5 text-accent" /></span>{ok ? <Check className="size-3.5 text-[#248a4b]" /> : <CircleAlert className="size-3.5 text-[#e8a127]" />}</div><p className="mt-[14%] text-[8px] font-semibold">{String(title)}</p><p className="mt-1 text-[7px] text-[#8d887f]">{String(copy)}</p></div>;
            })}
          </div>
          <div className="mt-[4%] grid grid-cols-[1.25fr_.75fr] gap-[3%]">
            <div className="overflow-hidden rounded-lg border border-[#d8d4cb] bg-[#fcfbf7]">
              <div className="flex items-center justify-between border-b border-[#d8d4cb] p-[4%]"><div><p className="font-mono text-[6px] uppercase tracking-[.15em] text-[#8d887f]">Versions</p><p className="mt-1 text-[10px] font-semibold">История публикаций</p></div><button className="rounded-md border border-[#d8d4cb] px-2 py-1 text-[7px]">Обновить</button></div>
              {["v.12 · Production build", "v.11 · Обновление каталога", "v.10 · Первая публикация"].map((item, index) => <div key={item} data-guide-target={index === 0 ? "dashboard-version" : undefined} className="flex items-center gap-3 border-b border-[#e7e3da] p-[3%] text-[7px]"><span className="grid size-6 place-items-center rounded-full bg-[#248a4b]/10 text-[#248a4b]"><Check className="size-3" /></span><span className="font-semibold">{item}</span><span className="ml-auto text-[#8d887f]">{index === 0 ? "сейчас" : `${index} дн.`}</span><span className="font-semibold text-[#248a4b]">done</span></div>)}
            </div>
            <div className="rounded-lg border border-[#d8d4cb] bg-[#fcfbf7] p-[7%]"><p className="font-mono text-[6px] uppercase tracking-[.15em] text-[#8d887f]">Эксплуатация</p><p className="mt-2 text-[10px] font-semibold">Без разработчика</p><div className="mt-[10%] space-y-3 text-[7px] text-[#6d6962]"><p className="flex gap-2"><ShieldCheck className="size-3 shrink-0 text-accent" />Health-check после релиза</p><p className="flex gap-2"><Cloud className="size-3 shrink-0 text-accent" />Всегда активный контейнер</p><p className="flex gap-2"><ArrowRight className="size-3 shrink-0 text-accent" />Версии и откат</p></div></div>
          </div>
        </div>
      </div>
      <CalloutLayer callouts={[
        { number: 1, target: "dashboard-open", offset: [-15, 8] },
        { number: 2, target: "dashboard-version", offset: [8, 20] },
      ]} />
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
          <div className="flex items-center justify-between"><span className="font-mono text-[9px] text-accent">{number}</span><Icon className="size-4 text-accent" /></div>
          <p className="mt-8 text-sm font-semibold">{title}</p><p className="mt-1 text-xs leading-5 text-[#8d887f]">{copy}</p>
          {index < nodes.length - 1 && <span className="absolute -right-2 top-1/2 z-10 hidden size-4 -translate-y-1/2 place-items-center rounded-full bg-[#171716] text-white md:grid"><ChevronRight className="size-3" /></span>}
        </div>
      ))}
    </div>
  );
}

export const guideColors = { ink, paper, canvas, line, accent, muted, success };
