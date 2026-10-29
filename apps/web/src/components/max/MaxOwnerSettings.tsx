"use client";

import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ApiError } from "@/lib/api/client";
import { getMaxProjectConfig, getMaxProjectContent, saveMaxProjectConfig } from "@/lib/api/max-studio";
import { ownerRequestError } from "@/lib/api/max-management";
import type { MaxContentDelivery, MaxProjectConfig } from "@/lib/api/types";

type Fields = { name: string; summary: string; email: string };
type Draft = { baseline: MaxProjectConfig; fields: Fields };
const fieldsOf = (value: MaxProjectConfig): Fields => ({ name: value.config.app_name, summary: value.config.summary, email: value.config.support.email ?? "" });

export function MaxOwnerSettings(props: { projectId: string; onDirtyChange?: (dirty: boolean) => void }) {
  return <Settings key={props.projectId} {...props} />;
}
function Settings({ projectId, onDirtyChange }: { projectId: string; onDirtyChange?: (dirty: boolean) => void }) {
  const qc = useQueryClient();
  const config = useQuery({ queryKey: ["max-config", projectId], queryFn: () => getMaxProjectConfig(projectId), retry: false, refetchOnWindowFocus: false });
  const [draft, setDraft] = useState<Draft | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [savedVersion, setSavedVersion] = useState<number | null>(null);
  const delivery = useQuery({
    queryKey: ["max-content", projectId], queryFn: () => getMaxProjectContent(projectId),
    enabled: !pending, retry: false,
    refetchInterval: query => query.state.data && [query.state.data.public.state, query.state.data.preview.state].some(state => state === "pending" || state === "unknown") ? 5_000 : false,
  });
  const [latest, setLatest] = useState<MaxProjectConfig | null>(null);
  const saving = useRef(false); const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => { onDirtyChange?.(Boolean(draft) || pending); return () => onDirtyChange?.(false); }, [onDirtyChange, draft, pending]);
  useEffect(() => {
    if (!draft) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warn); return () => window.removeEventListener("beforeunload", warn);
  }, [draft]);
  const fields = draft?.fields ?? (config.data ? fieldsOf(config.data) : null);
  const conflict = error instanceof ApiError && error.status === 409;
  function edit(patch: Partial<Fields>) {
    if (!config.data || !fields) return;
    setDraft(previous => ({ baseline: previous?.baseline ?? config.data!, fields: { ...fields, ...patch } })); setError(null); setSavedVersion(null); setLatest(null);
  }
  async function save() {
    if (!draft || saving.current || !draft.fields.name.trim() || !draft.fields.summary.trim()) return;
    saving.current = true; setPending(true); setError(null); setSavedVersion(null);
    const base = draft.baseline.config;
    try {
      const response = await saveMaxProjectConfig(projectId, {
        ...base, app_name: draft.fields.name.trim(), summary: draft.fields.summary.trim(),
        support: { ...base.support, email: draft.fields.email.trim() || null },
      }, draft.baseline.config_version);
      if (!mounted.current) return;
      await qc.cancelQueries({ queryKey: ["max-config", projectId] });
      qc.setQueryData(["max-config", projectId], response); setDraft(null); setSavedVersion(response.config_version); setLatest(null);
      await delivery.refetch();
    } catch (failure) { if (mounted.current) setError(failure); }
    finally { saving.current = false; if (mounted.current) setPending(false); }
  }
  async function reapply() {
    if (!config.data || draft || saving.current) return;
    saving.current = true; setPending(true); setError(null);
    try {
      const response = await saveMaxProjectConfig(projectId, config.data.config, config.data.config_version);
      if (!mounted.current) return;
      qc.setQueryData(["max-config", projectId], response);
      setSavedVersion(response.config_version);
      await delivery.refetch();
    } catch (failure) { if (mounted.current) setError(failure); }
    finally { saving.current = false; if (mounted.current) setPending(false); }
  }
  async function reload() {
    const response = await config.refetch();
    if (mounted.current && response.data && !response.error) setLatest(response.data);
    else if (mounted.current) setError(response.error);
  }
  return <section className="max-owner-settings" aria-label="Настройки приложения">
    <div className="max-owner-section-heading"><h2>Настройки</h2><p>Название, описание и контакт поддержки. Сохранение не запускает ИИ.</p></div>
    {config.isPending && <p role="status">{config.fetchStatus === "paused" ? "Нет соединения. Подключитесь к интернету, чтобы загрузить настройки." : "Загружаем настройки…"}</p>}
    {config.isError && <div role="alert" className="max-owner-notice"><p>{ownerRequestError(config.error)}</p><Button variant="outline" onClick={() => void config.refetch()}>Повторить загрузку</Button></div>}
    {fields && <form onSubmit={event => { event.preventDefault(); void save(); }}>
      <fieldset disabled={pending}>
        <label>Название приложения<Input aria-label="Название приложения" maxLength={100} required value={fields.name} onChange={event => edit({ name: event.target.value })} /></label>
        <label>Описание приложения<textarea aria-label="Описание приложения" maxLength={20000} required rows={5} value={fields.summary} onChange={event => edit({ summary: event.target.value })} /></label>
        <label>Email поддержки<Input aria-label="Email поддержки" type="email" value={fields.email} onChange={event => edit({ email: event.target.value })} /></label>
      </fieldset>
      {error ? <div role="alert" className="max-owner-error"><p>{conflict ? "Настройки изменились в другой вкладке. Ваш черновик остался в форме. Загрузите актуальную версию перед сохранением." : ownerRequestError(error)}</p>{conflict && <Button type="button" variant="outline" disabled={config.isFetching} onClick={() => void reload()}>Загрузить актуальные настройки</Button>}</div> : null}
      {latest && <div className="max-owner-notice"><p>Актуальные настройки загружены. Перенесите свои название, описание и email поверх них и проверьте перед сохранением.</p><Button type="button" variant="outline" onClick={() => { setDraft(previous => previous ? { ...previous, baseline: latest } : previous); setLatest(null); setError(null); }}>Перенести мои настройки</Button></div>}
      {config.data && <div role="status" className="max-owner-notice">
        <p>Настройки сохранены в Yleum. Версия {savedVersion ?? config.data.config_version}.</p>
        {(["public", "preview"] as const).map(target => {
          const status = delivery.data?.[target];
          const version = savedVersion ?? config.data!.config_version;
          const state = delivery.data?.config_version === version && status?.desired_config_version === version &&
            (status.state !== "applied" || status.confirmed_config_version === version) ? status.state : "unknown";
          const copy: Record<MaxContentDelivery["state"], string> = {applied:"Применение подтверждено",pending:"Применяются",unknown:"Применение не подтверждено",not_published:"Ещё не опубликовано",blocked:"Применение приостановлено",deferred:"Применятся после пробуждения",not_created:"Ещё не создан"};
          return <p key={target}>{target === "public" ? "Опубликованное приложение" : "Предпросмотр"}: {copy[state]}</p>;
        })}
        <Button type="button" variant="outline" disabled={Boolean(draft) || pending} onClick={() => void reapply()}>Повторить применение</Button>
      </div>}
      <div className="max-owner-actions"><Button type="submit" aria-busy={pending} disabled={!draft || pending || !fields.name.trim() || !fields.summary.trim()}>Сохранить настройки</Button><Button type="button" variant="outline" disabled={!draft || pending} onClick={() => { setDraft(null); setError(null); setLatest(null); }}>Отменить правки</Button></div>
    </form>}
  </section>;
}
