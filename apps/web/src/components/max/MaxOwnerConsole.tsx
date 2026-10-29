"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { getMaxManagement, ownerRequestError, type ResourceId } from "@/lib/api/max-management";
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
  const overview = useQuery({ queryKey: ["max-management", projectId], queryFn: ({ signal }) => getMaxManagement(projectId, signal), retry: false, refetchOnWindowFocus: false });
  const configuredCatalog = overview.data?.configured_modules.includes("catalog") || overview.data?.active_modules.includes("catalog");
  const legacy = useQuery({ queryKey: ["max-config", projectId], queryFn: () => getMaxProjectConfig(projectId), enabled: overview.isSuccess && !configuredCatalog, retry: false, refetchOnWindowFocus: false });
  const hasCatalog = configuredCatalog || Boolean(legacy.data && legacy.data.config.management_modules == null && (legacy.data.config.content.length || ["catalog", "booking"].includes(legacy.data.config.app_type)));
  const data = overview.data;
  const resources = data?.state === "active" ? data.resources.filter(resource => data.active_modules.includes(resource.id)) : [];
  const sections: { id: Section; label: string }[] = [
    ...(hasCatalog ? [{ id: "catalog" as const, label: "Каталог" }] : []),
    ...resources.map(resource => ({ id: resource.id, label: resource.label })),
    { id: "settings", label: "Настройки" }, { id: "statistics", label: "Статистика" },
  ];
  const active = sections.some(section => section.id === selected) ? selected : sections[0]?.id;
  const resource = resources.find(item => item.id === active);
  function select(section: Section) {
    if (section === active || (dirty && !window.confirm("Есть несохранённые правки. Отменить их и открыть другой раздел?"))) return;
    setDirty(false); setSelected(section);
  }
  return <MaxSectionShell projectId={projectId} projectName={projectName} active="manage" eyebrow="Ежедневная работа" title="Управление" lead="Меняйте данные и работайте с обращениями прямо в Yleum.">
    <div className="max-owner-console">
      {overview.isPending && <p role="status" className="max-owner-notice">{overview.fetchStatus === "paused" ? "Нет соединения. Подключитесь к интернету, чтобы открыть управление." : "Загружаем разделы управления…"}</p>}
      {overview.isError && <div role="alert" className="max-owner-notice"><p>{ownerRequestError(overview.error)}</p><Button variant="outline" onClick={() => void overview.refetch()}>Повторить загрузку</Button></div>}
      {overview.data && overview.data.state !== "active" && <div role="status" className="max-owner-notice"><p>{stateCopy[overview.data.state]}</p>{overview.data.state === "unavailable" && <Button variant="outline" onClick={() => void overview.refetch()}>Повторить загрузку</Button>}</div>}
      <nav className="max-owner-tabs" aria-label="Разделы управления">{sections.map(section => <Button key={section.id} variant={active === section.id ? "primary" : "outline"} aria-current={active === section.id ? "page" : undefined} onClick={() => select(section.id)}>{section.label}</Button>)}</nav>
      {active === "catalog" && <><p className="max-owner-hint">До 100 позиций каталога, включая скрытые.</p><MaxOwnerCatalog projectId={projectId} projectName={projectName} embedded onDirtyChange={setDirty} /></>}
      {resource && <MaxOwnerRecords key={resource.id} projectId={projectId} resource={resource} onDirtyChange={setDirty} />}
      {!overview.isPending && active === "settings" && <MaxOwnerSettings projectId={projectId} onDirtyChange={setDirty} />}
      {active === "statistics" && <MaxOwnerAnalytics projectId={projectId} />}
    </div>
  </MaxSectionShell>;
}
