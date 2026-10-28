"use client";

import { useEffect, useId, useState } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { ArrowDownToLine, ArrowUpRight, MousePointer2, RefreshCw, Users, Zap } from "lucide-react";
import {
  getMaxAnalytics, getMaxAnalyticsEvents, type MaxAnalyticsDay,
  type MaxAnalyticsEventKind, type MaxAnalyticsEventType,
} from "@/lib/api/max-analytics";
import "./max-owner-analytics.css";

const metrics = [
  { key: "users", label: "Пользователи", icon: Users },
  { key: "opens", label: "Открытия из MAX", icon: ArrowUpRight },
  { key: "actions", label: "Действия", icon: MousePointer2 },
  { key: "events", label: "Все события", icon: Zap },
] as const;
type Metric = typeof metrics[number]["key"];
const kinds: { value: MaxAnalyticsEventKind | "all"; label: string }[] = [
  { value: "all", label: "Все" }, { value: "open", label: "Открытия" },
  { value: "action", label: "Действия" }, { value: "event", label: "События" },
];
const eventLabels: Record<MaxAnalyticsEventType, string> = {
  app_open: "Открытие приложения", screen_view: "Просмотр экрана", item_view: "Просмотр элемента",
  button_click: "Нажатие кнопки", form_submit: "Отправка формы", cart_add: "Добавление в корзину",
  checkout_start: "Начало оформления", order_created: "Заказ создан", booking_created: "Бронирование создано",
  lead_created: "Заявка создана",
};
const reportedLabels: Partial<Record<MaxAnalyticsEventType, string>> = {
  form_submit: "Сообщение об отправке формы", order_created: "Сообщение о заказе",
  booking_created: "Сообщение о бронировании", lead_created: "Сообщение о заявке",
};
const kindLabels: Record<MaxAnalyticsEventKind, string> = {
  open: "Открытие из MAX", action: "Действие", event: "Событие приложения",
};
const timeFormatter = new Intl.DateTimeFormat("ru-RU", { timeZone: "Europe/Moscow", hour: "2-digit", minute: "2-digit", second: "2-digit" });
const dateFormatter = new Intl.DateTimeFormat("ru-RU", { timeZone: "Europe/Moscow", day: "numeric", month: "short" });
function dateLabel(value: string) { return dateFormatter.format(new Date(value)); }
function timeLabel(value: string | number) { return timeFormatter.format(new Date(value)); }

function useRefreshAvailable() {
  const [available, setAvailable] = useState(false);
  useEffect(() => {
    const sync = () => setAvailable(document.visibilityState === "visible" && navigator.onLine);
    sync();
    document.addEventListener("visibilitychange", sync);
    window.addEventListener("online", sync);
    window.addEventListener("offline", sync);
    return () => {
      document.removeEventListener("visibilitychange", sync);
      window.removeEventListener("online", sync);
      window.removeEventListener("offline", sync);
    };
  }, []);
  return available;
}

