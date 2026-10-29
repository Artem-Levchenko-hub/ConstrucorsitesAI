"use client";

import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ApiError } from "@/lib/api/client";
import { getMaxOwnerRecords, ownerMoney, ownerRequestError, updateMaxOwnerRecord, type OwnerRecord, type OwnerRecordUpdate, type OwnerStatus, type ResourceDefinition } from "@/lib/api/max-management";

type Filters = { query: string; status: string; from: string; to: string };
type Draft = { record: OwnerRecord; status: OwnerStatus; note: string; operation?: OwnerRecordUpdate; error?: unknown; latest?: OwnerRecord; reloadMessage?: string };
const initialFilters: Filters = { query: "", status: "", from: "", to: "" };
const dateLabel = (value: string) => new Date(value).toLocaleString("ru-RU");
function dirty(draft: Draft | null): boolean { return Boolean(draft && (draft.status !== draft.record.status || draft.note !== (draft.record.note ?? ""))); }
function ambiguous(error: unknown): boolean {
  // A response body can fail after the server committed, outside fetch's ApiError normalization.
  // Only a recognized client-error response establishes a definite rejection.
  return error != null && (!(error instanceof ApiError) || error.status === 0 || error.status >= 500);
}
function conflict(error: unknown): boolean { return error instanceof ApiError && String(error.code) === "revision_conflict"; }
function dayBound(value: string, end = false): string | undefined {
  if (!value) return undefined;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) throw new Error("Укажите корректную дату.");
  const [year, month, day] = value.split("-").map(Number);
  const date = new Date(year, month - 1, day);
  if (date.getFullYear() !== year || date.getMonth() !== month - 1 || date.getDate() !== day) throw new Error("Укажите корректную дату.");
  if (end) date.setHours(23, 59, 59, 999);
  return date.toISOString();
}

export function MaxOwnerRecords(props: { projectId: string; resource: ResourceDefinition; onDirtyChange?: (dirty: boolean) => void }) {
  return <Records key={`${props.projectId}:${props.resource.id}`} {...props} />;
}

