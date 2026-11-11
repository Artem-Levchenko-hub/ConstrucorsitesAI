"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Clock3, Inbox, RefreshCw, ShoppingBag } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api/client";
import {
  getMaxTeam, getStaffAccess, getStaffRecords, staffAccessDenied, staffRequestError, updateStaffRecord,
  type StaffAccess, type StaffAction, type StaffPermission, type StaffRecord, type StaffRecordUpdate,
  type StaffRecordsPage, type TeamMember,
} from "@/lib/api/max-staff";
import { maxStaffEnabled, maxStaffProjectPath } from "@/lib/max-staff-path";
import { MaxStaffRecord, staffRecordTitle, staffStatus, staffTime } from "./MaxStaffRecord";
import "./max-staff.css";

const refreshMs = 15_000;
const automaticPageLimit = 3;
const orderFilters = [
  ["now", "Готовить сейчас"], ["ready", "Готовы"], ["later", "На потом"], ["mine", "Мои"], ["test", "Проверка"], ["closed", "Завершённые"],
] as const;
const leadFilters = [["now", "Открытые"], ["mine", "Мои"], ["closed", "Закрытые"]] as const;
type Filter = (typeof orderFilters)[number][0];
type Attempt = { id: string; update: StaffRecordUpdate };
const emptyCopy: Record<Filter, string> = {
  now: "Сейчас очередь пуста", ready: "Готовых заказов пока нет", later: "На потом заказов нет",
  mine: "За вами пока нет открытых записей", test: "Тестовых заказов пока нет", closed: "Завершённых записей пока нет",
};

function closed(record: StaffRecord) {
  return record.status === "completed" || record.status === "cancelled";
}

function preparingLater(record: StaffRecord, now: number) {
  return record.resource === "orders" && record.status === "new" && record.prepare_at !== null && Date.parse(record.prepare_at) > now;
}

function inFilter(record: StaffRecord, filter: Filter, actorId: string, now: number): boolean {
  const test = record.resource === "orders" && record.payment === "test";
  if (filter === "test") return test;
  if (test) return false;
  if (filter === "closed") return closed(record);
  if (closed(record)) return false;
  if (filter === "mine") return record.assignee_id === actorId;
  if (filter === "ready") return record.status === "ready";
  if (filter === "later") return preparingLater(record, now);
  return record.status !== "ready" && !preparingLater(record, now);
}

function priority(record: StaffRecord) {
  return Date.parse(record.resource === "orders" ? record.scheduled_for ?? record.created_at : record.created_at);
}

