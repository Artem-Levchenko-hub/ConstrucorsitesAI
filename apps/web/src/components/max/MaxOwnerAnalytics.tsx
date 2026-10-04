"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { getMaxAnalytics } from "@/lib/api/max-analytics";

const metrics = [
  ["users", "Пользователи"], ["opens", "Открытия из MAX"],
  ["actions", "Сохранённые действия"], ["events", "Все события"],
] as const;

export function MaxOwnerAnalytics({ projectId }: { projectId: string }) {
  const [days, setDays] = useState(30);
  const query = useQuery({ queryKey: ["max-owner-analytics", projectId, days],
    queryFn: () => getMaxAnalytics(projectId, days), retry: false });
  const data = !query.isError ? query.data : undefined;
  return (
    <section className="max-dashboard-system" aria-labelledby="max-analytics-heading">
      <header>
        <div><h2 id="max-analytics-heading">Посещения и действия</h2>
          <p>Активность пользователей приложения из MAX</p></div>
        <div className="flex flex-wrap items-center gap-2">
          <label className="text-sm">Период <select aria-label="Период статистики" value={days}
            className="rounded-md border border-border bg-bg-primary px-2 py-2"
            onChange={event => setDays(Number(event.target.value))}>
            <option value={7}>7 дней</option><option value={30}>30 дней</option><option value={90}>90 дней</option>
          </select></label>
          <Button variant="outline" onClick={() => void query.refetch()} disabled={query.isFetching}>
            <RefreshCw className="size-4" />Обновить статистику
          </Button>
        </div>
      </header>
      {query.isPending && <p role="status" className="py-4 text-sm text-fg-secondary">Загружаем статистику…</p>}
      {query.isError && <p role="alert" className="py-4 text-sm text-danger-fg">Статистика временно недоступна. Попробуйте обновить.</p>}
      {data && <>
        <p className="mb-4 text-sm text-fg-secondary">{data.from_date} — {data.to_date} · Московское время</p>
        <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          {metrics.map(([key, label]) => <div key={key} data-metric={key}
            className="rounded-xl border border-border p-4">
            <dt className="text-sm text-fg-secondary">{label}</dt>
            <dd className="mt-2 text-2xl font-semibold tabular-nums">{data[key].toLocaleString("ru-RU")}</dd>
          </div>)}
        </dl>
        {data.events === 0 ? <p className="max-dashboard-empty mt-4">Нет полученных событий за выбранный период. События появятся после подтверждённого входа из MAX в версию приложения с подключённым сбором статистики.</p>
          : <details className="mt-4"><summary className="cursor-pointer text-sm">По дням</summary>
            <div className="mt-3 overflow-x-auto"><table className="w-full text-left text-sm tabular-nums">
              <caption className="sr-only">Полученные события по дням, московское время</caption>
              <thead><tr><th scope="col" className="py-2">Дата</th>{metrics.map(([key, label]) => <th scope="col" key={key} className="px-3 py-2">{label}</th>)}</tr></thead>
              <tbody>{data.daily.map(day => <tr key={day.date} className="border-t border-border"><th scope="row" className="py-2 font-normal">{day.date}</th>{metrics.map(([key]) => <td key={key} className="px-3 py-2">{day[key]}</td>)}</tr>)}</tbody>
            </table></div>
          </details>}
        <p className="mt-4 text-sm text-fg-secondary">Учитываются полученные события: подтверждённые входы из MAX, сохранённые действия и события приложения. Повторная доставка одного события не увеличивает счётчик. Просмотры в редакторе и технические проверки не учитываются. Пользователи — уникальные участники за период; сумма по дням может быть больше.</p>
        {data.measured_since && <p className="mt-2 text-xs text-fg-secondary">Первое полученное событие: {new Date(data.measured_since).toLocaleString("ru-RU", { timeZone: data.timezone })} МСК. История до подключения сбора не восстанавливается.</p>}
      </>}
    </section>
  );
}
