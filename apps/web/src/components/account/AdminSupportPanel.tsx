"use client";

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Check, ChevronLeft, ChevronRight, Search } from "lucide-react";
import {
  listSupport,
  supportContextOptions,
  supportStatus,
  supportTiming,
  type SupportStatus,
  type SupportTicket,
} from "@/lib/api/support";
import { useSupportClock } from "@/hooks/useSupportClock";
import { SupportThread } from "@/components/support/SupportThread";
import "@/components/support/support.css";
import "./admin-support.css";

type AdminSupportPanelProps = {
  archived?: boolean;
  ticketId: string | null;
  onSelect: (id: string | null) => void;
};

type AdminSupportQueueProps = AdminSupportPanelProps & {
  scope: string;
  archived: boolean;
};

const statuses = Object.keys(supportStatus) as SupportStatus[];
const pageSize = 25;

export function AdminSupportPanel({ archived = false, ticketId, onSelect }: AdminSupportPanelProps) {
  const context = useQuery(supportContextOptions);
  // Keying by the verified principal prevents a previous account's conversation/draft reuse.
  if (!context.data) return <p role="status">Проверяем доступ…</p>;
  if (context.isError || !context.data.is_admin) return <p role="alert">Доступ к поддержке только у администратора.</p>;
  return <AdminSupportQueue key={`${context.data.user_id}:${archived}`} scope={context.data.user_id!} archived={archived} ticketId={ticketId} onSelect={onSelect} />;
}

function AdminSupportTicket({ ticket, now, onSelect }: {
  ticket: SupportTicket;
  now: number;
  onSelect: (id: string) => void;
}) {
  const waitingForReply = !ticket.first_responded_at && ticket.first_response_due_at;
  return <button type="button" className="admin-support-ticket" onClick={() => onSelect(ticket.id)}>
    <span className="admin-support-ticket-main">
      <span className="admin-support-ticket-heading">
        <span className="admin-support-reference">{ticket.reference}</span>
        <span className="admin-support-status-badge" data-status={ticket.status}>{supportStatus[ticket.status]}</span>
      </span>
      <strong className="admin-support-subject">{ticket.subject}</strong>
      <span className="admin-support-preview">{ticket.last_message_preview}</span>
    </span>
    <span className="admin-support-ticket-timing">
      <time className="admin-support-updated" dateTime={ticket.updated_at}>{new Date(ticket.updated_at).toLocaleString("ru-RU")}</time>
      <span className="admin-support-deadline" data-overdue={ticket.first_response_overdue || undefined}>{supportTiming(ticket, now)}</span>
      {waitingForReply && <span className="admin-support-waiting">Ожидание: {Math.max(0, Math.floor((now - new Date(ticket.created_at).getTime()) / 60_000))} мин</span>}
    </span>
    <span className="admin-support-ticket-footer">
      <span className="admin-support-requester">{ticket.requester_email}</span>
      <span className="admin-support-message-count">Сообщений: {ticket.message_count}</span>
      {ticket.assigned_admin_email && <span className="admin-support-assignee">{ticket.assigned_admin_email}</span>}
    </span>
    <ChevronRight className="admin-support-ticket-arrow" size={18} aria-hidden="true" />
  </button>;
}

function AdminSupportQueue({ scope, archived, ticketId, onSelect }: AdminSupportQueueProps) {
  const now = useSupportClock();
  const [status, setStatus] = useState<SupportStatus>("new");
  const [overdue, setOverdue] = useState(false);
  const [search, setSearch] = useState("");
  const [term, setTerm] = useState("");
  const [offset, setOffset] = useState(0);

  useEffect(() => {
    const timer = setTimeout(() => { setTerm(search.trim()); setOffset(0); }, 300);
    return () => clearTimeout(timer);
  }, [search]);

  const params = new URLSearchParams({ archived: String(archived), limit: String(pageSize), offset: String(offset), search: term });
  if (!archived && !overdue) params.set("status", status);
  if (overdue) params.set("overdue", "true");
  const query = useQuery({
    queryKey: ["support", scope, "admin-list", archived, status, overdue, term, offset],
    queryFn: ({ signal }) => listSupport(params.toString(), true, signal),
    retry: false,
    staleTime: 0,
    refetchInterval: 30_000,
  });

  return <div className="admin-support-panel">
    <header className="admin-support-heading">
      <h2>{archived ? "Архив поддержки" : "Поддержка"}</h2>
      <p>{archived ? "Завершённые более суток назад обращения. История переписки сохранена." : "Обращения пользователей и гостей. Ответы сохраняются в переписке и отправляются по email."}</p>
    </header>
    {ticketId ? <>
      <button type="button" className="admin-support-back" onClick={() => onSelect(null)}><ArrowLeft size={16} aria-hidden="true" />К списку обращений</button>
      <SupportThread key={ticketId} id={ticketId} scope={scope} admin />
    </> : <>
      <div className="admin-support-toolbar">
        {!archived && <div className="admin-support-status-filters" role="group" aria-label="Фильтр статуса">
          {statuses.map(value => <button key={value} type="button" aria-pressed={status === value} onClick={() => { setStatus(value); setOffset(0); }}>
            {supportStatus[value]}{value === "new" && query.data?.new_count ? <span className="admin-support-filter-count">{query.data.new_count}</span> : null}
          </button>)}
        </div>}
        <label className="admin-support-search">
          <Search size={17} aria-hidden="true" />
          <input aria-label="Поиск обращений" placeholder="Тема, номер или email" value={search} onChange={event => setSearch(event.target.value)} />
        </label>
        {!archived && <label className="admin-support-overdue" data-active={overdue}>
          <input type="checkbox" checked={overdue} onChange={event => { setOverdue(event.target.checked); setOffset(0); }} />
          <span className="admin-support-checkbox" aria-hidden="true">{overdue && <Check size={13} strokeWidth={3} />}</span>
          <span>Просрочен первый ответ</span>
        </label>}
      </div>
      {query.isPending && <p className="admin-support-state" role="status">Загружаем обращения…</p>}
      {query.isError && <p className="admin-support-state admin-support-state-error" role="alert">Не удалось загрузить обращения. <button type="button" onClick={() => void query.refetch()}>Повторить</button></p>}
      {query.data && <>
        <div className="admin-support-list-heading"><p>Всего: <strong>{query.data.total}</strong></p></div>
        <div className="admin-support-queue">
          {query.data.items.map(ticket => <AdminSupportTicket key={ticket.id} ticket={ticket} now={now} onSelect={onSelect} />)}
        </div>
        {query.data.items.length === 0 && <p className="admin-support-state">Обращений пока нет.</p>}
        <div className="admin-support-pagination">
          <span>{query.data.total ? offset + 1 : 0}–{Math.min(offset + pageSize, query.data.total)} из {query.data.total}</span>
          <div>
            <button type="button" disabled={offset === 0} onClick={() => setOffset(value => Math.max(0, value - pageSize))}><ChevronLeft size={16} aria-hidden="true" />Назад</button>
            <button type="button" disabled={offset + pageSize >= query.data.total} onClick={() => setOffset(value => value + pageSize)}>Далее<ChevronRight size={16} aria-hidden="true" /></button>
          </div>
        </div>
      </>}
    </>}
  </div>;
}
