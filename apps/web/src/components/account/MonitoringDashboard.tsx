"use client";

import { useQueries, useQuery, useQueryClient, type UseQueryResult } from "@tanstack/react-query";
import {
  Activity, ArrowLeft, LogOut, ArrowUpRight, ChevronRight, CircleAlert, Clock3, Database,
  Layers3, List, Radio, RefreshCw, Search, Server, Workflow,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useId, useState } from "react";

import {
  getMetric, getMetricCatalog, getMetricFilters,
  type MetricChoice, type MetricDefinition, type MetricGroup, type MetricPoint,
  type MetricResult, type MetricScope, type MonitorPeriod,
} from "@/lib/api/monitoring";
import { apiFetch } from "@/lib/api/client";
import { MONITORING_LOGIN_PATH, MONITORING_SESSION_API, type MonitoringRole } from "@/lib/monitoring-access";
import { appendSnapshots, CURRENT_METRICS, pointUnit } from "@/lib/monitoring-chart";
import { formatDate, formatNumber, MonitoringChart } from "./MonitoringChart";
import "./monitoring.css";

const GROUPS: Record<MetricGroup, string> = {
  infrastructure: "VPS и инциденты",
  activity: "Пользователи и приложения",
  generation: "Генерация и публикации",
  ai: "ИИ и расходы",
  platform: "API и интеграции",
};
const VIEWS = [
  ["overview", "Обзор", Activity],
  ["infrastructure", "VPS", Server],
  ["activity", "Активность", Layers3],
  ["generation", "Генерация", Workflow],
  ["ai", "ИИ и расходы", Radio],
  ["platform", "API и БД", Database],
  ["catalog", "Все метрики", List],
] as const;

type View = (typeof VIEWS)[number][0];
type Refresh = 0 | 15 | 30 | 60;
type MetricQuery = UseQueryResult<MetricResult, Error>;
const DEFAULT_SCOPE: MetricScope = { period: "24h", client: "", project: "", vps: "" };
const PANELS: Record<Exclude<View, "catalog">, { stats: number[]; charts: number[] }> = {
  overview: { stats: [1, 2, 43, 96], charts: [86, 33, 47, 61] },
  infrastructure: { stats: [1, 2, 20, 99], charts: [9, 11, 13, 16, 17, 100] },
  activity: { stats: [], charts: [25, 33, 32, 28, 30, 94] },
  generation: { stats: [43, 45, 46, 37], charts: [47, 44, 48, 49, 51, 36] },
  ai: { stats: [], charts: [86, 61, 62, 81, 66, 89] },
  platform: { stats: [96], charts: [91, 93, 92, 95, 97] },
};
const PERIODS: { value: MonitorPeriod; label: string; long: string }[] = [
  { value: "24h", label: "24 ч", long: "Последние 24 часа" },
  { value: "7d", label: "7 д", long: "Последние 7 дней" },
  { value: "30d", label: "30 д", long: "Последние 30 дней" },
];
const STATES: Record<MetricResult["state"], string> = {
  measured: "Есть данные",
  no_data: "Нет измерений",
  stale: "Данные устарели",
  source_unavailable: "Источник недоступен",
  not_applicable: "Нет такого разреза",
};
const REFRESH_TIME = new Intl.DateTimeFormat("ru-RU", {
  hour: "2-digit", minute: "2-digit", second: "2-digit", timeZone: "Asia/Tomsk",
});

function queryFor(metric: MetricDefinition, scope: MetricScope, refresh: Refresh) {
  return {
    queryKey: ["admin-monitoring", "reading", metric.id, scope],
    queryFn: ({ signal }: { signal: AbortSignal }) => getMetric(metric.id, scope, signal),
    enabled: metric.source !== null,
    staleTime: 10_000,
    refetchInterval: refresh ? refresh * 1000 : false as const,
    refetchOnWindowFocus: refresh !== 0,
    refetchOnReconnect: refresh !== 0,
    retry: false,
  };
}

function ScopeSelect({ label, options, value, onChange, disabled }: {
  label: string; options: MetricChoice[]; value: string; onChange: (id: string) => void; disabled: boolean;
}) {
  const id = useId();
  return (
    <div className="monitor-field">
      <label htmlFor={id}>{label}</label>
      <select id={id} value={value} onChange={(event) => onChange(event.target.value)} disabled={disabled}>
        <option value="">Все</option>
        {options.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}
      </select>
    </div>
  );
}