function DailyChart({ daily, metric }: { daily: MaxAnalyticsDay[]; metric: Metric }) {
  const chartId = useId();
  const label = metrics.find(item => item.key === metric)!.label;
  const maximum = Math.max(1, ...daily.map(day => day[metric]));
  const points = daily.map((day, index) => ({
    x: daily.length === 1 ? 360 : 36 + index / (daily.length - 1) * 648,
    y: 182 - day[metric] / maximum * 152,
    day,
  }));
  const line = points.map(point => `${point.x},${point.y}`).join(" ");
  return <div className="max-analytics-chart" key={metric}>
    <div className="max-analytics-section-heading"><h3>{label} по дням</h3><span>0 — {maximum.toLocaleString("ru-RU")}</span></div>
    <svg viewBox="0 0 720 200" preserveAspectRatio="none" role="img" aria-labelledby={`${chartId}-title ${chartId}-description`}>
      <title id={`${chartId}-title`}>{label} по дням</title>
      <desc id={`${chartId}-description`}>Дневные значения за выбранный период. Точные числа — в таблице под графиком.</desc>
      <defs><linearGradient id={chartId} x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="currentColor" stopOpacity=".15" /><stop offset="100%" stopColor="currentColor" stopOpacity="0" /></linearGradient></defs>
      <path className="max-analytics-gridline" d="M36 30H684 M36 106H684 M36 182H684" />
      {points.length > 1 && <polygon points={`${points[0].x},182 ${line} ${points.at(-1)!.x},182`} fill={`url(#${chartId})`} />}
      <polyline points={line} fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
      {points.map(point => <circle key={point.day.date} cx={point.x} cy={point.y} r={daily.length > 31 ? 2 : 3} fill="currentColor"><title>{dateLabel(point.day.date)}: {point.day[metric]}</title></circle>)}
    </svg>
    {daily.length > 0 && <div className="max-analytics-chart-dates"><span>{dateLabel(daily[0].date)}</span><span>{dateLabel(daily.at(-1)!.date)}</span></div>}
    <details className="max-analytics-details"><summary>Таблица по дням</summary>
      <table><caption className="sr-only">{label} по дням, московское время</caption>
        <thead><tr><th scope="col">Дата</th><th scope="col">{label}</th></tr></thead>
        <tbody>{daily.map(day => <tr key={day.date}><th scope="row">{day.date}</th><td>{day[metric].toLocaleString("ru-RU")}</td></tr>)}</tbody>
      </table>
    </details>
  </div>;
}

export function MaxOwnerAnalytics({ projectId }: { projectId: string }) {
  // Reset selections with the owner scope; cached responses are also keyed by project.
  return <OwnerAnalytics key={projectId} projectId={projectId} />;
}

