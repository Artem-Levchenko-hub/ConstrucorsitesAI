"use client";

import { useEffect, useRef, useState } from "react";
import { ArrowLeft, Clock3, History } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import {
  getStaffHistory, staffRequestError, type StaffAccess, type StaffAction, type StaffRecord, type TeamMember,
} from "@/lib/api/max-staff";

export const staffStatus = {
  new: "Новый", in_progress: "В работе", ready: "Готов", completed: "Завершён", cancelled: "Отменён",
} as const;

const actionLabels: Record<StaffAction, string> = {
  claim: "Взять в работу", ready: "Отметить готовым", hand_over: "Выдать заказ", close: "Закрыть заявку", reassign: "Сменить ответственного",
};
const historyLabels: Record<StaffAction, string> = {
  claim: "Взял в работу", ready: "Отметил готовым", hand_over: "Выдал заказ", close: "Закрыл заявку", reassign: "Сменил ответственного",
};
const clockFormat = new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit" });
const dateFormat = new Intl.DateTimeFormat("ru-RU", { dateStyle: "short", timeStyle: "short" });

export function staffTime(value: string | null, full = false): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isFinite(date.valueOf()) ? (full ? dateFormat : clockFormat).format(date) : "—";
}

export function staffRecordTitle(record: StaffRecord): string {
  if (record.resource === "leads") return record.customer_name || "Заявка";
  return record.items[0]?.title || "Заказ";
}

function availableAction(record: StaffRecord, access: StaffAccess): StaffAction | null {
  if (record.resource === "orders" && record.payment === "test") return null;
  if (record.status === "completed" || record.status === "cancelled") return null;
  if (!record.assignee_id) return "claim";
  if (record.status === "new") return record.assignee_id === access.actor_id ? "claim" : null;
  if (record.assignee_id !== access.actor_id && !access.is_owner) return null;
  if (record.resource === "leads") return record.status === "in_progress" ? "close" : null;
  if (record.status === "in_progress") return "ready";
  return record.status === "ready" ? "hand_over" : null;
}

