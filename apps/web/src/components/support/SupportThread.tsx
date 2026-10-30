"use client";
import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSupportClock } from "@/hooks/useSupportClock";
import { ApiError } from "@/lib/api/client";
import { clearSupportReply, readSupportReply, saveSupportReply, getSupport, replySupport, supportStatus, updateSupport, supportHours, supportTiming, type SupportDetail, type SupportStatus } from "@/lib/api/support";

export function SupportThread({ id, scope, admin = false, token }: { id: string; scope: string; admin?: boolean; token?: string }) {
  const now = useSupportClock();
  const client = useQueryClient();
  const key = ["support", scope, admin ? "admin-detail" : "detail", id];
  const query = useQuery({ queryKey: key, queryFn: ({ signal }) => getSupport(id, admin, token, signal), staleTime: 0, retry: false, refetchInterval: 30_000 });
  const [draft] = useState(() => readSupportReply(scope, id));
  const [body, setBody] = useState(draft?.message ?? "");
  const active = useRef(true);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const busy = useRef(false);
  const attempt = useRef<{ request_id: string; message: string } | null>(draft);
  async function mutate(action: () => Promise<SupportDetail>, reply = false) {
    if (busy.current) return;
    busy.current = true; setPending(true); setError("");
    try {
      const result = await action(); if (!active.current) return; client.setQueryData(key, result);
      if (reply) { setBody(""); attempt.current = null; clearSupportReply(); }
      await client.invalidateQueries({ queryKey: ["support"] });
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 409) { setError("Обращение уже изменилось. Проверьте актуальный статус и повторите действие."); await query.refetch(); }
      else setError("Не удалось отправить. Текст сохранён — повторите попытку.");
    } finally { busy.current = false; setPending(false); }
  }
  if (query.isError) return <div role="alert">Обращение недоступно. <button type="button" onClick={() => void query.refetch()}>Повторить</button></div>;
  if (!query.data) return <p role="status">Загружаем переписку…</p>;
  const ticket = query.data;
  return <section className="support-thread" aria-label="Переписка с поддержкой">
    <header><span className="support-meta">{ticket.reference} · {supportStatus[ticket.status]}{ticket.archived ? " · Архив" : ""}</span><h3>{ticket.subject}</h3>
      {admin && <p className="support-meta">{ticket.requester_email}{ticket.assigned_admin_email ? ` · В работе у ${ticket.assigned_admin_email}` : ""}</p>}
    </header>
    <p className="support-meta">{ticket.support_hours ?? supportHours}. {supportTiming(ticket, now)}</p>
    {admin && !ticket.first_responded_at && ticket.first_response_due_at && <p className="support-meta">Ожидание: {Math.max(0, Math.floor((now - new Date(ticket.created_at).getTime()) / 60_000))} мин</p>}
    {admin && <div className="support-actions" aria-label="Статус обращения">{(Object.keys(supportStatus) as SupportStatus[]).map(status => <button key={status} type="button" aria-pressed={ticket.status === status} disabled={pending || ticket.status === status} onClick={() => void mutate(() => updateSupport(id, status, ticket.version))}>{status === "in_progress" ? "Взять в работу" : status === "done" ? "Готово" : "Вернуть в новые"}</button>)}</div>}
    <ol className="support-messages" aria-live="polite" aria-relevant="additions text">{ticket.messages.map(message => <li key={message.id} data-author={message.author}><div className="support-meta">{message.author === "support" ? "Поддержка Yleum" : admin ? "Пользователь" : "Вы"} · <time dateTime={message.created_at}>{new Date(message.created_at).toLocaleString("ru-RU")}</time></div><p>{message.body}</p></li>)}</ol>
    <form className="support-form" onSubmit={event => { event.preventDefault(); const message = body.trim(); if (!message) return; if (attempt.current?.message !== message) attempt.current = { request_id: crypto.randomUUID(), message }; const payload = attempt.current; saveSupportReply(scope, id, payload); void mutate(() => replySupport(id, payload, admin, token), true); }}>
      <label>Ваш ответ<textarea aria-label="Ответ" value={body} onChange={event => setBody(event.target.value)} maxLength={8000} required rows={4} disabled={pending} /></label>
      {ticket.status === "done" && !admin && <p className="support-meta">Новый ответ снова откроет обращение.</p>}
      {error && <p role="alert" className="support-error">{error}</p>}
      <button className="support-primary" type="submit" disabled={pending || !body.trim()}>{pending ? "Отправляем…" : "Отправить ответ"}</button>
    </form>
  </section>;
}
