"use client";

import { Fragment, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, ChevronDown, Loader2, X } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { decideBusiness, listBusinessReviews, type BusinessReview } from "@/lib/api/max-account";
import { ApiError } from "@/lib/api/client";
import { AdminCellLabel, AdminSearch, AdminState, AdminStatus, adminDate } from "./AdminPresentation";

const kindLabels = { legal_entity: "Юридическое лицо", sole_proprietor: "Индивидуальный предприниматель", self_employed: "Самозанятый" };
const statusLabels = { pending: "Ожидает проверки", verified: "Подтверждён", rejected: "Отклонён", suspended: "Приостановлен" };
const tones = { pending: "warning", verified: "success", rejected: "danger", suspended: "neutral" } as const;

export function AdminVerificationPanel() {
  const queryClient = useQueryClient();
  const [filter, setFilter] = useState<"pending" | "all">("pending");
  const [search, setSearch] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [notes, setNotes] = useState<Record<string, string>>({});
  const reviews = useQuery({ queryKey: ["admin-business-reviews"], queryFn: listBusinessReviews, retry: false });
  const decision = useMutation({
    mutationFn: ({ inn, approved, note }: { inn: string; approved: boolean; note?: string }) => decideBusiness(inn, approved, note),
    onSuccess: profile => {
      void queryClient.invalidateQueries({ queryKey: ["admin-business-reviews"] });
      toast.success(profile.status === "verified" ? "Организация подтверждена" : "Заявка отклонена");
    },
    onError: error => toast.error("Не удалось сохранить решение", { description: error instanceof Error ? error.message : "Повторите попытку" }),
  });
  const visible = useMemo(() => {
    const needle = search.trim().toLocaleLowerCase("ru-RU");
    return (reviews.data ?? []).filter(item => (filter === "all" || item.status === "pending")
      && (!needle || item.legal_name.toLocaleLowerCase("ru-RU").includes(needle) || item.inn.includes(needle) || item.owner_email.toLocaleLowerCase("ru-RU").includes(needle)));
  }, [filter, reviews.data, search]);
  if (reviews.isError && reviews.error instanceof ApiError && reviews.error.status === 403)
    return <AdminState error title="Нет административного доступа" description="Этот аккаунт не имеет роли администратора." />;
  if (reviews.isLoading) return <AdminState loading title="Загружаем организации" />;
  if (reviews.isError) return <AdminState error title="Не удалось загрузить очередь" description={reviews.error instanceof Error ? reviews.error.message : "Повторите попытку"} retry={() => void reviews.refetch()} />;

  return <div className="admin-panel">
    <div className="admin-toolbar admin-toolbar--organizations">
      <div className="admin-filters" aria-label="Фильтр организаций">
        {(["pending", "all"] as const).map(value => <button key={value} type="button" aria-pressed={filter === value} onClick={() => setFilter(value)}>{value === "pending" ? "Ожидают проверки" : "Все заявки"}</button>)}
      </div>
      <AdminSearch label="Поиск организаций" placeholder="Название, ИНН или email" value={search} onChange={setSearch} />
    </div>
    <p className="admin-list-caption">{filter === "pending" ? "На проверке" : "Показано"}: <strong>{visible.length}</strong> · среди загруженных заявок</p>
    {visible.length === 0 ? <AdminState title={search.trim() ? "Ничего не найдено" : filter === "pending" ? "Очередь пуста" : "Заявок пока нет"}
      description={search.trim() ? "Измените запрос или выберите все заявки." : "Новые заявки появятся здесь."} /> :
      <div className="admin-table-wrap"><table role="table" className="admin-table admin-business-table" aria-label="Организации">
        <thead><tr>{[["identity", "Организация"], ["owner", "Владелец"], ["status", "Статус"], ["actions", ""]].map(([id, label]) => <th key={id} id={`admin-business-${id}`} scope="col">{label || <span className="sr-only">Действия</span>}</th>)}</tr></thead>
        <tbody>{visible.map((item: BusinessReview) => <Fragment key={item.id}>
          <tr role="row">
            <td role="cell" headers="admin-business-identity"><AdminCellLabel>Организация</AdminCellLabel><strong>{item.legal_name}</strong><small>ИНН {item.inn}</small><small>{kindLabels[item.kind]}</small></td>
            <td role="cell" headers="admin-business-owner"><AdminCellLabel>Владелец</AdminCellLabel>{item.owner_email}<small>Заявка от {adminDate(item.created_at)}</small></td>
            <td role="cell" headers="admin-business-status"><AdminCellLabel>Статус</AdminCellLabel><AdminStatus tone={tones[item.status] ?? "neutral"}>{statusLabels[item.status] ?? item.status}</AdminStatus></td>
            <td role="cell" headers="admin-business-actions" className="admin-row-actions"><Button variant="outline" size="sm" aria-expanded={expanded === item.id} aria-controls={`review-${item.id}`} onClick={() => setExpanded(expanded === item.id ? null : item.id)}>{expanded === item.id ? "Свернуть" : item.status === "pending" ? "Рассмотреть" : "Подробнее"}<ChevronDown /></Button></td>
          </tr>
          {expanded === item.id && <tr className="admin-review-row"><td colSpan={4}>
            <section id={`review-${item.id}`} className="admin-review" aria-label={`Проверка: ${item.legal_name}`}>
              <div><h3>Реквизиты для проверки</h3><dl><div><dt>ОГРН / ОГРНИП</dt><dd>{item.ogrn || "Не указан"}</dd></div>
                <div><dt>Подтверждение</dt><dd>{item.verified_at ? adminDate(item.verified_at, true) : "Ещё не подтверждён"}</dd></div></dl>
                {item.verification_note && <p className="admin-muted">{item.verification_note}</p>}
              </div>
              {item.status === "pending" && <div className="admin-review-decision">
                <label htmlFor={`note-${item.id}`}>Комментарий к решению</label>
                <Input id={`note-${item.id}`} aria-label="Комментарий к решению" value={notes[item.inn] ?? ""} onChange={event => setNotes(current => ({ ...current, [item.inn]: event.target.value }))} placeholder="Что проверено или нужно уточнить" disabled={decision.isPending} />
                {decision.isError && decision.variables?.inn === item.inn && <p role="alert" className="admin-inline-error">Решение не сохранено. {decision.error instanceof Error ? decision.error.message : "Повторите попытку."}</p>}
                <div className="admin-review-actions">
                  <Button variant="outline" className="admin-reject" disabled={decision.isPending} onClick={() => decision.mutate({ inn: item.inn, approved: false, note: notes[item.inn] })}><X />Отклонить</Button>
                  <Button className="admin-approve" disabled={decision.isPending} onClick={() => decision.mutate({ inn: item.inn, approved: true, note: notes[item.inn] || "Реквизиты проверены" })}>{decision.isPending && decision.variables?.inn === item.inn ? <Loader2 className="animate-spin motion-reduce:animate-none" /> : <Check />}Подтвердить</Button>
                </div>
              </div>}
            </section>
          </td></tr>}
        </Fragment>)}</tbody>
      </table></div>}
  </div>;
}