export function MaxStaffRecord({
  record, access, members, locked, claimAvailable, onBack, onAction,
}: {
  record: StaffRecord; access: StaffAccess; members: TeamMember[]; locked: boolean; claimAvailable: boolean;
  onBack: () => void; onAction: (action: StaffAction, details?: { outcome?: string; assignee_id?: string | null }) => void;
}) {
  const [outcome, setOutcome] = useState("");
  const [assignee, setAssignee] = useState<string | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => { heading.current?.focus(); }, []);
  const history = useQuery({
    queryKey: ["max-staff-history", access.project_id, access.actor_id, record.resource, record.id],
    queryFn: ({ signal }) => getStaffHistory(access.project_id, record, signal),
    enabled: access.is_owner && historyOpen, retry: false, staleTime: 0, gcTime: 0,
  });
  const nextAction = availableAction(record, access);
  const action = nextAction === "claim" && !claimAvailable ? null : nextAction;
  const canReassign = access.is_owner && record.status !== "completed" && record.status !== "cancelled"
    && !(record.resource === "orders" && record.payment === "test");
  const selectedAssignee = assignee ?? record.assignee_id ?? "";
  const eligible = members.filter(member => member.permissions.includes(record.resource));

  return <article className="max-staff-record" aria-label={record.resource === "orders" ? "Детали заказа" : "Детали заявки"}>
    <Button type="button" variant="ghost" className="max-staff-record-back" onClick={onBack}><ArrowLeft aria-hidden="true" /> К очереди</Button>
    <div className="max-staff-record-heading">
      <div><p className="max-staff-kicker">{record.resource === "orders" ? "Заказ" : "Заявка"}</p>
        <h3 ref={heading} tabIndex={-1}>{staffRecordTitle(record)}</h3></div>
      <span className="max-staff-status" data-status={record.status}>{staffStatus[record.status]}</span>
    </div>
    <dl className="max-staff-meta">
      <div><dt>Поступил</dt><dd>{staffTime(record.created_at, true)}</dd></div>
      <div><dt>Ответственный</dt><dd>{record.assignee_name || "Не назначен"}</dd></div>
      {record.resource === "orders" && <>
        <div><dt>Выдача</dt><dd>{record.scheduled_for ? staffTime(record.scheduled_for, true) : "Как можно скорее"}</dd></div>
        {record.prepare_at && <div><dt>Начать готовить</dt><dd>{staffTime(record.prepare_at, true)}</dd></div>}
      </>}
    </dl>
    {record.resource === "orders" ? <>
      <p className="max-staff-payment"><Clock3 aria-hidden="true" />
        {{ pickup: "Оплата при получении", paid: "Оплачен онлайн", test: "Тестовая оплата" }[record.payment]}</p>
      <ul className="max-staff-items" aria-label="Состав заказа">
        {record.items.map((item, index) => <li key={index}><div>{item.title}{item.option && <small>{item.option}</small>}</div><strong>×{item.quantity}</strong></li>)}
      </ul>
    </> : <div className="max-staff-contact">
      {record.contact_phone && <a href={`tel:${encodeURIComponent(record.contact_phone)}`}>{record.contact_phone}</a>}
      {record.contact_email && <a href={`mailto:${encodeURIComponent(record.contact_email)}`}>{record.contact_email}</a>}
    </div>}
    {record.comment && <p className="max-staff-comment"><strong>Комментарий клиента</strong>{record.comment}</p>}
    {record.outcome && <p className="max-staff-comment"><strong>Итог</strong>{record.outcome}</p>}
    {action && <form className="max-staff-record-action" onSubmit={event => {
      event.preventDefault();
      if (!locked && (action !== "close" || outcome.trim())) onAction(action, action === "close" ? { outcome: outcome.trim() } : undefined);
    }}>
      {action === "close" && <label className="max-staff-field">Итог работы
        <textarea required maxLength={500} rows={3} disabled={locked} value={outcome} onChange={event => setOutcome(event.target.value)} />
      </label>}
      <Button type="submit" disabled={locked || (action === "close" && !outcome.trim())}>
        {action === "claim" && record.status === "ready" ? "Назначить себе" : actionLabels[action]}
      </Button>
    </form>}
    {canReassign && <form className="max-staff-assignment" onSubmit={event => {
      event.preventDefault();
      if (!locked && selectedAssignee !== (record.assignee_id ?? "")) onAction("reassign", { assignee_id: selectedAssignee || null });
    }}>
      <label className="max-staff-field">Ответственный<select value={selectedAssignee} disabled={locked}
        onChange={event => setAssignee(event.target.value)}>
        <option value="">Снять назначение</option>
        <option value={access.actor_id}>Я</option>
        {eligible.map(member => <option key={member.user_id} value={member.user_id}>{member.name}</option>)}
        {record.assignee_id && record.assignee_id !== access.actor_id && !eligible.some(member => member.user_id === record.assignee_id)
          && <option value={record.assignee_id}>{record.assignee_name || "Текущий сотрудник"}</option>}
      </select></label>
      <Button type="submit" variant="outline" disabled={locked || selectedAssignee === (record.assignee_id ?? "")}>Сохранить назначение</Button>
    </form>}
    {access.is_owner && <details className="max-staff-history" onToggle={event => setHistoryOpen(event.currentTarget.open)}>
      <summary><History aria-hidden="true" /> История действий</summary>
      {history.isPending && historyOpen && <p role="status">Загружаем историю…</p>}
      {history.isError && <div role="alert"><p>{staffRequestError(history.error)}</p><Button variant="outline" onClick={() => void history.refetch()}>Повторить</Button></div>}
      {history.isSuccess && (!history.data.length ? <p>Действий пока нет.</p> : <ol>{history.data.map((event, index) => <li key={index}>
        <strong>{event.actor_name}</strong> · {historyLabels[event.action]} · <time dateTime={event.created_at}>{staffTime(event.created_at, true)}</time>
        {event.assignee_name && <p>Ответственный: {event.assignee_name}</p>}{event.outcome && <p>{event.outcome}</p>}
      </li>)}</ol>)}
    </details>}
  </article>;
}
