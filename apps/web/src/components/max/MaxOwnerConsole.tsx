"use client";

import { useCallback, useRef, useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarDays, CircleAlert, History, Inbox, Loader2, ShoppingBag, UsersRound } from "lucide-react";
import { Button } from "@/components/ui/button";
import { enableMaxManagement, getMaxManagement, ownerRequestError, type ResourceDefinition, type ResourceId } from "@/lib/api/max-management";
import { getMaxTeam } from "@/lib/api/max-staff";
import { getMaxProjectConfig } from "@/lib/api/max-studio";
import { MaxSectionShell } from "./MaxSectionShell";
import { MaxOwnerRecords } from "./MaxOwnerRecords";
import { MaxStaffQueue } from "./MaxStaffQueue";
import { MaxStaffTeam } from "./MaxStaffTeam";
import { useMaxWorkspaceDraftGuard } from "./use-max-workspace-draft-guard";
import "./max-owner-console.css";

type Section = ResourceId | "team";
const sectionIcon = {
  orders: ShoppingBag,
  leads: Inbox,
  bookings: CalendarDays,
  team: UsersRound,
};
const stateCopy = {
  not_published: "Обращения клиентов появятся после публикации приложения. Пока можно подготовить каталог и настройки.",
  unsupported: "Опубликованное приложение пока не поддерживает управление обращениями. Для подключения нужна совместимая доработка и публикация; существующие данные сохранены.",
  unavailable: "Рабочее приложение сейчас недоступно. Обращения не удалось загрузить. Повторите попытку позже.",
};
type OwnerConsoleProps = { projectId: string; projectName: string; initialTeam?: boolean };

export function MaxOwnerConsole(props: OwnerConsoleProps) {
  return <OwnerConsole key={props.projectId} {...props} />;
}
function OwnerConsole({ projectId, projectName, initialTeam }: OwnerConsoleProps) {
  const [selected, setSelected] = useState<Section | null>(initialTeam ? "team" : null);
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
  const team = useQuery({ queryKey: ["max-team", projectId], queryFn: ({ signal }) => getMaxTeam(projectId, signal), retry: false, staleTime: 0, gcTime: 0 });
  const data = overview.data;
  const managementPrepared = Boolean(data?.configured_modules.some(module => module !== "catalog")) || enable.isSuccess;
  const resources = data?.state === "active" ? data.resources.filter(resource => data.active_modules.includes(resource.id)) : [];
  const sections: { id: Section; label: string }[] = [
    ...resources.map(resource => ({ id: resource.id, label: resource.label })),
    { id: "team", label: "Команда" },
  ];
  const active = sections.some(section => section.id === selected) ? selected : overview.isPending ? null : sections[0].id;
  const resource = resources.find(item => item.id === active);
  const needsTeamSettings = resource?.id === "orders" || resource?.id === "leads";
  const teamSettings = team.data?.settings;
  const staffResource = resource?.id === "orders" && teamSettings?.orders_mode ? "orders"
    : resource?.id === "leads" && teamSettings?.leads_enabled ? "leads" : null;
  function confirmDiscard() {
    return !dirtyRef.current || window.confirm("Есть несохранённые правки. Отменить их и открыть другой раздел?");
  }
  function select(section: Section) {
    if (section === active || !confirmDiscard()) return;
    onDirtyChange(false); setSelected(section);
  }
  function prepareManagement() {
    if (dirtyRef.current || enable.isPending) return;
    // Keep the current section when the available sections refresh after enabling.
    setSelected(active ?? "team");
    enable.mutate();
  }
  return <MaxSectionShell projectId={projectId} projectName={projectName} active="manage" eyebrow="Ежедневная работа" title="Управление"
    lead="Работайте с обращениями и управляйте доступом сотрудников." learningContext={active === "team" || staffResource ? "staff-team" : undefined}>
    <div className="max-owner-console">
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
      <div className="max-owner-workbench">
        <div className="max-owner-rail">
          <nav className="max-owner-tabs" aria-label="Разделы управления">
            {sections.map(section => {
              const Icon = sectionIcon[section.id];
              return <Button key={section.id} variant="ghost"
                aria-current={active === section.id ? "page" : undefined} onClick={() => select(section.id)}>
                <Icon aria-hidden="true" className="size-4" />{section.label}
              </Button>;
            })}
          </nav>
        </div>
        <div className="max-owner-workbench-content">
          {overview.isPending && active !== "team" && <div role="status" className="max-owner-notice max-owner-loading">
            <Loader2 aria-hidden="true" className="size-5 motion-safe:animate-spin" />
            <p>{overview.fetchStatus === "paused" ? "Нет соединения. Подключитесь к интернету, чтобы открыть управление." : "Загружаем разделы управления…"}</p>
          </div>}
          {needsTeamSettings && team.isPending && <div role="status" className="max-owner-notice max-owner-loading">
            <Loader2 aria-hidden="true" className="size-5 motion-safe:animate-spin" /><p>Проверяем настройки очереди…</p>
          </div>}
          {needsTeamSettings && team.isError && !teamSettings && <div role="alert" className="max-owner-notice" data-tone="error">
            <p>Не удалось проверить настройки очереди.</p><Button variant="outline" onClick={() => void team.refetch()}>Повторить загрузку</Button>
          </div>}
          {resource && (!needsTeamSettings || team.isSuccess || teamSettings) && (staffResource ? <>
            <MaxStaffQueue key={staffResource} projectId={projectId} resource={staffResource} members={team.data?.members} onDirtyChange={onDirtyChange} />
            <LegacyRecords key={`legacy-${resource.id}`} projectId={projectId} resource={resource} />
          </> : <MaxOwnerRecords key={resource.id} projectId={projectId} resource={resource} onDirtyChange={onDirtyChange} />)}
          {active === "team" && <MaxStaffTeam projectId={projectId} team={team} onDirtyChange={onDirtyChange} />}
        </div>
      </div>
    </div>
  </MaxSectionShell>;
}

function LegacyRecords({ projectId, resource }: { projectId: string; resource: ResourceDefinition }) {
  const [open, setOpen] = useState(false);
  return <details className="max-staff-history max-staff-legacy" onToggle={event => setOpen(event.currentTarget.open)}>
    <summary><History aria-hidden="true" /> До подключения команды</summary>
    {open && <MaxOwnerRecords projectId={projectId} resource={resource} readOnly />}
  </details>;
}
