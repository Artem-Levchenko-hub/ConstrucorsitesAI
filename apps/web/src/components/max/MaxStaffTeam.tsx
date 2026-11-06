"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useMutation, useQueryClient, type UseQueryResult } from "@tanstack/react-query";
import { ArrowUpRight, Copy, Inbox, Link2, ShoppingBag, UsersRound } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ApiError } from "@/lib/api/client";
import {
  createMaxInvitation, revokeMaxInvitation, revokeMaxMember, saveMaxTeamSettings, staffRequestError, updateMaxMember,
  type StaffPermission, type TeamMember, type TeamSettings, type TeamView,
} from "@/lib/api/max-staff";
import { MAX_STAFF_JOIN_PATH, maxStaffProjectPath } from "@/lib/max-staff-path";
import { staffTime } from "./MaxStaffRecord";
import "./max-staff.css";

const permissionLabels = { orders: "Заказы", leads: "Заявки" } as const;
const permissions = Object.keys(permissionLabels) as StaffPermission[];
const blankInvite = { email: "", name: "", permissions: [] as StaffPermission[] };

function TeamPermissions({ value, onChange }: { value: StaffPermission[]; onChange: (next: StaffPermission[]) => void }) {
  return <fieldset className="max-staff-permissions">
    <legend>Рабочие разделы</legend>
    {permissions.map(permission => <label key={permission}>
      <input type="checkbox" checked={value.includes(permission)} onChange={event => {
        onChange(event.target.checked ? [...value, permission] : value.filter(item => item !== permission));
      }} />
      {permission === "orders" ? <ShoppingBag aria-hidden="true" /> : <Inbox aria-hidden="true" />}
      <span>{permissionLabels[permission]}</span>
    </label>)}
  </fieldset>;
}

type PersonAction =
  | { type: "edit"; userId: string; permissions: StaffPermission[] }
  | { type: "remove-member"; userId: string }
  | { type: "remove-invitation"; id: string };

