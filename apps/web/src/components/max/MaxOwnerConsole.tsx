"use client";

import { useCallback, useRef, useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { enableMaxManagement, getMaxManagement, ownerRequestError, type ResourceId } from "@/lib/api/max-management";
import { getMaxProjectConfig } from "@/lib/api/max-studio";
import { MaxSectionShell } from "./MaxSectionShell";
import { MaxOwnerCatalog } from "./MaxOwnerCatalog";
import { MaxOwnerAnalytics } from "./MaxOwnerAnalytics";
import { MaxOwnerRecords } from "./MaxOwnerRecords";
import { MaxOwnerSettings } from "./MaxOwnerSettings";
import "./max-owner-console.css";

type Section = ResourceId | "catalog" | "settings" | "statistics";
const stateCopy = {
  not_published: "Обращения клиентов появятся после публикации приложения. Пока можно подготовить каталог и настройки.",
  unsupported: "Опубликованное приложение пока не поддерживает управление обращениями. Для подключения нужна совместимая доработка и публикация; существующие данные сохранены.",
  unavailable: "Рабочее приложение сейчас недоступно. Обращения не удалось загрузить. Повторите попытку позже.",
};
export function MaxOwnerConsole(props: { projectId: string; projectName: string }) {
  return <OwnerConsole key={props.projectId} {...props} />;
}
function OwnerConsole({ projectId, projectName }: { projectId: string; projectName: string }) {
  const [selected, setSelected] = useState<Section | null>(null);
  const [dirty, setDirty] = useState(false);
  const dirtyRef = useRef(false);
  const queryClient = useQueryClient();
  const onDirtyChange = useCallback((value: boolean) => {
    dirtyRef.current = value; setDirty(value);
  }, []);
  const enable = useMutation({
    mutationFn: async () => {
      if (dirtyRef.current) throw new Error("unsaved_owner_draft");
      const latest = await getMaxProjectConfig(projectId);
      if (dirtyRef.current) throw new Error("unsaved_owner_draft");
      return enableMaxManagement(projectId, latest.config_version);
    },
    retry: false,
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["max-config", projectId] }),
        queryClient.invalidateQueries({ queryKey: ["max-management", projectId] }),
      ]);
    },
  });
  const overview = useQuery({ queryKey: ["max-management", projectId], queryFn: ({ signal }) => getMaxManagement(projectId, signal), retry: false, refetchOnWindowFocus: false });
  const configuredCatalog = overview.data?.configured_modules.includes("catalog") || overview.data?.active_modules.includes("catalog");
  const legacy = useQuery({ queryKey: ["max-config", projectId], queryFn: () => getMaxProjectConfig(projectId), enabled: overview.isSuccess && !configuredCatalog, retry: false, refetchOnWindowFocus: false });
  const hasCatalog = configuredCatalog || Boolean(legacy.data && legacy.data.config.management_modules == null && (legacy.data.config.content.length || ["catalog", "booking"].includes(legacy.data.config.app_type)));
  const data = overview.data;
  const managementPrepared = Boolean(data?.configured_modules.some(module => module !== "catalog")) || enable.isSuccess;
  const resources = data?.state === "active" ? data.resources.filter(resource => data.active_modules.includes(resource.id)) : [];
  const sections: { id: Section; label: string }[] = [
    ...(hasCatalog ? [{ id: "catalog" as const, label: "Каталог" }] : []),
    ...resources.map(resource => ({ id: resource.id, label: resource.label })),
    { id: "settings", label: "Настройки" }, { id: "statistics", label: "Статистика" },
  ];
  const active = sections.some(section => section.id === selected) ? selected : sections[0]?.id;
  const resource = resources.find(item => item.id === active);
  function confirmDiscard() {
    return !dirtyRef.current || window.confirm("Есть несохранённые правки. Отменить их и открыть другой раздел?");
  }
  function select(section: Section) {
    if (section === active || !confirmDiscard()) return;
    onDirtyChange(false); setSelected(section);
  }
  function prepareManagement() {
    if (dirtyRef.current || enable.isPending) return;
    // Adding the Catalog section must not replace a currently open Settings
    // form, including a draft started after the enable request was sent.
    setSelected(active ?? "settings");
    enable.mutate();
  }
  return <MaxSectionShell projectId={projectId} projectName={projectName} active="manage" eyebrow="Ежедневная работа" title="Управление" lead="Меняйте данные и работайте с обращениями прямо в Yleum.">
    <div className="max-owner-console">
      {overview.isPending && <p role="status" className="max-owner-notice">{overview.fetchStatus === "paused" ? "Нет соединения. Подключитесь к интернету, чтобы открыть управление." : "Загружаем разделы управления…"}</p>}
      {overview.isError && <div role="alert" className="max-owner-notice"><p>{ownerRequestError(overview.error)}</p><Button variant="outline" onClick={() => void overview.refetch()}>Повторить загрузку</Button></div>}
      {overview.data && overview.data.state !== "active" && <div role="status" className="max-owner-notice">
        <p>{stateCopy[overview.data.state]}</p>
        {overview.data.state === "unavailable" && <Button variant="outline" onClick={() => void overview.refetch()}>Повторить загрузку</Button>}
        {overview.data.state === "unsupported" && (managementPrepared ? <>
          <p>Подготовлено к доработке. Откройте редактор, затем опубликуйте совместимую версию.</p>
          <Link href={`/max/${encodeURIComponent(projectId)}`} onClick={event => { if (!confirmDiscard()) event.preventDefault(); }}>Открыть редактор</Link>
        </> : <>
          <p>Подключение сохранит настройки управления. Доработка и публикация запускаются отдельно.</p>
          {dirty && <p>Сохраните или отмените правки перед подключением.</p>}
          {enable.isError && <p role="alert">{ownerRequestError(enable.error)}</p>}
          {enable.isPaused && <p>Нет соединения. Подключение продолжится после восстановления связи.</p>}
          <Button variant="outline" disabled={dirty || enable.isPending} onClick={prepareManagement}>
            {enable.isPending ? "Подключаем…" : "Подключить управление"}
          </Button>
        </>)}
      </div>}
      <nav className="max-owner-tabs" aria-label="Разделы управления">{sections.map(section => <Button key={section.id} variant={active === section.id ? "primary" : "outline"} aria-current={active === section.id ? "page" : undefined} onClick={() => select(section.id)}>{section.label}</Button>)}</nav>
      {active === "catalog" && <><p className="max-owner-hint">До 100 позиций каталога, включая скрытые.</p><MaxOwnerCatalog projectId={projectId} projectName={projectName} embedded onDirtyChange={onDirtyChange} /></>}
      {resource && <MaxOwnerRecords key={resource.id} projectId={projectId} resource={resource} onDirtyChange={onDirtyChange} />}
      {!overview.isPending && active === "settings" && <MaxOwnerSettings projectId={projectId} onDirtyChange={onDirtyChange} />}
      {active === "statistics" && <MaxOwnerAnalytics projectId={projectId} />}
    </div>
  </MaxSectionShell>;
}