function statusOf(metric: MetricDefinition, query: MetricQuery) {
  if (!metric.source) return { state: "source_unavailable", label: "Сбор не настроен" } as const;
  if (query.isError) return { state: "error", label: "Ошибка запроса" } as const;
  if (!query.data) return { state: "loading", label: "Загрузка…" } as const;
  return { state: query.data.state, label: STATES[query.data.state] };
}

function ReadingStatus({ metric, query }: { metric: MetricDefinition; query: MetricQuery }) {
  const status = statusOf(metric, query);
  return <span className="monitor-reading-status" data-state={status.state}><i />{status.label}</span>;
}

function MetricValue({ metric, query, onSelect }: {
  metric: MetricDefinition; query: MetricQuery; onSelect: (id: number) => void;
}) {
  const points = query.data?.points ?? [];
  const seriesCount = new Set(points.map((point) => point.series)).size;
  const single = seriesCount === 1 ? points[0] : null;
  return (
    <button className="monitor-value" type="button" onClick={() => onSelect(metric.id)}>
      <span>{metric.title}<ArrowUpRight aria-hidden="true" size={15} /></span>
      <strong>{single ? formatNumber(single.value) : seriesCount ? formatNumber(seriesCount) : "—"}
        {single && <small>{pointUnit(metric.id, metric.unit, single.series)}</small>}
        {seriesCount > 1 && <small>рядов</small>}
      </strong>
      <ReadingStatus metric={metric} query={query} />
    </button>
  );
}

function MetricTable({ points, metric }: { points: MetricPoint[]; metric: MetricDefinition }) {
  const [page, setPage] = useState(0);
  const size = 15;
  const current = Math.min(page, Math.max(0, Math.ceil(points.length / size) - 1));
  const hasP95 = points.some((point) => point.p95 !== null);
  const hasSamples = points.some((point) => point.samples !== null);
  const hasMean = points.some((point) => point.mean != null);
  return (
    <>
      <div className="monitor-table" tabIndex={0} aria-label="Таблица измерений">
        <table>
          <thead><tr>
            <th scope="col">Ряд</th>
            <th scope="col">Время · Томск</th>
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
                <td>{point.at ? formatDate(point.at) : CURRENT_METRICS.has(metric.id) ? "Снимок" : "За период"}</td>
                <td>{formatNumber(point.value)} {pointUnit(metric.id, metric.unit, point.series)}</td>
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
        <button type="button" disabled={current === 0} onClick={() => setPage(current - 1)}>Назад</button>
        <button type="button" disabled={(current + 1) * size >= points.length} onClick={() => setPage(current + 1)}>Далее</button>
      </div>}
    </>
  );
}

