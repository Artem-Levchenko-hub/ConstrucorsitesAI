"use client";

import { useQueries, useQuery } from "@tanstack/react-query";
import { ArrowLeft, ChevronRight, RefreshCw, Search } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  getMetric,
  getMetricCatalog,
  getMetricFilters,
  type MetricChoice,
  type MetricDefinition,
  type MetricGroup,
  type MetricPoint,
  type MetricResult,
  type MetricScope,
  type MonitorPeriod,
} from "@/lib/api/monitoring";
import "./monitoring.css";

const GROUPS: Record<MetricGroup, string> = {
  infrastructure: "VPS и инциденты",
  activity: "Пользователи и приложения",
  generation: "Генерация и публикации",
  ai: "ИИ и расходы",
  platform: "API и интеграции",
};
const DEFAULT_SCOPE: MetricScope = { period: "24h", client: "", project: "", vps: "" };
const OVERVIEW = [1, 2, 43, 45, 47, 48, 86, 20, 100];
const STATE_LABELS: Record<MetricResult["state"], string> = {
  measured: "Измерено",
  no_data: "Нет данных",
  stale: "Данные устарели",
  source_unavailable: "Источник недоступен",
  not_applicable: "Не применимо",
};
const formatNumber = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 }).format;
const timeFormatter = new Intl.DateTimeFormat("ru-RU", {
  day: "2-digit",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
  timeZone: "Asia/Tomsk",
});
const formatDate = (value: string) => timeFormatter.format(new Date(value));

function metricQuery(id: number, scope: MetricScope) {
  return {
    queryKey: ["admin-monitoring", id, scope],
    queryFn: ({ signal }: { signal: AbortSignal }) => getMetric(id, scope, signal),
    staleTime: 30_000,
    refetchInterval: 60_000,
    retry: false,
  };
}

function ScopeSelect({
  label, options, value, onChange, disabled,
}: {
  label: string;
  options: MetricChoice[];
  value: string;
  onChange: (id: string) => void;
  disabled?: boolean;
}) {
  return (
    <label>
      {label}
      <select value={value} onChange={(event) => onChange(event.target.value)} disabled={disabled}>
        <option value="">Все</option>
        {options.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}
      </select>
    </label>
  );
}

function MetricChart({ points, unit }: { points: MetricPoint[]; unit: string }) {
  const series = [...new Set(points.flatMap((point) => point.at ? [point.series] : []))].sort();
  const [selectedSeries, setSelectedSeries] = useState("");
  const selected = series.includes(selectedSeries) ? selectedSeries : series[0];
  const values = points
    .filter((point) => point.at && point.series === selected)
    .map((point) => ({ at: point.at!, value: point.value }))
    .sort((a, b) => a.at.localeCompare(b.at));
  if (values.length < 2) return null;

  const first = values[0];
  const last = values[values.length - 1];
  const start = Date.parse(first.at);
  const elapsed = Math.max(1, Date.parse(last.at) - start);
  const lower = Math.min(0, ...values.map((point) => point.value));
  const upper = Math.max(0, ...values.map((point) => point.value));
  const scale = upper - lower || 1;
  const baseline = (upper / scale) * 100;

  return (
    <figure className="monitor-chart">
      <figcaption>
        <strong>Динамика</strong>
        {series.length > 1 && (
          <select aria-label="Серия графика" value={selected} onChange={(e) => setSelectedSeries(e.target.value)}>
            {series.map((name) => <option key={name}>{name}</option>)}
          </select>
        )}
      </figcaption>
      <div className="monitor-chart-body" aria-hidden="true">
        <div className="monitor-chart-scale">
          <span>{formatNumber(upper)} {unit}</span>
          <span>{formatNumber(lower)} {unit}</span>
        </div>
        <div className="monitor-chart-plot">
          <i className="monitor-chart-baseline" style={{ top: baseline + "%" }} />
          {values.map((point) => (
            <span
              className="monitor-chart-bar"
              key={point.at + "/" + point.value}
              title={formatDate(point.at) + " · " + formatNumber(point.value)}
              style={{
                left: ((Date.parse(point.at) - start) / elapsed) * 100 + "%",
                top: Math.min(baseline, ((upper - point.value) / scale) * 100) + "%",
                height: "max(2px, " + (Math.abs(point.value) / scale) * 100 + "%)",
              }}
            />
          ))}
        </div>
      </div>
      <div className="monitor-chart-dates"><span>{formatDate(first.at)}</span><span>{formatDate(last.at)}</span></div>
    </figure>
  );
}

