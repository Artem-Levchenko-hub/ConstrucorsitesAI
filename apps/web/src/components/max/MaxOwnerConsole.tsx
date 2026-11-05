"use client";

import { useCallback, useRef, useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarDays, CircleAlert, Inbox, Loader2, PackageOpen, Settings2, ShoppingBag } from "lucide-react";
import { Button } from "@/components/ui/button";
import { enableMaxManagement, getMaxManagement, ownerRequestError, type ResourceId } from "@/lib/api/max-management";
import { getMaxProjectConfig } from "@/lib/api/max-studio";
import { MaxSectionShell } from "./MaxSectionShell";
import { MaxOwnerCatalog } from "./MaxOwnerCatalog";
import { MaxOwnerRecords } from "./MaxOwnerRecords";
import { MaxOwnerSettings } from "./MaxOwnerSettings";
import { useMaxWorkspaceDraftGuard } from "./use-max-workspace-draft-guard";
import "./max-owner-console.css";

type Section = ResourceId | "catalog" | "settings";
const sectionIcon = {
  catalog: PackageOpen,
  orders: ShoppingBag,
  leads: Inbox,
  bookings: CalendarDays,
  settings: Settings2,
};
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
  useMaxWorkspaceDraftGuard(dirty);
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
    { id: "settings", label: "Настройки" },
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
      {overview.isPending && <div role="status" className="max-owner-notice max-owner-loading">
        <Loader2 aria-hidden="true" className="size-5 motion-safe:animate-spin" />
        <p>{overview.fetchStatus === "paused" ? "Нет соединения. Подключитесь к интернету, чтобы открыть управление." : "Загружаем разделы управления…"}</p>
      </div>}
      {overview.isError && <div role="alert" className="max-owner-notice" data-tone="error">
        <strong><CircleAlert aria-hidden="true" className="size-4" />Не удалось открыть управление</strong>
        <p>{ownerRequestError(overview.error)}</p>
        <Button variant="outline" onClick={() => void overview.refetch()}>Повторить загрузку</Button>
      </div>}
      {overview.data && overview.data.state !== "active" && <div role="status" className="max-owner-notice">
        <p>{stateCopy[overview.data.state]}</p>
        {overview.data.state === "unavailable" && <Button variant="outline" onClick={() => void overview.refetch()}>Повторить загрузку</Button>}
        {overview.data.state === "unsupported" && (managementPrepared ? <>
          <p>Подготовлено к доработке. Откройте редактор, затем опубликуйте совместимую версию.</p>
          <Button asChild variant="outline"><Link href={`/max/${encodeURIComponent(projectId)}`}>Открыть редактор</Link></Button>
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
      {!overview.isPending && <div className="max-owner-workbench">
        <aside className="max-owner-rail">
          <p className="max-owner-rail-label">Рабочие разделы</p>
          <nav className="max-owner-tabs" aria-label="Разделы управления">
            {sections.map(section => {
              const Icon = sectionIcon[section.id];
              return <Button key={section.id} variant="ghost"
                aria-current={active === section.id ? "page" : undefined} onClick={() => select(section.id)}>
                <Icon aria-hidden="true" className="size-4" />{section.label}
              </Button>;
            })}
          </nav>
        </aside>
        <div className="max-owner-workbench-content">
          {active === "catalog" && <section aria-label="Каталог">
            <header className="max-owner-section-heading"><h2>Каталог</h2><p>До 100 позиций каталога, включая скрытые.</p></header>
            <MaxOwnerCatalog projectId={projectId} projectName={projectName} embedded onDirtyChange={onDirtyChange} />
          </section>}
          {resource && <MaxOwnerRecords key={resource.id} projectId={projectId} resource={resource} onDirtyChange={onDirtyChange} />}
          {active === "settings" && <MaxOwnerSettings projectId={projectId} onDirtyChange={onDirtyChange} />}
        </div>
      </div>}
    </div>
  </MaxSectionShell>;
}