function MetricPanel({ metric, query, detail, refresh, onSelect }: {
  metric: MetricDefinition; query: MetricQuery; detail?: boolean; refresh: Refresh; onSelect: (id: number) => void;
}) {
  const [history, setHistory] = useState<MetricPoint[]>([]);
  const [showHistory, setShowHistory] = useState(() => CURRENT_METRICS.has(metric.id));
  const result = query.data;
  const points = result?.points ?? [];
  const snapshots = detail && result?.state === "measured"
    ? appendSnapshots(history, result.points, result.measured_at)
    : history;
  if (snapshots !== history) setHistory(snapshots);
  const live = snapshots.length > 0 && showHistory;
  const chart = live ? snapshots : points;
  const status = statusOf(metric, query);

  return (
    <section className="monitor-panel" data-detail={detail || undefined} aria-label={metric.title}>
      <div className="monitor-panel-head">
        <div>
          {detail ? <h2 id="metric-heading">{metric.title}</h2> : <h3>{metric.title}</h3>}
          <p>{detail ? "№" + metric.id + " · " + (metric.source ?? "Сбор не настроен") : metric.source ?? "Сбор не настроен"}</p>
        </div>
        <div className="monitor-panel-actions">
          <ReadingStatus metric={metric} query={query} />
          {!detail && <button className="monitor-icon" type="button" onClick={() => onSelect(metric.id)} aria-label={"Открыть метрику " + metric.title}>
            <ArrowUpRight aria-hidden="true" size={17} />
          </button>}
        </div>
      </div>
      {detail && <p className="monitor-formula">{metric.formula}</p>}
      {detail && snapshots.length > 0 && (
        <div className="monitor-period monitor-chart-mode" role="group" aria-label="Вид графика">
          <button type="button" aria-pressed={!showHistory} onClick={() => setShowHistory(false)}>Разбивка</button>
          <button type="button" aria-pressed={showHistory} onClick={() => setShowHistory(true)}>Наблюдение</button>
        </div>
      )}
      {chart.length && status.state !== "source_unavailable" ? (
        <>
          {live && <small className="monitor-history-label">Снимки с момента открытия, до 1 часа</small>}
          <MonitoringChart points={chart} metric={metric} compact={!detail} expectedStep={live && refresh ? refresh * 1000 : undefined} />
        </>
      ) : (
        <div className="monitor-empty" data-state={status.state}>
          {status.state === "loading" ? <span className="monitor-skeleton" /> : <Activity aria-hidden="true" size={26} />}
          <strong>{status.label}</strong>
          {result?.message && <span>{result.message}</span>}
        </div>
      )}
      {detail && (
        <>
          <div className="monitor-panel-foot">
            <span>{metric.note}</span>
            {result && <span>Проверено {formatDate(result.measured_at)} · Томск</span>}
          </div>
          {points.length > 0 && <MetricTable points={points} metric={metric} />}
        </>
      )}
    </section>
  );
}

