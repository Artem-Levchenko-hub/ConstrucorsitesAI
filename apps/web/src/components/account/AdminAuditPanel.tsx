"use client";

import { useQuery } from "@tanstack/react-query";
import { listAdminAudit } from "@/lib/api/admin";
import { AdminCellLabel, AdminState, adminDate } from "./AdminPresentation";

const fields = { role: "Роль", status: "Статус", email_verified: "Email подтверждён", business_status: "Организация" };
const actions: Record<string, string> = {
  "account.update": "Аккаунт обновлён",
  "business.verify": "Организация подтверждена",
  "creator.subscription.lifetime_business.bootstrap": "Активирован бессрочный тариф Business",
};
const values: Record<string, string> = { admin: "Администратор", user: "Пользователь", active: "Активен", suspended: "Приостановлен", deletion_pending: "Удаление запрошено", pending: "Ожидает проверки", verified: "Подтверждена", rejected: "Отклонена" };
function valueLabel(value: unknown) {
  if (value === undefined || value === null) return "—";
  if (typeof value === "boolean") return value ? "Да" : "Нет";
  return values[String(value)] ?? String(value);
}
export function AdminAuditPanel() {
  const audit = useQuery({ queryKey: ["admin-audit"], queryFn: listAdminAudit, retry: false });
  if (audit.isLoading) return <AdminState loading title="Загружаем журнал" />;
  if (audit.isError) return <AdminState error title="Журнал не загрузился" description={audit.error instanceof Error ? audit.error.message : "Повторите попытку"} retry={() => void audit.refetch()} />;
  if (!audit.data?.length) return <AdminState title="Изменений пока нет" description="Выдача прав и подтверждения появятся здесь." />;
  return <div className="admin-panel">
    <p className="admin-list-caption">Последние загруженные изменения · <strong>{audit.data.length}</strong></p>
    <div className="admin-table-wrap"><table role="table" className="admin-table admin-audit-table" aria-label="Журнал изменений">
      <thead><tr>{[["date", "Когда"], ["actor", "Кто изменил"], ["target", "Аккаунт"], ["changes", "Изменение"]].map(([id, label]) => <th key={id} id={`admin-audit-${id}`} scope="col">{label}</th>)}</tr></thead>
      <tbody>{audit.data.map(event => {
        const changes = Object.entries(fields).filter(([key]) => event.details.before?.[key] !== event.details.after?.[key]);
        return <tr key={event.id} role="row">
          <td role="cell" headers="admin-audit-date"><AdminCellLabel>Когда</AdminCellLabel><time dateTime={event.created_at}>{adminDate(event.created_at, true)}</time></td>
          <td role="cell" headers="admin-audit-actor"><AdminCellLabel>Кто изменил</AdminCellLabel>{event.actor_email}</td>
          <td role="cell" headers="admin-audit-target"><AdminCellLabel>Аккаунт</AdminCellLabel>{event.target_email}</td>
          <td role="cell" headers="admin-audit-changes"><AdminCellLabel>Изменение</AdminCellLabel>
            {changes.length ? <ul className="admin-changes">{changes.map(([key, label]) => <li key={key}><span>{label}: </span><span className="admin-muted">{valueLabel(event.details.before?.[key])}</span> → <strong>{valueLabel(event.details.after?.[key])}</strong></li>)}</ul> : <span>{actions[event.action] ?? event.action}</span>}
            {event.details.note && <small>{event.details.note}</small>}
          </td>
        </tr>;
      })}</tbody>
    </table></div>
  </div>;
}