function OwnerAnalytics({ projectId }: { projectId: string }) {
  const headingId = useId();
  const [days, setDays] = useState(30);
  const [metric, setMetric] = useState<Metric>("users");
  const [kind, setKind] = useState<MaxAnalyticsEventKind | "all">("all");
  const refreshAvailable = useRefreshAvailable();
  const interval = refreshAvailable ? 10_000 : false;
  const query = useQuery({ queryKey: ["max-owner-analytics", projectId, days],
    queryFn: () => getMaxAnalytics(projectId, days), retry: false,
    refetchInterval: interval, refetchIntervalInBackground: false });
  const feed = useInfiniteQuery({
    queryKey: ["max-owner-analytics-events", projectId, days, kind],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam, signal }) => getMaxAnalyticsEvents(projectId, {
      days, kind: kind === "all" ? undefined : kind, cursor: pageParam, signal,
    }),
    getNextPageParam: page => page.next_cursor ?? undefined,
    retry: false, refetchInterval: interval, refetchIntervalInBackground: false,
  });
  const data = !query.isError ? query.data : undefined;
  const pages = !feed.isError ? feed.data?.pages : undefined;
  const items = Array.from(new Map(pages?.flatMap(page => page.items).map(item => [item.id, item]) ?? []).values())
    .sort((left, right) => Date.parse(right.occurred_at) - Date.parse(left.occurred_at) || right.id.localeCompare(left.id));
  const updating = query.isFetching || feed.isFetching;
  const failed = query.isError || feed.isError;
  const updatedAt = Math.min(query.dataUpdatedAt, feed.dataUpdatedAt);
  const status = failed ? "Не удалось обновить" : updating ? "Обновляем…" : !refreshAvailable ? "Автообновление на паузе" : updatedAt ? `Обновлено в ${timeLabel(updatedAt)} МСК` : "Ожидаем данные";
  return <section className="max-owner-analytics" aria-labelledby={headingId}>
    <header className="max-analytics-header">
      <h2 id={headingId}>Активность</h2>
      <div className="max-analytics-controls">
        <label className="sr-only" htmlFor={`${headingId}-period`}>Период статистики</label>
        <select id={`${headingId}-period`} aria-label="Период статистики" value={days} onChange={event => setDays(Number(event.target.value))}>
          <option value={7}>7 дней</option><option value={30}>30 дней</option><option value={90}>90 дней</option>
        </select>
        <button type="button" className="max-analytics-refresh" aria-label="Обновить статистику" disabled={updating}
          onClick={() => { void query.refetch(); void feed.refetch(); }}><RefreshCw size={16} aria-hidden="true" /></button>
      </div>
    </header>
    <div className="max-analytics-status"><span>{data ? `${dateLabel(data.from_date)} — ${dateLabel(data.to_date)} · МСК` : "Московское время"}</span><span role="status">{status}</span></div>
    {query.isPending && <div className="max-analytics-loading" role="status">Загружаем статистику…<div aria-hidden="true" /></div>}
    {query.isError && <p role="alert" className="max-analytics-empty">Статистика временно недоступна. Попробуйте обновить.</p>}
    {data && <>
      <div className="max-analytics-metrics" aria-label="Показатель на графике">
        {metrics.map(({ key, label, icon: Icon }) => <button key={key} type="button" data-metric={key} aria-pressed={metric === key}
          onClick={() => setMetric(key)} className="max-analytics-metric">
          <span className="max-analytics-metric-label"><Icon size={16} aria-hidden="true" />{label}</span>
          <strong>{data[key].toLocaleString("ru-RU")}</strong><span className="max-analytics-metric-mark" aria-hidden="true"><ArrowUpRight size={14} /></span>
        </button>)}
      </div>
      {data.events === 0 ? <p className="max-analytics-empty">Нет полученных событий за этот период.</p> : <DailyChart daily={data.daily} metric={metric} />}
    </>}
    <section className="max-analytics-feed" aria-labelledby={`${headingId}-events`}>
      <div className="max-analytics-section-heading"><h3 id={`${headingId}-events`}>Последние события</h3><span>МСК</span></div>
      <div className="max-analytics-filters" role="group" aria-label="Тип событий">{kinds.map(filter => <button key={filter.value} type="button" aria-pressed={kind === filter.value} onClick={() => setKind(filter.value)}>{filter.label}</button>)}</div>
      {feed.isPending && <p className="max-analytics-empty" role="status">Загружаем события…</p>}
      {feed.isError && <p className="max-analytics-empty" role="alert">События временно недоступны. Попробуйте обновить.</p>}
      {pages && items.length === 0 && <p className="max-analytics-empty">Событий пока нет.</p>}
      {pages && items.length > 0 && <ol className="max-analytics-events">{items.map(item => {
        const Icon = item.kind === "open" ? ArrowUpRight : item.kind === "action" ? MousePointer2 : Zap;
        return <li key={item.id}><span className="max-analytics-event-icon"><Icon size={16} aria-hidden="true" /></span>
          <span>{item.event_type ? (item.kind === "event" ? reportedLabels[item.event_type] : undefined) ?? eventLabels[item.event_type] ?? kindLabels[item.kind] : kindLabels[item.kind]}</span>
          <time dateTime={item.occurred_at}>{dateLabel(item.occurred_at)}<br />{timeLabel(item.occurred_at)}</time>
        </li>;
      })}</ol>}
      {pages && feed.hasNextPage && <button className="max-analytics-more" type="button" disabled={feed.isFetching} onClick={() => void feed.fetchNextPage()}><ArrowDownToLine size={16} aria-hidden="true" />{feed.isFetchingNextPage ? "Загружаем…" : "Показать ещё"}</button>}
    </section>
    <details className="max-analytics-details max-analytics-method"><summary>Как считаем</summary>
      <p>Пользователи — уникальные участники за весь период. Один человек может учитываться в нескольких днях, поэтому сумма дневных значений бывает больше.</p>
      <p>Открытия — подтверждённые входы из MAX. Действия — сохранённые действия. Все события включают открытия, действия и события приложения. Повторная доставка не увеличивает счётчик. Просмотры в редакторе и технические проверки не учитываются.</p>
      <p>Категория «События» — сообщения самого приложения. Сообщение о заказе не подтверждает сохранение заказа; подтверждённые операции относятся к «Действиям».</p>
      <p>Сбор работает в версиях приложения с подключённой статистикой. История до подключения не восстанавливается. Старые события без точного типа показываем общими названиями.</p>
      <p>Московское время. Проверяем новые данные каждые 10 секунд, пока вкладка видна и есть сеть. Это полученные события, а не число людей онлайн.</p>
      {data?.measured_since && <p>Первое событие: {dateLabel(data.measured_since)}, {timeLabel(data.measured_since)} МСК.</p>}
    </details>
  </section>;
}