export function MaxStaffQueue({ projectId, userId, resource, members, onDirtyChange }: {
  projectId: string; userId?: string; resource?: StaffPermission; members?: TeamMember[]; onDirtyChange?: (dirty: boolean) => void;
}) {
  const [selectedResource, setResource] = useState<StaffPermission | null>(null);
  const [boardBusy, setBoardBusy] = useState(false);
  const access = useQuery({
    queryKey: ["max-staff-access", projectId, userId ?? "owner"], queryFn: ({ signal }) => getStaffAccess(projectId, signal),
    retry: false, staleTime: 0, gcTime: 0, refetchOnWindowFocus: "always", refetchInterval: refreshMs,
  });
  const permissions = access.isSuccess ? access.data.permissions : [];
  const ownerTeam = useQuery({
    queryKey: ["max-team", projectId, access.data?.actor_id], queryFn: ({ signal }) => getMaxTeam(projectId, signal),
    enabled: !resource && access.data?.is_owner === true && access.isSuccess, retry: false, staleTime: 0, gcTime: 0,
  });
  useEffect(() => { onDirtyChange?.(boardBusy); return () => onDirtyChange?.(false); }, [boardBusy, onDirtyChange]);
  const selected = resource ?? (selectedResource && permissions.includes(selectedResource) ? selectedResource : permissions[0]);
  const allowed = selected && permissions.includes(selected);
  const managePath = `/max/${encodeURIComponent(projectId)}/manage`;
  const loginReturn = maxStaffEnabled ? maxStaffProjectPath(projectId) : managePath;
  return <section className="max-staff-queue" aria-label="Рабочая очередь">
    {!resource && <header className="max-staff-page-heading">
      <p className="max-staff-kicker">Рабочий кабинет</p>
      <h1>{access.isSuccess ? access.data.project_name : "Приложение"}</h1>
      <p>Заказы и заявки, доступные вашей команде.</p>
    </header>}
    {access.isPending && <p role="status" className="max-staff-notice">Проверяем доступ к очереди…</p>}
    {access.isError && <div role="alert" className="max-staff-notice" data-tone="error"><p>{staffRequestError(access.error)}</p>
      <div className="max-staff-actions"><Button variant="outline" onClick={() => void access.refetch()}>Проверить доступ</Button>
        {access.error instanceof ApiError && access.error.status === 401 && <Button asChild variant="ghost"><Link href={`/login?next=${encodeURIComponent(loginReturn)}`}>Войти</Link></Button>}
      </div>
    </div>}
    {access.isSuccess && !allowed && <div className="max-staff-empty"><Inbox aria-hidden="true" />
      <h2>Рабочая очередь пока недоступна</h2><p>{access.data.is_owner
        ? `Включите нужную очередь в разделе «${maxStaffEnabled ? "Команда" : "Настройки очередей"}» и опубликуйте совместимую версию приложения.`
        : "Обратитесь к владельцу приложения, чтобы проверить доступ к заказам или заявкам."}</p>
      {maxStaffEnabled && access.data.is_owner && !resource && <Button asChild variant="outline"><Link href={`${managePath}?section=team`}>Открыть команду</Link></Button>}
    </div>}
    {!resource && permissions.length > 1 && <nav className="max-staff-resource" aria-label="Рабочие разделы">
      {permissions.map(value => <Button key={value} type="button" variant="ghost" aria-current={value === selected ? "page" : undefined}
        disabled={boardBusy} onClick={() => setResource(value)}>{value === "orders" ? <ShoppingBag aria-hidden="true" /> : <Inbox aria-hidden="true" />}
        {value === "orders" ? "Заказы" : "Заявки"}</Button>)}
    </nav>}
    {access.isSuccess && allowed && <StaffBoard key={`${access.data.actor_id}:${selected}`} access={access.data} resource={selected}
      members={access.data.is_owner ? members ?? ownerTeam.data?.members ?? [] : []} onBusyChange={setBoardBusy}
      onAccessChange={() => { void access.refetch(); }} />}
  </section>;
}

