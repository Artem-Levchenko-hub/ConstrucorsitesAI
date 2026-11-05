"use client";

import Link from "next/link";
import { ArrowRight, Inbox, ShoppingBag, UsersRound } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { getStaffProjects, staffRequestError } from "@/lib/api/max-staff";
import { MAX_STAFF_PATH, maxStaffProjectPath } from "@/lib/max-staff-path";

export function MaxStaffProjects({ userId }: { userId: string }) {
  const projects = useQuery({
    queryKey: ["max-staff-projects", userId], queryFn: ({ signal }) => getStaffProjects(signal),
    retry: false, staleTime: 0, gcTime: 0, refetchOnWindowFocus: "always",
  });
  return <>
    <header className="max-staff-page-heading"><p className="max-staff-kicker">Кабинет сотрудника</p>
      <h1>Рабочие приложения</h1><p>Выберите приложение, чтобы открыть заказы или заявки.</p></header>
    {projects.isPending ? <p role="status" className="max-staff-notice">Загружаем доступные приложения…</p>
      : projects.isError ? <div role="alert" className="max-staff-notice" data-tone="error">
        <p>{staffRequestError(projects.error)}</p><div className="max-staff-actions">
          <Button variant="outline" onClick={() => void projects.refetch()}>Повторить загрузку</Button>
          <Button asChild variant="ghost"><Link href={`/login?next=${encodeURIComponent(MAX_STAFF_PATH)}`}>Войти в аккаунт</Link></Button>
        </div>
      </div> : !projects.data.length ? <div className="max-staff-empty"><UsersRound aria-hidden="true" />
        <h2>Рабочих приложений пока нет</h2><p>Попросите владельца прислать персональное приглашение.</p>
      </div> : <ul className="max-staff-projects">
        {projects.data.map(project => <li key={project.project_id}>
          <Link href={maxStaffProjectPath(project.project_id)}>
            <div><strong>{project.project_name}</strong><span className="max-staff-project-permissions">
              {project.permissions.includes("orders") && <span><ShoppingBag aria-hidden="true" /> Заказы</span>}
              {project.permissions.includes("leads") && <span><Inbox aria-hidden="true" /> Заявки</span>}
            </span></div><ArrowRight aria-hidden="true" className="size-5 shrink-0" />
          </Link>
        </li>)}
      </ul>}
  </>;
}
