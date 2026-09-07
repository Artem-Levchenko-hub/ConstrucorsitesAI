"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BadgeCheck, Ban, ChevronDown, KeyRound, Loader2, RotateCcw, ShieldCheck, ShieldOff } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { listAdminUsers, updateAdminUser, type AdminUser, type AdminUserUpdate } from "@/lib/api/admin";
import { AdminCellLabel, AdminSearch, AdminState, AdminStatus, adminDate } from "./AdminPresentation";

function UserStatus({ status }: { status: string }) {
  if (status === "active") return <AdminStatus tone="success">Активен</AdminStatus>;
  if (status === "suspended") return <AdminStatus tone="warning">Приостановлен</AdminStatus>;
  if (status === "deletion_pending") return <AdminStatus tone="danger">Удаление запрошено</AdminStatus>;
  return <AdminStatus tone="neutral">{status || "Неизвестно"}</AdminStatus>;
}

export function AdminUsersPanel({ currentEmail }: { currentEmail: string }) {
  const queryClient = useQueryClient();
  const [search, setSearch] = useState("");
  const users = useQuery({ queryKey: ["admin-users"], queryFn: () => listAdminUsers(), retry: false });
  const update = useMutation({
    mutationFn: ({ user, change }: { user: AdminUser; change: AdminUserUpdate }) => updateAdminUser(user.id, change),
    onSuccess: user => {
      void queryClient.invalidateQueries({ queryKey: ["admin-users"] });
      void queryClient.invalidateQueries({ queryKey: ["admin-audit"] });
      toast.success(`Аккаунт ${user.email} обновлён`);
    },
    onError: error => toast.error("Не удалось обновить аккаунт", { description: error instanceof Error ? error.message : "Повторите попытку" }),
  });
  const visible = useMemo(() => {
    const needle = search.trim().toLocaleLowerCase("ru-RU");
    return (users.data ?? []).filter(user => !needle || user.email.toLocaleLowerCase("ru-RU").includes(needle)
      || user.business?.legal_name.toLocaleLowerCase("ru-RU").includes(needle) || user.business?.inn.includes(needle));
  }, [search, users.data]);
  if (users.isLoading) return <AdminState loading title="Загружаем аккаунты" />;
  if (users.isError) return <AdminState error title="Аккаунты не загрузились" description={users.error instanceof Error ? users.error.message : "Повторите попытку"} retry={() => void users.refetch()} />;

  return <div className="admin-panel">
    <div className="admin-toolbar">
      <AdminSearch label="Поиск аккаунтов" placeholder="Email, организация или ИНН" value={search} onChange={setSearch} />
      <p className="admin-count"><strong>{visible.length}</strong> из {users.data?.length ?? 0} загруженных</p>
    </div>
    {update.isError && <p className="admin-inline-error" role="alert">Изменение не сохранено. {update.error instanceof Error ? update.error.message : "Повторите попытку."}</p>}
    {visible.length === 0 ? <AdminState title={search.trim() ? "Ничего не найдено" : "Аккаунтов пока нет"} description={search.trim() ? "Попробуйте другой email, название организации или ИНН." : undefined} /> :
      <div className="admin-table-wrap"><table role="table" className="admin-table admin-users-table" aria-label="Аккаунты">
        <thead><tr>{[["identity", "Аккаунт"], ["role", "Роль"], ["status", "Статус"], ["balance", "Баланс"], ["actions", ""]].map(([id, label]) =>
          <th key={id} scope="col" id={`admin-users-${id}`}>{label || <span className="sr-only">Действия</span>}</th>)}</tr></thead>
        <tbody>{visible.map(user => {
          const isSelf = user.email.toLowerCase() === currentEmail.toLowerCase();
          const pending = update.isPending && update.variables?.user.id === user.id;
          return <tr key={user.id} role="row">
            <td role="cell" headers="admin-users-identity"><AdminCellLabel>Аккаунт</AdminCellLabel>
              <div className="admin-identity"><strong>{user.email}</strong>{isSelf && <span className="admin-self">Вы</span>}</div>
              <small className={user.email_verified_at ? "" : "admin-attention"}>{user.email_verified_at ? "Email подтверждён" : "Email не подтверждён"}</small>
              {user.business && <small>{user.business.legal_name} · ИНН {user.business.inn}</small>}
              <small className="admin-date">Создан {adminDate(user.created_at)} · {user.last_login_at ? `Вход ${adminDate(user.last_login_at)}` : "Ещё не входил"}</small>
            </td>
            <td role="cell" headers="admin-users-role"><AdminCellLabel>Роль</AdminCellLabel><span className={user.is_admin ? "admin-role" : "admin-muted"}>{user.is_admin && <ShieldCheck aria-hidden="true" />} {user.is_admin ? "Администратор" : "Пользователь"}</span></td>
            <td role="cell" headers="admin-users-status"><AdminCellLabel>Статус</AdminCellLabel><UserStatus status={user.status} /></td>
            <td role="cell" headers="admin-users-balance"><AdminCellLabel>Баланс</AdminCellLabel><span className="admin-money">{Number(user.wallet_balance_rub).toLocaleString("ru-RU")} ₽</span></td>
            <td role="cell" headers="admin-users-actions" className="admin-row-actions">
              <DropdownMenu>
                <DropdownMenuTrigger asChild><Button variant="outline" size="sm" disabled={pending} aria-label={`Действия с аккаунтом ${user.email}`}>{pending ? <Loader2 className="animate-spin motion-reduce:animate-none" /> : null}Действия<ChevronDown /></Button></DropdownMenuTrigger>
                <DropdownMenuContent data-max-studio className="admin-action-menu" align="end">
                  <DropdownMenuLabel className="admin-menu-label">{user.email}</DropdownMenuLabel>
                  {!user.email_verified_at && <DropdownMenuItem disabled={pending} onSelect={() => update.mutate({ user, change: { email_verified: true } })}><BadgeCheck />Подтвердить email</DropdownMenuItem>}
                  {user.business && user.business.status !== "verified" && <DropdownMenuItem disabled={pending} onSelect={() => update.mutate({ user, change: { business_verified: true, note: "Реквизиты проверены администратором" } })}><KeyRound />Подтвердить бизнес</DropdownMenuItem>}
                  {user.role === "admin"
                    ? <DropdownMenuItem disabled={pending || isSelf} onSelect={() => update.mutate({ user, change: { role: "user" } })}><ShieldOff />Снять права</DropdownMenuItem>
                    : <DropdownMenuItem disabled={pending} onSelect={() => update.mutate({ user, change: { role: "admin" } })}><ShieldCheck />Сделать админом</DropdownMenuItem>}
                  <DropdownMenuSeparator />
                  {user.status === "active"
                    ? <DropdownMenuItem className="admin-danger-action" disabled={pending || isSelf} onSelect={() => update.mutate({ user, change: { status: "suspended" } })}><Ban />Приостановить</DropdownMenuItem>
                    : <DropdownMenuItem disabled={pending} onSelect={() => update.mutate({ user, change: { status: "active" } })}><RotateCcw />Восстановить</DropdownMenuItem>}
                  {isSelf && <p className="admin-menu-note">Нельзя снять права или приостановить свой аккаунт.</p>}
                </DropdownMenuContent>
              </DropdownMenu>
            </td>
          </tr>;
        })}</tbody>
      </table></div>}
  </div>;
}