export function MaxStaffTeam({ projectId, team, onDirtyChange }: {
  projectId: string; team: UseQueryResult<TeamView>; onDirtyChange?: (dirty: boolean) => void;
}) {
  const cache = useQueryClient();
  const [settingsDraft, setSettingsDraft] = useState<TeamSettings | null>(null);
  const [invite, setInvite] = useState(blankInvite);
  const [created, setCreated] = useState<{ href: string; expiresAt: string } | null>(null);
  const [copied, setCopied] = useState(false);
  const [editing, setEditing] = useState<{ member: TeamMember; permissions: StaffPermission[] } | null>(null);
  const linkField = useRef<HTMLInputElement>(null);
  const key = ["max-team", projectId];
  const refreshStaff = () => cache.invalidateQueries({ queryKey: ["max-staff-access", projectId] });

  const settings = useMutation({
    mutationFn: (value: TeamSettings) => saveMaxTeamSettings(projectId, value), retry: false,
    onSuccess: next => { cache.setQueryData(key, next); setSettingsDraft(null); void refreshStaff(); },
  });
  const invitation = useMutation({
    mutationFn: () => createMaxInvitation(projectId, { ...invite, email: invite.email.trim(), name: invite.name.trim() }), retry: false,
    onSuccess: next => {
      const fragment = new URLSearchParams({ token: next.token });
      setCreated({ href: `${location.origin}${MAX_STAFF_JOIN_PATH}#${fragment}`, expiresAt: next.expires_at });
      setCopied(false); setInvite(blankInvite); void cache.invalidateQueries({ queryKey: key });
    },
  });
  const person = useMutation({
    mutationFn: async (action: PersonAction) => {
      if (action.type === "edit") await updateMaxMember(projectId, action.userId, action.permissions);
      else if (action.type === "remove-member") await revokeMaxMember(projectId, action.userId);
      else await revokeMaxInvitation(projectId, action.id);
    },
    retry: false,
    onSuccess: () => { setEditing(null); void cache.invalidateQueries({ queryKey: key }); void refreshStaff(); },
  });
  const pending = settings.isPending || invitation.isPending || person.isPending;
  const dirty = Boolean(settingsDraft || invite.name || invite.email || invite.permissions.length || editing || pending || (created && !copied));
  useEffect(() => { onDirtyChange?.(dirty); return () => onDirtyChange?.(false); }, [dirty, onDirtyChange]);
  const current = settingsDraft ?? team.data?.settings;
  const needsPublication = settings.error instanceof ApiError && String(settings.error.code) === "unsupported";
  const projectHref = `/max/${encodeURIComponent(projectId)}`;

  async function copy() {
    if (!created) return;
    try { await navigator.clipboard.writeText(created.href); setCopied(true); }
    catch { linkField.current?.focus(); }
  }

  return <section className="max-staff-team" aria-label="Команда">
    <header className="max-staff-board-heading max-staff-team-intro"><div>
      <p>Пригласите сотрудников и выберите, с какими обращениями они будут работать.</p></div>
      <Button asChild variant="outline"><Link href={maxStaffProjectPath(projectId)}>Открыть рабочий кабинет <ArrowUpRight aria-hidden="true" /></Link></Button>
    </header>
    {team.isPending && <p role="status" className="max-staff-notice">Загружаем команду…</p>}
    {team.isError && <div role="alert" className="max-staff-notice" data-tone="error"><p>{staffRequestError(team.error)}</p>
      <Button variant="outline" onClick={() => void team.refetch()}>Повторить загрузку</Button>
    </div>}
    {team.isSuccess && current && <>
      <form className="max-staff-panel" onSubmit={event => { event.preventDefault(); if (settingsDraft && !pending) settings.mutate(settingsDraft); }}>
        <header><h3>Настройки очередей</h3><p>Включайте очереди после публикации версии приложения, которая их поддерживает.</p></header>
        <fieldset disabled={pending} className="max-staff-settings-fields">
          <div className="max-staff-queue-setting">
            <p className="max-staff-field-label">Заявки</p>
            <label className="max-staff-checkbox"><input type="checkbox" checked={current.leads_enabled}
              disabled={team.data.settings.leads_enabled} onChange={event => { setSettingsDraft({ ...current, leads_enabled: event.target.checked }); settings.reset(); }} />
              <span>Принимать заявки</span>
            </label>
            <p className="max-staff-field-help">{team.data.settings.leads_enabled ? "Рабочая очередь включена." : "Сотрудники смогут брать заявки и отмечать итог."}</p>
          </div>
          <div className="max-staff-queue-setting">
            <label className="max-staff-field">Оплата заказов<select value={current.orders_mode ?? ""} disabled={team.data.settings.orders_mode !== null}
              onChange={event => { setSettingsDraft({ ...current, orders_mode: (event.target.value || null) as TeamSettings["orders_mode"] }); settings.reset(); }}>
              <option value="">Заказы выключены</option><option value="pickup">При получении</option><option value="yookassa">Онлайн через ЮKassa</option>
            </select></label>
            <p className="max-staff-field-help">{team.data.settings.orders_mode ? "Способ оплаты уже выбран для этой очереди." : "Способ оплаты фиксируется при включении очереди заказов."}</p>
            {current.orders_mode && <label className="max-staff-field">Время приготовления, мин
              <Input type="number" min={1} max={120} required value={Number.isNaN(current.prep_minutes) ? "" : current.prep_minutes}
                onChange={event => { setSettingsDraft({ ...current, prep_minutes: event.target.valueAsNumber }); settings.reset(); }} />
            </label>}
          </div>
        </fieldset>
        {current.orders_mode === "yookassa" && <div className="max-staff-settings-hint">
          <p>Подключите ЮKassa в разделе «Бот и подключения». Для старого приложения попросите в чате «Подключи рабочую очередь и оплату через ЮKassa по сохранённому заказу», затем опубликуйте версию.</p>
          <p>Оплата требует проверки, если магазин ЮKassa запрашивает данные для чека. Текущий сценарий такую оплату не завершает.</p>
        </div>}
        {settings.isError && (needsPublication ? <div role="alert" className="max-staff-notice" data-tone="error">
          <p>{settings.error.message}</p>
          <p>Сначала обновите приложение в чате и опубликуйте новую версию. Затем вернитесь сюда и включите очередь.</p>
          <div className="max-staff-actions">
            <Button asChild variant="outline"><Link href={projectHref}>К чату</Link></Button>
            <Button asChild variant="outline"><Link href={`${projectHref}/publish`}>К публикации</Link></Button>
          </div>
        </div> : <p role="alert">{staffRequestError(settings.error)}</p>)}
        {settings.isSuccess && <p role="status">Настройки очередей сохранены.</p>}
        <div className="max-staff-actions">
          <Button type="submit" disabled={!settingsDraft || pending}>{settings.isPending ? "Сохраняем…" : "Сохранить настройки"}</Button>
          {settingsDraft && <Button type="button" variant="ghost" disabled={pending} onClick={() => { setSettingsDraft(null); settings.reset(); }}>Отменить</Button>}
        </div>
      </form>
      <div className="max-staff-team-columns">
        <form className="max-staff-panel" onSubmit={event => {
          event.preventDefault();
          if (!pending && invite.email.trim() && invite.name.trim() && invite.permissions.length) invitation.mutate();
        }}>
          <header><h3>Пригласить сотрудника</h3><p>Ссылка сработает один раз для аккаунта с указанным подтверждённым email.</p></header>
          <fieldset disabled={pending} className="max-staff-form-fields">
            <label className="max-staff-field">Имя<Input type="text" autoComplete="off" required maxLength={64} value={invite.name} onChange={event => setInvite({ ...invite, name: event.target.value })} /></label>
            <label className="max-staff-field">Email<Input autoComplete="off" type="email" required maxLength={254} value={invite.email} onChange={event => setInvite({ ...invite, email: event.target.value })} /></label>
            <TeamPermissions value={invite.permissions} onChange={value => setInvite({ ...invite, permissions: value })} />
          </fieldset>
          {invitation.isError && <p role="alert">{staffRequestError(invitation.error)}</p>}
          <div className="max-staff-actions"><Button type="submit" disabled={pending || !invite.permissions.length || !invite.email.trim() || !invite.name.trim()}>
            <Link2 aria-hidden="true" />{invitation.isPending ? "Создаём…" : "Создать приглашение"}
          </Button></div>
          {created && <div className="max-staff-invite-result">
            <label className="max-staff-field">Персональная ссылка<Input id="max-staff-invite-link" ref={linkField} readOnly value={created.href}
              onFocus={event => event.target.select()} onCopy={() => setCopied(true)} /></label>
            <p role="status">{copied ? "Ссылка скопирована. " : ""}Действует до {staffTime(created.expiresAt, true)}.</p>
            <Button type="button" variant="outline" onClick={() => void copy()}><Copy aria-hidden="true" /> Скопировать</Button>
          </div>}
        </form>
        <section className="max-staff-panel" aria-label="Сотрудники и приглашения">
          <header><h3>Сотрудники и приглашения</h3><p>Список действующих доступов и ожидающих приглашений.</p></header>
          {person.isError && <p role="alert">{staffRequestError(person.error)}</p>}
          {!team.data.members.length && !team.data.invitations.length && <div className="max-staff-empty"><UsersRound aria-hidden="true" />
            <h4>Сотрудников пока нет</h4><p>Создайте первое приглашение, чтобы добавить сотрудника.</p></div>}
          <ul className="max-staff-people">
            {team.data.members.map(member => <li key={member.user_id}>
              <strong>{member.name}</strong><small>{member.email}</small>
              {editing?.member.user_id === member.user_id ? <form onSubmit={event => {
                event.preventDefault(); if (!pending && editing.permissions.length) person.mutate({ type: "edit", userId: member.user_id, permissions: editing.permissions });
              }}>
                <fieldset disabled={pending} className="max-staff-form-fields">
                  <TeamPermissions value={editing.permissions} onChange={value => setEditing({ member, permissions: value })} />
                  <div className="max-staff-actions"><Button type="submit" disabled={!editing.permissions.length}>Сохранить доступ</Button>
                    <Button type="button" variant="ghost" onClick={() => { setEditing(null); person.reset(); }}>Отменить</Button></div>
                </fieldset>
              </form> : <>
                <p className="max-staff-project-permissions">{member.permissions.map(permission => permissionLabels[permission]).join(" · ")}</p>
                <div className="max-staff-actions"><Button type="button" variant="outline" disabled={pending}
                  onClick={() => { setEditing({ member, permissions: [...member.permissions] }); person.reset(); }}>Изменить доступ</Button>
                  <Button type="button" variant="ghost" disabled={pending} onClick={() => {
                    if (window.confirm("Закрыть доступ этого сотрудника к рабочему кабинету?")) person.mutate({ type: "remove-member", userId: member.user_id });
                  }}>Отозвать доступ</Button></div>
              </>}
            </li>)}
            {team.data.invitations.map(item => <li key={item.id}>
              <strong>{item.name}</strong><small>{item.email}</small>
              <p className="max-staff-project-permissions">{item.permissions.map(permission => permissionLabels[permission]).join(" · ")}</p>
              <p>Приглашение до {staffTime(item.expires_at, true)}</p>
              <Button type="button" variant="ghost" disabled={pending} onClick={() => {
                if (window.confirm("Отменить это приглашение?")) person.mutate({ type: "remove-invitation", id: item.id });
              }}>Отменить приглашение</Button>
            </li>)}
          </ul>
        </section>
      </div>
    </>}
  </section>;
}
