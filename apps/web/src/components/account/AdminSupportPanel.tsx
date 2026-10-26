"use client";
import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { listSupport, supportContextOptions, supportStatus, type SupportStatus } from "@/lib/api/support";
import { SupportThread } from "@/components/support/SupportThread";
import "@/components/support/support.css";

export function AdminSupportPanel({ archived = false, ticketId, onSelect }: { archived?: boolean; ticketId: string | null; onSelect: (id: string | null) => void }) {
  const context = useQuery(supportContextOptions);
  // Keying by the verified principal prevents a previous account's conversation/draft reuse.
  if (!context.data) return <p role="status">Проверяем доступ…</p>;
  if (context.isError || !context.data.is_admin) return <p role="alert">Доступ к поддержке только у администратора.</p>;
  return <AdminSupportQueue key={`${context.data.user_id}:${archived}`} scope={context.data.user_id!} archived={archived} ticketId={ticketId} onSelect={onSelect} />;
}

function AdminSupportQueue({ scope, archived, ticketId, onSelect }: { scope: string; archived: boolean; ticketId: string | null; onSelect: (id: string | null) => void }) {
  const [status, setStatus] = useState<SupportStatus>("new");
  const [search, setSearch] = useState("");
  const [term, setTerm] = useState("");
  const [offset, setOffset] = useState(0);
  useEffect(() => { const timer = setTimeout(() => { setTerm(search.trim()); setOffset(0); }, 300); return () => clearTimeout(timer); }, [search]);
  const params = new URLSearchParams({ archived: String(archived), limit: "25", offset: String(offset), search: term });
  if (!archived) params.set("status", status);
  const query = useQuery({ queryKey: ["support", scope, "admin-list", archived, status, term, offset], queryFn: ({ signal }) => listSupport(params.toString(), true, signal), retry: false, staleTime: 0, refetchInterval: 30_000 });
  return <div className="admin-support">
    <header><h2>{archived ? "Архив поддержки" : "Поддержка"}</h2><p className="support-meta">{archived ? "Завершённые более суток назад обращения. История переписки сохранена." : "Обращения пользователей и гостей. Ответы сохраняются в переписке и отправляются по email."}</p></header>
    {ticketId ? <><button type="button" className="support-back" onClick={() => onSelect(null)}>← К списку обращений</button><SupportThread key={ticketId} id={ticketId} scope={scope} admin /></> : <>
      {!archived && <div className="support-actions" aria-label="Фильтр статуса">{(Object.keys(supportStatus) as SupportStatus[]).map(value => <button key={value} type="button" aria-pressed={status === value} onClick={() => { setStatus(value); setOffset(0); }}>{supportStatus[value]}{value === "new" && query.data?.new_count ? ` · ${query.data.new_count}` : ""}</button>)}</div>}
      <input className="support-search" aria-label="Поиск обращений" placeholder="Тема, номер или email" value={search} onChange={event => setSearch(event.target.value)} />
      {query.isPending && <p role="status">Загружаем обращения…</p>}
      {query.isError && <p role="alert">Не удалось загрузить обращения. <button type="button" onClick={() => void query.refetch()}>Повторить</button></p>}
      {query.data && <><p className="support-meta">Всего: {query.data.total}</p><div className="support-queue">{query.data.items.map(ticket => <button type="button" className="support-ticket" key={ticket.id} onClick={() => onSelect(ticket.id)}><span className="support-meta">{ticket.reference} · {supportStatus[ticket.status]} · {new Date(ticket.updated_at).toLocaleString("ru-RU")}</span><strong>{ticket.subject}</strong><span className="support-meta">{ticket.requester_email}</span><p>{ticket.last_message_preview}</p><span className="support-meta">Сообщений: {ticket.message_count}{ticket.assigned_admin_email ? ` · ${ticket.assigned_admin_email}` : ""}</span></button>)}</div>{query.data.items.length === 0 && <p>Обращений пока нет.</p>}
        <div className="support-pagination support-actions"><button type="button" disabled={offset === 0} onClick={() => setOffset(value => Math.max(0, value - 25))}>Назад</button><span>{query.data.total ? offset + 1 : 0}–{Math.min(offset + 25, query.data.total)} из {query.data.total}</span><button type="button" disabled={offset + 25 >= query.data.total} onClick={() => setOffset(value => value + 25)}>Далее</button></div></>}
    </>}
  </div>;
}