function Records({ projectId, resource, onDirtyChange }: { projectId: string; resource: ResourceDefinition; onDirtyChange?: (dirty: boolean) => void }) {
  const [filters, setFilters] = useState<Filters>(initialFilters);
  const [cursors, setCursors] = useState<Array<string | undefined>>([undefined]);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [pending, setPending] = useState(false);
  const [saved, setSaved] = useState(false);
  const saving = useRef(false);
  const mounted = useRef(true);
  const unsaved = dirty(draft);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => { onDirtyChange?.(unsaved || pending); return () => onDirtyChange?.(false); }, [onDirtyChange, unsaved, pending]);
  useEffect(() => {
    if (!unsaved) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [unsaved]);
  let createdFrom: string | undefined; let createdTo: string | undefined; let dateError = "";
  try {
    createdFrom = dayBound(filters.from); createdTo = dayBound(filters.to, true);
    if (createdFrom && createdTo && createdFrom > createdTo) dateError = "Дата начала не может быть позже даты окончания.";
  } catch (error) { dateError = (error as Error).message; }
  const query = { resource: resource.id, limit: 25, cursor: cursors.at(-1), query: filters.query, status: (filters.status || undefined) as OwnerStatus | undefined, ...(resource.id === "bookings" ? { requested_from: createdFrom, requested_to: createdTo } : { created_from: createdFrom, created_to: createdTo }) };
  const records = useQuery({
    queryKey: ["max-owner-records", projectId, query], queryFn: ({ signal }) => getMaxOwnerRecords(projectId, query, signal),
    retry: false, enabled: !dateError && !pending, refetchOnWindowFocus: false,
  });

  function mayLeave() { return !saving.current && (!unsaved || window.confirm("Есть несохранённые правки. Отменить их и перейти дальше?")); }
  function select(record: OwnerRecord) {
    if (record.id === draft?.record.id || !mayLeave()) return;
    setDraft({ record, status: record.status, note: record.note ?? "" }); setSaved(false);
  }
  function changeFilter(patch: Partial<Filters>) {
    if (!mayLeave()) return;
    setDraft(null); setSaved(false); setFilters(previous => ({ ...previous, ...patch })); setCursors([undefined]);
  }
  function page(next: boolean) {
    if (!mayLeave()) return;
    setDraft(null); setSaved(false);
    setCursors(previous => next ? [...previous, records.data?.next_cursor ?? undefined] : previous.slice(0, -1));
  }
  function edit(patch: Partial<Pick<Draft, "note" | "status">>) {
    if (saving.current || ambiguous(draft?.error)) return;
    setSaved(false);
    setDraft(previous => previous ? { ...previous, ...patch, operation: previous.error && !ambiguous(previous.error) ? undefined : previous.operation, error: undefined, latest: undefined, reloadMessage: undefined } : previous);
  }
  async function save() {
    if (!draft || saving.current || !dirty(draft) || draft.note.length > 2000) return;
    const operation: OwnerRecordUpdate = draft.operation ?? {
      expected_revision: draft.record.revision, operation_key: crypto.randomUUID(),
      ...(draft.status !== draft.record.status ? { status: draft.status } : {}),
      ...(draft.note !== (draft.record.note ?? "") ? { note: draft.note } : {}),
    };
    saving.current = true; setPending(true); setSaved(false); setDraft({ ...draft, operation, error: undefined });
    try {
      const record = await updateMaxOwnerRecord(projectId, resource.id, draft.record.id, operation);
      if (!mounted.current) return;
      setDraft({ record, status: record.status, note: record.note ?? "" }); setSaved(true);
      void records.refetch();
    } catch (error) {
      if (mounted.current) setDraft(previous => previous ? { ...previous, error } : previous);
    } finally { saving.current = false; if (mounted.current) setPending(false); }
  }
  async function reload() {
    const response = await records.refetch();
    if (!mounted.current) return;
    setDraft(previous => {
      if (!previous) return previous;
      const latest = response.data?.records.find(record => record.id === previous.record.id);
      return { ...previous, latest, reloadMessage: response.error ? ownerRequestError(response.error) : latest ? "Актуальная версия загружена. Ваш черновик сохранён. Проверьте его перед применением." : "Обращение не найдено в текущей выборке. Ваш черновик сохранён; вернитесь к списку и проверьте фильтры." };
    });
  }
  const locked = pending || ambiguous(draft?.error);
  return <section className="max-owner-records" aria-label={resource.label}>
    <div className="max-owner-section-heading"><h2>{resource.label}</h2><p>Рабочие обращения опубликованного приложения. Данные предпросмотра сюда не входят.</p></div>
    <div className="max-owner-filters">
      <label>Поиск<Input aria-label="Поиск обращений" maxLength={120} value={filters.query} disabled={pending} placeholder="Имя или контакт клиента" onChange={event => changeFilter({ query: event.target.value })} /></label>
      <label>Статус<select aria-label="Фильтр статуса" value={filters.status} disabled={pending} onChange={event => changeFilter({ status: event.target.value })}><option value="">Все статусы</option>{Object.entries(resource.status_labels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <label>{resource.id === "bookings" ? "Дата записи с" : "Дата создания с"}<Input aria-label={resource.id === "bookings" ? "Дата записи с" : "С даты"} type="date" value={filters.from} disabled={pending} onChange={event => changeFilter({ from: event.target.value })} /></label>
      <label>{resource.id === "bookings" ? "Дата записи по" : "Дата создания по"}<Input aria-label={resource.id === "bookings" ? "Дата записи по" : "По дату"} type="date" value={filters.to} disabled={pending} onChange={event => changeFilter({ to: event.target.value })} /></label>
    </div>
    {dateError && <p role="alert" className="max-owner-error">{dateError}</p>}
    {!dateError && <div className="max-owner-record-layout">
      <div>
        {records.isPending && <p role="status">{records.fetchStatus === "paused" ? "Нет соединения. Подключитесь к интернету, чтобы загрузить обращения." : "Загружаем обращения…"}</p>}
        {records.isError && <div role="alert" className="max-owner-notice"><p>{ownerRequestError(records.error)}</p><Button variant="outline" onClick={() => void records.refetch()}>Повторить загрузку</Button></div>}
        {records.isSuccess && !records.data.records.length && <p className="max-owner-notice">{filters.query || filters.status || filters.from || filters.to ? "По этим условиям ничего не найдено. Измените фильтры." : "Обращений пока нет. Здесь появятся обращения клиентов после отправки через опубликованное приложение."}</p>}
        <div className="max-owner-record-list">{records.data?.records.map(record => <article key={record.id} className="max-owner-record-row" data-selected={draft?.record.id === record.id}>
          <Button variant="ghost" disabled={pending} onClick={() => select(record)}>{record.customer_name}</Button>
          <span>{resource.status_labels[record.status]}</span><time dateTime={resource.id === "bookings" ? record.requested_for ?? record.created_at : record.created_at}>{dateLabel(resource.id === "bookings" ? record.requested_for ?? record.created_at : record.created_at)}</time>
          {resource.id === "bookings" && <span>{record.service_title}</span>}
          {record.total_kopecks != null && <strong>{ownerMoney(record.total_kopecks)}</strong>}
        </article>)}</div>
        <div className="max-owner-actions"><Button variant="outline" disabled={cursors.length === 1 || pending || records.isFetching} onClick={() => page(false)}>Предыдущая страница</Button><span>Страница {cursors.length}</span><Button variant="outline" disabled={!records.data?.next_cursor || pending || records.isFetching} onClick={() => page(true)}>Следующая страница</Button></div>
      </div>
      {draft && <article className="max-owner-record-card" aria-label={`${resource.singular_label}: ${draft.record.customer_name}`}>
        <h3>{resource.singular_label}</h3><p className="max-owner-hint">Сведения клиента и состав сохранены на момент обращения.</p>
        <dl>{resource.fields.filter(field => field.readonly && field.key !== "items").map(field => {
          const value = draft.record[field.key];
          if (value == null || value === "") return null;
          return <div key={field.key}><dt>{field.label}</dt><dd>{field.kind === "date" ? dateLabel(String(value)) : field.kind === "money" ? ownerMoney(Number(value)) : String(value)}</dd></div>;
        })}</dl>
        {draft.record.items.length > 0 && <div><h4>Состав заказа</h4><ul>{draft.record.items.map((item, index) => <li key={`${item.catalog_id}:${index}`}><strong>{item.title}</strong>{item.option && <span>{item.option}</span>}<span>{item.quantity.toLocaleString("ru-RU")} × {ownerMoney(item.unit_price_kopecks)}</span></li>)}</ul></div>}
        <form onSubmit={event => { event.preventDefault(); void save(); }}>
          <label>Статус<select aria-label="Статус обращения" disabled={locked} value={draft.status} onChange={event => edit({ status: event.target.value as OwnerStatus })}>{[draft.record.status, ...(resource.transitions[draft.record.status] ?? [])].map(status => <option key={status} value={status}>{resource.status_labels[status]}</option>)}</select></label>
          <label>Заметка владельца<textarea aria-label="Заметка владельца" maxLength={2000} rows={5} value={draft.note} disabled={locked} onChange={event => edit({ note: event.target.value })} /></label><p className="max-owner-hint">Видна только владельцу. До 2000 символов.</p>
          {draft.error ? <div role="alert" className="max-owner-error"><p>{conflict(draft.error) ? "Обращение изменилось в другой вкладке. Ваш черновик остался в форме. Загрузите актуальную версию и проверьте правки." : ownerRequestError(draft.error)}</p>{ambiguous(draft.error) && <p>Неизвестно, сохранились ли изменения. Повторите эту же попытку, чтобы проверить результат.</p>}{conflict(draft.error) && <Button type="button" variant="outline" disabled={records.isFetching} onClick={() => void reload()}>Загрузить актуальную версию</Button>}</div> : null}
          {draft.reloadMessage && <p role="status" className="max-owner-notice">{draft.reloadMessage}</p>}
          {draft.latest && <div className="max-owner-notice"><p>Текущий статус: {resource.status_labels[draft.latest.status]}. Заметка: {draft.latest.note || "Нет заметки"}</p><Button type="button" variant="outline" onClick={() => setDraft(previous => previous?.latest ? { ...previous, record: previous.latest, status: [previous.latest.status, ...resource.transitions[previous.latest.status]].includes(previous.status) ? previous.status : previous.latest.status, operation: undefined, error: undefined, latest: undefined, reloadMessage: "Черновик перенесён к актуальной версии. Проверьте изменения и сохраните." } : previous)}>Применить к актуальной версии</Button></div>}
          {saved && <p role="status">Изменения сохранены</p>}
          {pending && <p role="status">Сохраняем изменения…</p>}
          <div className="max-owner-actions"><Button type="submit" aria-busy={pending} disabled={!unsaved || pending || draft.note.length > 2000}>Сохранить обращение</Button><Button type="button" variant="outline" disabled={pending || ambiguous(draft.error)} onClick={() => { setDraft({ record: draft.record, status: draft.record.status, note: draft.record.note ?? "" }); setSaved(false); }}>Отменить правки</Button></div>
        </form>
      </article>}
    </div>}
  </section>;
}