function MetricCatalog({ metrics, search, setSearch, onSelect }: {
  metrics: MetricDefinition[]; search: string; setSearch: (value: string) => void; onSelect: (id: number) => void;
}) {
  const [group, setGroup] = useState<MetricGroup | "all">("all");
  const needle = search.trim().replace(/^[№#]\s*/, "").toLocaleLowerCase("ru");
  const visible = metrics.filter((metric) => (
    (group === "all" || metric.group === group)
    && (!needle || String(metric.id) === needle || metric.title.toLocaleLowerCase("ru").includes(needle))
  ));

  return (
    <section className="monitor-catalog" aria-labelledby="monitor-catalog-heading">
      <div className="monitor-section-head"><h2 id="monitor-catalog-heading">Каталог метрик</h2><span>{visible.length} из {metrics.length}</span></div>
      <div className="monitor-catalog-controls">
        <label className="monitor-search"><Search aria-hidden="true" size={17} />
          <input type="search" aria-label="Поиск метрики" placeholder="Номер или название" value={search} onChange={(event) => setSearch(event.target.value)} />
        </label>
        <select aria-label="Раздел каталога" value={group} onChange={(event) => setGroup(event.target.value as MetricGroup | "all")}>
          <option value="all">Все разделы</option>
          {Object.entries(GROUPS).map(([key, title]) => <option key={key} value={key}>{title}</option>)}
        </select>
      </div>
      <div className="monitor-catalog-list">
        {visible.map((metric) => (
          <a href={"#metric-" + metric.id} key={metric.id} onClick={(event) => { event.preventDefault(); onSelect(metric.id); }}>
            <span>{String(metric.id).padStart(2, "0")}</span>
            <strong>{metric.title}</strong>
            <small>{GROUPS[metric.group]}</small>
            <ChevronRight size={16} aria-hidden="true" />
          </a>
        ))}
        {!visible.length && <p className="monitor-inline-state" role="status">По этому запросу метрик нет.</p>}
      </div>
    </section>
  );
}

function MonitoringSessionLinks({ role }: { role: MonitoringRole }) {
  const [pending, setPending] = useState(false);
  const [failed, setFailed] = useState(false);

  async function signOut() {
    setPending(true);
    setFailed(false);
    try {
      await apiFetch<void>(MONITORING_SESSION_API, { method: "DELETE", timeoutMs: 10000 });
      window.location.assign(MONITORING_LOGIN_PATH);
    } catch {
      setFailed(true);
      setPending(false);
    }
  }

  if (role === "admin") {
    return (
      <nav className="monitor-context" aria-label="Доступ к мониторингу">
        <Link href="/admin/max" className="monitor-back"><ArrowLeft aria-hidden="true" size={15} />Админ-центр</Link>
        <Link href={MONITORING_LOGIN_PATH} className="monitor-back">Вход наблюдателя</Link>
      </nav>
    );
  }
  return (
    <div className="monitor-context">
      <button className="monitor-back" type="button" disabled={pending} onClick={() => void signOut()}>
        <LogOut aria-hidden="true" size={15} />{pending ? "Выходим…" : "Выйти из мониторинга"}
      </button>
      {failed && <span role="alert" className="monitor-session-error">Не удалось выйти. Повторите попытку.</span>}
    </div>
  );
}

export function MonitoringDashboard({ role }: { role: MonitoringRole }) {
  const [scope, setScope] = useState<MetricScope>(DEFAULT_SCOPE);
  const [view, setView] = useState<View>("overview");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [search, setSearch] = useState("");
  const [refresh, setRefresh] = useState<Refresh>(30);
  const queryClient = useQueryClient();
  const catalog = useQuery({ queryKey: ["admin-monitoring", "catalog"], queryFn: ({ signal }) => getMetricCatalog(signal), staleTime: 300_000 });
  const filters = useQuery({ queryKey: ["admin-monitoring", "filters"], queryFn: ({ signal }) => getMetricFilters(signal), staleTime: 60_000 });
  const metrics = catalog.data ?? [];
  const filterOptions = filters.data;

  useEffect(() => {
    const read = () => {
      const match = /^#metric-(\d+)$/.exec(window.location.hash);
      setSelectedId(match ? Number(match[1]) : null);
    };
    read();
    window.addEventListener("hashchange", read);
    window.addEventListener("popstate", read);
    return () => {
      window.removeEventListener("hashchange", read);
      window.removeEventListener("popstate", read);
    };
  }, []);

  const selected = metrics.find((metric) => metric.id === selectedId);
  const panels = view === "catalog" ? { stats: [], charts: [] } : PANELS[view];
  const visibleIds = selectedId === null ? [...panels.stats, ...panels.charts] : [selectedId];
  const chosen = [...new Set(visibleIds)].flatMap((id) => {
    const metric = metrics.find((candidate) => candidate.id === id);
    return metric ? [metric] : [];
  });
  const queries = useQueries({ queries: chosen.map((metric) => queryFor(metric, scope, refresh)) });
  const visible = new Map(chosen.map((metric, index) => [metric.id, { metric, query: queries[index] }]));
  const updated = Math.max(0, ...queries.map((query) => query.dataUpdatedAt));
  const fetching = queries.some((query) => query.isFetching);
  const matching = queries.some((query) => query.data?.state === "measured");
  const cacheScope = Object.values(scope).join("/");
  const scroll = () => document.querySelector(".monitor-console")?.scrollTo({ top: 0 });

  function openMetric(id: number) {
    window.history.pushState(null, "", "#metric-" + id);
    setSelectedId(id);
    scroll();
  }

  function openView(next: View) {
    if (window.location.hash) window.history.pushState(null, "", window.location.pathname + window.location.search);
    setSelectedId(null);
    setView(next);
    scroll();
  }

  return (
    <main className="monitor-console">
      <div className="monitor-frame">
        <header className="monitor-header">
          <div>
            <MonitoringSessionLinks role={role} />
            <h1>Мониторинг</h1>
          </div>
          <div className="monitor-refresh">
            <span className="monitor-live" data-active={Boolean(refresh && matching)}><i />{refresh ? "Автообновление" : "Пауза"}</span>
            <label className="monitor-sr" htmlFor="monitor-refresh">Интервал автообновления</label>
            <select id="monitor-refresh" value={refresh} onChange={(event) => setRefresh(Number(event.target.value) as Refresh)}>
              <option value="15">15 с</option><option value="30">30 с</option><option value="60">60 с</option><option value="0">Пауза</option>
            </select>
            <button className="monitor-icon" type="button" title="Обновить" aria-label="Обновить данные" disabled={!queries.length || fetching}
              onClick={() => { void queryClient.refetchQueries({ queryKey: ["admin-monitoring", "reading"], type: "active" }); }}>
              <RefreshCw aria-hidden="true" size={17} data-fetching={fetching || undefined} />
            </button>
          </div>
        </header>

        <div className="monitor-toolbar" aria-label="Фильтры мониторинга">
          <div className="monitor-field"><span>Период</span>
            <div className="monitor-period" aria-label="Период" role="group">
              {PERIODS.map((period) => <button key={period.value} type="button" aria-label={period.long} aria-pressed={scope.period === period.value}
                onClick={() => setScope((current) => ({ ...current, period: period.value }))}>{period.label}</button>)}
            </div>
          </div>
          <ScopeSelect label="Клиент" options={filterOptions?.clients ?? []} value={scope.client} disabled={!filterOptions}
            onChange={(client) => setScope((current) => ({ ...current, client, project: "", vps: "" }))} />
          <ScopeSelect label="VPS" options={filterOptions?.servers.filter((entry) => !scope.client || entry.client_id === scope.client) ?? []}
            value={scope.vps} disabled={!filterOptions} onChange={(vps) => setScope((current) => ({ ...current, vps, project: "" }))} />
          <ScopeSelect label="Приложение" options={filterOptions?.projects.filter((entry) => !scope.client || entry.client_id === scope.client) ?? []}
            value={scope.project} disabled={!filterOptions} onChange={(project) => setScope((current) => ({ ...current, project, vps: "" }))} />
          <div className="monitor-last"><Clock3 size={15} aria-hidden="true" />
            <span>{updated ? REFRESH_TIME.format(updated) + " · Томск" : "Ожидание данных"}</span>
          </div>
        </div>

        <nav className="monitor-nav" aria-label="Разделы мониторинга">
          {VIEWS.map(([value, label, Icon]) => (
            <button key={value} type="button" aria-pressed={!selected && view === value} onClick={() => openView(value)}>
              <Icon size={16} aria-hidden="true" />{label}
            </button>
          ))}
        </nav>

        {filters.isError && <p className="monitor-inline-state" role="alert"><CircleAlert size={16} aria-hidden="true" />Фильтры недоступны. <button type="button" onClick={() => void filters.refetch()}>Повторить</button></p>}
        {catalog.isError && <p className="monitor-inline-state" role="alert"><CircleAlert size={16} aria-hidden="true" />Каталог недоступен. <button type="button" onClick={() => void catalog.refetch()}>Повторить</button></p>}
        {catalog.isPending && <div className="monitor-empty" role="status"><span className="monitor-skeleton" />Загружаем метрики…</div>}

        {catalog.isSuccess && (
          selectedId !== null ? (
            <div className="monitor-details">
              <button className="monitor-back" type="button" onClick={() => openView(view)}><ArrowLeft size={15} aria-hidden="true" />{view === "catalog" ? "К каталогу" : "К разделу"}</button>
              {selected && visible.has(selected.id) ? (
                <MetricPanel key={selected.id + cacheScope} metric={selected} query={visible.get(selected.id)!.query} refresh={refresh} onSelect={openMetric} detail />
              ) : <p className="monitor-inline-state" role="status">Метрика №{selectedId} отсутствует в каталоге.</p>}
            </div>
          ) : view === "catalog" ? <MetricCatalog metrics={metrics} search={search} setSearch={setSearch} onSelect={openMetric} /> : (
            <>
              {panels.stats.length > 0 && <section className="monitor-values" aria-label="Основные показатели">
                {panels.stats.map((id) => {
                  const entry = visible.get(id);
                  return entry && <MetricValue key={id} {...entry} onSelect={openMetric} />;
                })}
              </section>}
              <section aria-labelledby="monitor-charts-heading">
                <div className="monitor-section-head">
                  <h2 id="monitor-charts-heading">{view === "overview" ? "Сводка платформы" : GROUPS[view]}</h2>
                  <span>{PERIODS.find((period) => period.value === scope.period)?.long}</span>
                </div>
                <div className="monitor-grid">
                  {panels.charts.map((id) => {
                    const entry = visible.get(id);
                    return entry && <MetricPanel key={id + cacheScope} {...entry} refresh={refresh} onSelect={openMetric} />;
                  })}
                </div>
              </section>
            </>
          )
        )}
      </div>
    </main>
  );
}