function StaffBoard({ access, resource, members, onBusyChange, onAccessChange }: {
  access: StaffAccess; resource: StaffPermission; members: TeamMember[]; onBusyChange: (value: boolean) => void; onAccessChange: () => void;
}) {
  const { project_id: projectId, actor_id: actorId } = access;
  const cache = useQueryClient();
  const [filter, setFilter] = useState<Filter>("now");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [attempt, setAttempt] = useState<Attempt | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [writeDenied, setWriteDenied] = useState(false);
  const busy = useRef(false);
  const selectedButton = useRef<HTMLButtonElement | null>(null);
  const filterGroup = useRef<HTMLDivElement>(null);
  const boardHeading = useRef<HTMLHeadingElement>(null);
  const locked = pending || Boolean(attempt);
  useEffect(() => { onBusyChange(locked); return () => onBusyChange(false); }, [locked, onBusyChange]);
  const queryKey = ["max-staff-records", projectId, actorId, resource] as const;
  const queue = useInfiniteQuery({
    queryKey, queryFn: ({ pageParam, signal }) => getStaffRecords(projectId, resource, pageParam, signal),
    initialPageParam: undefined as string | undefined, getNextPageParam: page => page.next_cursor || undefined,
    retry: false, staleTime: 0, gcTime: 0,
    refetchInterval: query => !query.state.error && (query.state.data?.pages.length ?? 0) <= automaticPageLimit ? refreshMs : false,
    refetchOnWindowFocus: query => !query.state.error && (query.state.data?.pages.length ?? 0) <= automaticPageLimit ? "always" : false,
    refetchOnReconnect: query => (query.state.data?.pages.length ?? 0) <= automaticPageLimit,
  });
  const pages = queue.data?.pages ?? [];
  const rows = [...new Map(pages.flatMap(page => page.records.map(record => [record.id, record] as const))).values()];
  const serverTime = Date.parse(pages.at(-1)?.server_time ?? "");
  const [clock, setClock] = useState<{ base: number; elapsed: number } | null>(null);
  useEffect(() => {
    if (resource !== "orders" || !Number.isFinite(serverTime)) return;
    const receivedAt = performance.now();
    const timer = window.setInterval(() => setClock({ base: serverTime, elapsed: performance.now() - receivedAt }), 1_000);
    return () => window.clearInterval(timer);
  }, [resource, serverTime]);
  const now = serverTime + (clock?.base === serverTime ? clock.elapsed : 0);
  const selected = rows.find(record => record.id === selectedId);
  const filters = resource === "orders" ? orderFilters : leadFilters;
  const visible = rows.filter(record => inFilter(record, filter, actorId, now))
    .sort((left, right) => filter === "closed" ? priority(right) - priority(left) : priority(left) - priority(right));
  const { fetchNextPage, hasNextPage, isFetching, isFetchNextPageError, isFetchingNextPage } = queue;
  const autoCursor = hasNextPage && pages.length < automaticPageLimit && !queue.isError && !locked ? pages.at(-1)?.next_cursor : null;
  useEffect(() => {
    if (autoCursor && !isFetching) void fetchNextPage({ cancelRefetch: false });
  }, [autoCursor, isFetching, fetchNextPage]);
  const loadingMore = isFetchingNextPage || Boolean(autoCursor);
  const failedNextPage = isFetchNextPageError && !isFetchingNextPage;
  const failedQueue = queue.isError && !isFetchNextPageError;
  const manuallyRefreshed = pages.length > automaticPageLimit;

  async function send(next: Attempt) {
    if (busy.current) return;
    busy.current = true; setPending(true); setError(null);
    try {
      const record = await updateStaffRecord(projectId, next.id, next.update);
      cache.setQueryData<{ pages: StaffRecordsPage[]; pageParams: unknown[] }>(queryKey, current => current && ({
        ...current, pages: current.pages.map(page => ({ ...page, records: page.records.map(item => item.id === record.id ? record : item) })),
      }));
      setAttempt(null);
      await Promise.all([
        cache.invalidateQueries({ queryKey }),
        cache.invalidateQueries({ queryKey: ["max-staff-history", projectId, actorId, resource, record.id] }),
      ]);
    } catch (failure) {
      setError(failure);
      if (failure instanceof ApiError && (failure.status === 401 || failure.status === 403)) {
        setAttempt(null); setWriteDenied(true); onAccessChange();
      } else if (failure instanceof ApiError && (failure.status === 409 || failure.status === 404)) {
        setAttempt(null); await queue.refetch();
      } else {
        setAttempt(!(failure instanceof ApiError) || failure.status === 0 || failure.status >= 500 ? next : null);
      }
    } finally {
      busy.current = false; setPending(false);
    }
  }

  function actOnRecord(action: StaffAction, details?: { outcome?: string; assignee_id?: string | null }) {
    if (!selected || attempt || failedQueue) return;
    void send({ id: selected.id, update: { resource, action, expected_revision: selected.revision, operation_key: crypto.randomUUID(), ...details } });
  }

  const denied = writeDenied || staffAccessDenied(queue.error);
  if (denied) return <div role="alert" className="max-staff-notice" data-tone="error">
    <p>Доступ к этой очереди закрыт.</p>
    <Button variant="outline" onClick={async () => {
      onAccessChange();
      const result = await queue.refetch();
      if (result.isSuccess) { setWriteDenied(false); setError(null); setSelectedId(null); }
    }}>Проверить доступ</Button>
  </div>;

  function backToQueue() {
    if (locked) return;
    setSelectedId(null); setError(null);
    requestAnimationFrame(() => {
      const previous = selectedButton.current;
      const fallback = filterGroup.current?.querySelector<HTMLButtonElement>('button[aria-pressed="true"]') ?? boardHeading.current;
      (previous?.isConnected ? previous : fallback)?.focus();
    });
  }

  return <>
    <div className="max-staff-board-heading"><h2 ref={boardHeading} tabIndex={-1}>{resource === "orders" ? "Заказы" : "Заявки"}</h2>
      <Button type="button" variant="outline" disabled={queue.isFetching || pending} onClick={() => void queue.refetch()}><RefreshCw aria-hidden="true" /> Обновить</Button>
    </div>
    <div className="max-staff-board" data-detail-open={Boolean(selected)}>
      <div className="max-staff-list">
        <div ref={filterGroup} className="max-staff-filters" role="group" aria-label="Фильтр очереди">
          {filters.map(([value, label]) => <Button key={value} type="button" variant="ghost" aria-pressed={value === filter}
            disabled={pending || Boolean(attempt)} onClick={() => { setFilter(value); setSelectedId(null); setError(null); }}>{label}</Button>)}
        </div>
        {queue.isPending && <p role="status" className="max-staff-notice">Загружаем очередь…</p>}
        {failedQueue && <div role="alert" className="max-staff-notice" data-tone="error"><p>{staffRequestError(queue.error)}</p>
          <Button variant="outline" onClick={() => void queue.refetch()}>Повторить загрузку</Button>
        </div>}
        {loadingMore && <p role="status" className="max-staff-notice">Проверяем следующие страницы…</p>}
        {failedNextPage && <div role="alert" className="max-staff-notice" data-tone="error">
          <p>Следующая страница не загрузилась. {staffRequestError(queue.error)}</p>
          <Button type="button" variant="outline" disabled={isFetching} onClick={() => void fetchNextPage({ cancelRefetch: false })}>Повторить загрузку страницы</Button>
        </div>}
        {((hasNextPage && !loadingMore && !failedNextPage) || manuallyRefreshed) && <p role="status" className="max-staff-notice">
          {hasNextPage && !loadingMore && !failedNextPage && "Очередь проверена не полностью. Можно загрузить ещё записи. "}
          {manuallyRefreshed && "Чтобы получить свежие записи, нажмите «Обновить»."}
        </p>}
        {queue.isSuccess && !visible.length && !hasNextPage && <div className="max-staff-empty"><Inbox aria-hidden="true" /><h3>{emptyCopy[filter]}</h3>
          {!manuallyRefreshed && <p>Новые записи появятся здесь автоматически.</p>}
        </div>}
        {visible.length > 0 && <ul className="max-staff-rows" aria-label="Записи очереди">{visible.map(record => <li key={record.id}>
          <button type="button" aria-pressed={record.id === selectedId} disabled={locked}
            onClick={event => { selectedButton.current = event.currentTarget; setSelectedId(record.id); setError(null); }}>
            {record.resource === "orders" && <span className="max-staff-deadline" data-later={preparingLater(record, now)}>
              <Clock3 aria-hidden="true" />{record.scheduled_for ? staffTime(record.scheduled_for) : "Скорее"}
            </span>}
            <span className="max-staff-row-copy"><strong>{staffRecordTitle(record)}</strong>
              <small>{record.assignee_name || "Без ответственного"}</small></span>
            <span className="max-staff-status" data-status={record.status}>{staffStatus[record.status]}</span>
            <ArrowRight aria-hidden="true" className="max-staff-row-arrow" />
          </button>
        </li>)}</ul>}
        {hasNextPage && !loadingMore && !failedNextPage && <Button type="button" variant="outline" disabled={isFetching || locked}
          onClick={() => void fetchNextPage({ cancelRefetch: false })}>Загрузить ещё</Button>}
      </div>
      <div className="max-staff-detail">
        {error != null && <div role="alert" className="max-staff-notice" data-tone="error">
          <p>{attempt ? "Сервер не подтвердил результат. Повторите ту же попытку, чтобы уточнить статус." : staffRequestError(error)}</p>
          {attempt && <Button variant="outline" disabled={pending} onClick={() => void send(attempt)}>Повторить действие</Button>}
        </div>}
        {pending && <p role="status" className="max-staff-notice">Сохраняем действие…</p>}
        {selected ? <MaxStaffRecord key={selected.id} record={selected} access={access} members={members}
          locked={locked || failedQueue} claimAvailable={!preparingLater(selected, now)}
          onBack={backToQueue} onAction={actOnRecord} />
          : <div className="max-staff-empty max-staff-placeholder"><Inbox aria-hidden="true" /><h3>Выберите запись</h3><p>Здесь появятся детали и доступные действия.</p></div>}
      </div>
    </div>
  </>;
}