function MetricTable({ points, metric }: { points: MetricPoint[]; metric: MetricDefinition }) {
  const [page, setPage] = useState(0);
  const size = 20;
  const current = Math.min(page, Math.max(0, Math.ceil(points.length / size) - 1));
  const hasP95 = points.some((point) => point.p95 !== null);
  const hasSamples = points.some((point) => point.samples !== null);
  const hasMean = points.some((point) => point.mean != null);
  return (
    <>
      <div className="monitor-table" tabIndex={0} aria-label="Таблица измерений">
        <table>
          <thead><tr>
            <th scope="col">Серия</th>
            <th scope="col">Время</th>
            <th scope="col">{hasP95 || hasMean ? "Медиана" : "Значение"}</th>
            {hasMean && <th scope="col">Среднее</th>}
            {hasP95 && <th scope="col">p95</th>}
            {metric.id === 37 && <th scope="col">Доля ошибок</th>}
            {hasSamples && <th scope="col">Выборка</th>}
          </tr></thead>
          <tbody>
            {points.slice(current * size, (current + 1) * size).map((point, index) => (
              <tr key={point.series + "/" + String(point.at) + "/" + index}>
                <th scope="row">{point.series}</th>
                <td>{point.at ? formatDate(point.at) : "За период"}</td>
                <td>{formatNumber(point.value)} {metric.id === 37 ? "шт." : metric.unit}</td>
                {hasMean && <td>{point.mean == null ? "—" : formatNumber(point.mean)}</td>}
                {hasP95 && <td>{point.p95 === null ? "—" : formatNumber(point.p95)}</td>}
                {metric.id === 37 && <td>{point.samples ? formatNumber(100 * point.value / point.samples) + " %" : "—"}</td>}
                {hasSamples && <td>{point.samples === null ? "—" : formatNumber(point.samples)}</td>}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {points.length > size && <div className="monitor-pagination">
        <span>{current * size + 1}–{Math.min(points.length, (current + 1) * size)} из {points.length}</span>
        <Button type="button" size="sm" variant="secondary" disabled={current === 0} onClick={() => setPage(current - 1)}>Назад</Button>
        <Button type="button" size="sm" variant="secondary" disabled={(current + 1) * size >= points.length} onClick={() => setPage(current + 1)}>Далее</Button>
      </div>}
    </>
  );
}

function MetricDetails({ metric, scope }: { metric: MetricDefinition; scope: MetricScope }) {
  const query = useQuery(metricQuery(metric.id, scope));
  const result = query.data;
  return (
    <section id="metric-details" className="monitor-details" aria-labelledby="metric-heading">
      <div className="monitor-detail-heading">
        <div>
          <h3 id="metric-heading">№{metric.id}. {metric.title}</h3>
          <p>{metric.formula}</p>
        </div>
        <Button type="button" variant="secondary" size="sm" disabled={query.isFetching} onClick={() => void query.refetch()}>
          <RefreshCw aria-hidden="true" />Обновить
        </Button>
      </div>
      <dl className="monitor-definition">
        <div><dt>Источник</dt><dd>{metric.source ?? "Предстоит подключить"}</dd></div>
        <div><dt>Единица</dt><dd>{metric.unit}</dd></div>
        {result && <div><dt>Проверено · Томск</dt><dd>{formatDate(result.measured_at)}</dd></div>}
      </dl>
      {metric.note && <p className="monitor-note">{metric.note}</p>}
      {query.isPending && <p className="monitor-status" role="status">Загружаем измерения…</p>}
      {query.isError && <p className="monitor-status" role="alert">Дашборд не получил ответ. Обновите измерения.</p>}
      {result && <>
        <p className="monitor-status" role="status" data-state={result.state}>
          <strong>{STATE_LABELS[result.state]}</strong>{result.message && <span>{result.message}</span>}
        </p>
        {result.points.length > 0 && <>
          <MetricChart points={result.points} unit={metric.unit} />
          <MetricTable points={result.points} metric={metric} />
        </>}
      </>}
    </section>
  );
}

function Overview({ metrics, scope, onSelect }: {
  metrics: MetricDefinition[];
  scope: MetricScope;
  onSelect: (metric: MetricDefinition) => void;
}) {
  const chosen = OVERVIEW.flatMap((id) => {
    const metric = metrics.find((candidate) => candidate.id === id);
    return metric ? [metric] : [];
  });
  const queries = useQueries({ queries: chosen.map((metric) => metricQuery(metric.id, scope)) });
  return (
    <section className="monitor-overview" aria-label="Обзор">
      {chosen.map((metric, index) => {
        const query = queries[index];
        const result = query.data;
        const count = result?.points.length ?? 0;
        const onlyValue = count === 1 ? result?.points[0].value : undefined;
        const value = onlyValue !== undefined
          ? formatNumber(onlyValue) + " " + metric.unit
          : count > 1 ? "Открыть разбивку" : result ? STATE_LABELS[result.state] : query.isPending ? "Загрузка…" : "Ошибка загрузки";
        return (
          <button type="button" key={metric.id} onClick={() => onSelect(metric)}>
            <span>{metric.title}</span>
            <strong>{value}</strong>
            <ChevronRight aria-hidden="true" />
          </button>
        );
      })}
    </section>
  );
}

export function MonitoringDashboard() {
  const [scope, setScope] = useState<MetricScope>(DEFAULT_SCOPE);
  const [group, setGroup] = useState<MetricGroup | "all">("all");
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState(2);
  const catalog = useQuery({ queryKey: ["admin-monitoring", "catalog"], queryFn: ({ signal }) => getMetricCatalog(signal), staleTime: 300_000 });
  const filters = useQuery({ queryKey: ["admin-monitoring", "filters"], queryFn: ({ signal }) => getMetricFilters(signal), staleTime: 60_000 });
  const metrics = catalog.data ?? [];
  const filterOptions = filters.data;

  useEffect(() => {
    const read = () => {
      const hash = window.location.hash.slice(1);
      const match = /^metric-(\d+)$/.exec(hash);
      if (match) setSelectedId(Number(match[1]));
    };
    read();
    window.addEventListener("hashchange", read);
    return () => window.removeEventListener("hashchange", read);
  }, []);

  const normalized = search.toLocaleLowerCase("ru").trim().replace(/^[№#]\s*/, "");
  const visible = metrics.filter((metric) => (
    (group === "all" || group === metric.group)
      && (!normalized || String(metric.id) === normalized || metric.title.toLocaleLowerCase("ru").includes(normalized))
  ));
  const metric = visible.find(({ id }) => id === selectedId) ?? visible[0];
  const setMetric = (definition: MetricDefinition) => {
    setSelectedId(definition.id);
    setSearch("");
    setGroup(definition.group);
    window.history.replaceState(null, "", "#metric-" + definition.id);
  };

  return (
    <div className="monitor">
      <Link href="/admin/max" className="monitor-back"><ArrowLeft aria-hidden="true" />Админ-центр</Link>
      <h1>Мониторинг</h1>
      <div className="monitor-filters" aria-label="Фильтры мониторинга">
        <label>Период
          <select value={scope.period} onChange={(event) => setScope({ ...scope, period: event.target.value as MonitorPeriod })}>
            <option value="24h">24 часа</option><option value="7d">7 дней</option><option value="30d">30 дней</option>
          </select>
        </label>
        <ScopeSelect label="Клиент" options={filterOptions?.clients ?? []} value={scope.client} disabled={!filterOptions}
          onChange={(client) => setScope({ ...scope, client, project: "", vps: "" })} />
        <ScopeSelect label="VPS" options={filterOptions?.servers.filter((vps) => !scope.client || vps.client_id === scope.client) ?? []}
          value={scope.vps} disabled={!filterOptions}
          onChange={(vps) => setScope({ ...scope, vps, project: "" })} />
        <ScopeSelect label="Приложение" options={filterOptions?.projects.filter((project) => !scope.client || project.client_id === scope.client) ?? []}
          value={scope.project} disabled={!filterOptions}
          onChange={(project) => setScope({ ...scope, project, vps: "" })} />
      </div>
      {filters.isError && <p className="monitor-status" role="alert">Список клиентов и приложений недоступен. <button type="button" onClick={() => void filters.refetch()}>Повторить</button></p>}
      {catalog.isPending && <p className="monitor-status" role="status">Загружаем каталог метрик…</p>}
      {catalog.isError && <p className="monitor-status" role="alert">Каталог метрик недоступен. <button type="button" onClick={() => void catalog.refetch()}>Повторить</button></p>}
      {catalog.isSuccess && <>
        <Overview metrics={metrics} scope={scope} onSelect={setMetric} />
        <section aria-labelledby="catalog-heading">
          <div className="monitor-catalog-heading">
            <h3 id="catalog-heading">Каталог метрик</h3>
            <span>{visible.length} из {metrics.length}</span>
          </div>
          <div className="monitor-catalog-controls">
            <label className="monitor-search"><Search aria-hidden="true" />
              <input type="search" aria-label="Поиск метрики" placeholder="Номер или название метрики" value={search}
                onChange={(event) => { setSearch(event.target.value); setGroup("all"); }} />
            </label>
            <label className="monitor-group">Раздел
              <select value={group} onChange={(event) => setGroup(event.target.value as MetricGroup | "all")}>
                <option value="all">Все разделы</option>
                {Object.entries(GROUPS).map(([key, title]) => <option key={key} value={key}>{title}</option>)}
              </select>
            </label>
          </div>
          {metric ? <div className="monitor-workspace">
            <nav className="monitor-catalog" aria-label="Найденные метрики">
              {visible.map((item) => (
                <a href={"#metric-" + item.id} key={item.id} aria-current={item.id === metric.id ? "true" : undefined}
                  onClick={(event) => { event.preventDefault(); setSelectedId(item.id); window.history.replaceState(null, "", "#metric-" + item.id); }}>
                  <span>{item.id}</span><strong>{item.title}</strong><ChevronRight aria-hidden="true" />
                </a>
              ))}
            </nav>
            <MetricDetails key={metric.id + "/" + Object.values(scope).join("/")} metric={metric} scope={scope} />
          </div> : <p className="monitor-status" role="status">По этому запросу метрик нет.</p>}
        </section>
      </>}
    </div>
  );
}
