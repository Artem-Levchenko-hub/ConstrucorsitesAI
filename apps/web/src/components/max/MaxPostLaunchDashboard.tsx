"use client";

import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  Bot,
  Check,
  CircleAlert,
  Clock3,
  Cloud,
  ExternalLink,
  GitCommitHorizontal,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  Webhook,
} from "lucide-react";

import { MaxSectionShell } from "@/components/max/MaxSectionShell";
import { RuntimeButton } from "@/components/workspace/RuntimeButton";
import { Button } from "@/components/ui/button";
import { getMaxIntegration } from "@/lib/api/max-integration";
import { getMaxReadiness } from "@/lib/api/max-studio";
import { getDeployHistory, getLastDeploy, getRuntime } from "@/lib/api/runtime";
import { getMaxPublicationState } from "@/lib/max-publication-state";

export function MaxPostLaunchDashboard({ projectId, projectName }: { projectId: string; projectName: string }) {
  const runtime = useQuery({ queryKey: ["runtime", projectId], queryFn: () => getRuntime(projectId), retry: false });
  const deploy = useQuery({ queryKey: ["deploy", projectId], queryFn: () => getLastDeploy(projectId), retry: false });
  const readiness = useQuery({ queryKey: ["max-readiness", projectId], queryFn: () => getMaxReadiness(projectId), retry: false, refetchInterval: 10_000 });
  const history = useQuery({ queryKey: ["deploy-history", projectId], queryFn: () => getDeployHistory(projectId), retry: false });
  const integration = useQuery({ queryKey: ["max-integration", projectId], queryFn: () => getMaxIntegration(projectId), retry: false });

  const publicationState = getMaxPublicationState(readiness.data, deploy.data?.phase);
  const healthy = publicationState === "published";
  const statusLabel = healthy
    ? "Текущая версия опубликована"
    : publicationState === "checking"
      ? "Проверяем публикацию"
      : "Текущая версия не опубликована";
  const url = healthy ? deploy.data?.prod_url ?? integration.data?.app_url : null;

  return (
    <MaxSectionShell
      projectId={projectId}
      projectName={projectName}
      active="dashboard"
      eyebrow="Приложение запущено"
      title="После запуска"
      lead="Production-состояние, URL, контейнер, MAX webhook и история версий. Данные обновляются с сервера — этот экран не имитирует готовность локальными флагами."
    >
      <section className="mt-8 rounded-[12px] border border-[#d8d4cb] bg-[#fcfbf7]">
        <div className="flex flex-col gap-5 border-b border-[#d8d4cb] p-5 sm:p-6 lg:flex-row lg:items-center lg:justify-between">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className={`size-2 rounded-full ${healthy ? "bg-[#248a4b]" : "bg-[#e8c547]"}`} />
              <span className={`text-xs font-medium ${healthy ? "text-[#248a4b]" : "text-[#745f16]"}`}>{statusLabel}</span>
            </div>
            <h2 className="mt-3 text-2xl font-semibold">{projectName}</h2>
            {url ? (
              <a href={url} target="_blank" rel="noreferrer" className="mt-2 flex min-w-0 items-center gap-1.5 font-mono text-xs text-[#c84528]"><span className="truncate">{url}</span><ExternalLink className="size-3 shrink-0" /></a>
            ) : <p className="mt-2 text-xs text-[#8d887f]">Текущая версия появится по постоянному URL после публикации</p>}
          </div>
          <div className="w-full lg:min-w-[280px] lg:w-auto"><RuntimeButton projectId={projectId} display="compact" /></div>
        </div>
        <div className="grid divide-y divide-[#d8d4cb] sm:grid-cols-4 sm:divide-x sm:divide-y-0">
          {[
            ["Состояние", runtime.data?.state ?? "—"],
            ["Версия", deploy.data?.image_tag?.split(":").at(-1)?.slice(0, 12) ?? "—"],
            ["Цель", deploy.data?.target_label ?? "Omnia"],
            ["Последний релиз", deploy.data?.finished_at ? new Date(deploy.data.finished_at).toLocaleString("ru-RU") : "—"],
          ].map(([label, value]) => (
            <div key={label} className="p-5"><p className="omnia-kicker text-[#aaa59b]">{label}</p><p className="mt-2 truncate text-sm font-semibold">{value}</p></div>
          ))}
        </div>
      </section>

      <section className="mt-5 grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        {[
          [Cloud, "Контейнер", runtime.data?.state === "running", runtime.data?.state ?? "Нет данных"],
          [Activity, "Health-check", healthy, healthy ? "Отвечает" : "Нужна проверка"],
          [Bot, "MAX-бот", Boolean(integration.data?.connected), integration.data?.bot_name ?? "Не подключён"],
          [Webhook, "Webhook", integration.data?.status === "active", integration.data?.status === "active" ? "Активен" : "Не активирован"],
        ].map(([Icon, title, ok, copy]) => {
          const ItemIcon = Icon as typeof Cloud;
          return (
            <article key={String(title)} className="rounded-[12px] border border-[#d8d4cb] bg-[#fcfbf7] p-5">
              <div className="flex items-center justify-between">
                <span className="grid size-9 place-items-center rounded-[8px] bg-[#ece8df]"><ItemIcon className="size-4 text-[#f15a38]" /></span>
                {ok ? <Check className="size-4 text-[#248a4b]" /> : <CircleAlert className="size-4 text-[#e8a127]" />}
              </div>
              <h3 className="mt-5 text-sm font-semibold">{String(title)}</h3>
              <p className="mt-1 truncate text-xs text-[#8d887f]">{String(copy)}</p>
            </article>
          );
        })}
      </section>

      <section className="mt-5 grid gap-5 lg:grid-cols-[1.2fr_.8fr]">
        <div className="overflow-hidden rounded-[12px] border border-[#d8d4cb] bg-[#fcfbf7]">
          <div className="flex items-center justify-between border-b border-[#d8d4cb] p-5">
            <div><p className="omnia-kicker text-[#8d887f]">Versions</p><h2 className="mt-1 text-lg font-semibold">История публикаций</h2></div>
            <Button variant="outline" size="sm" onClick={() => history.refetch()}><RefreshCw className="size-3.5" />Обновить</Button>
          </div>
          <div className="divide-y divide-[#e7e3da]">
            {(history.data ?? []).slice(0, 8).map((item, index) => (
              <div key={item.run_id ?? `${item.started_at}-${index}`} className="grid gap-3 p-5 lg:grid-cols-[minmax(0,1fr)_130px_110px] lg:items-center">
                <div className="flex min-w-0 items-center gap-3">
                  <span className={`grid size-8 place-items-center rounded-full ${item.phase === "done" ? "bg-[#248a4b]/10 text-[#248a4b]" : "bg-[#c63d35]/10 text-[#c63d35]"}`}><GitCommitHorizontal className="size-4" /></span>
                  <div className="min-w-0"><p className="truncate text-sm font-medium">{item.image_tag?.split(":").at(-1) ?? "Production build"}</p><p className="mt-1 truncate text-[10px] text-[#8d887f]">{item.detail ?? item.target_label ?? "Omnia"}</p></div>
                </div>
                <span className="text-xs text-[#6d6962]">{item.finished_at ? new Date(item.finished_at).toLocaleDateString("ru-RU") : "в процессе"}</span>
                <span className={`text-xs font-medium ${item.phase === "done" ? "text-[#248a4b]" : item.phase === "failed" ? "text-[#c63d35]" : "text-[#745f16]"}`}>{item.phase}</span>
              </div>
            ))}
            {!history.isLoading && (history.data ?? []).length === 0 && <p className="p-8 text-center text-sm text-[#8d887f]">История появится после первой публикации.</p>}
          </div>
        </div>

        <aside className="rounded-[12px] border border-[#d8d4cb] bg-[#fcfbf7] p-6">
          <p className="omnia-kicker text-[#8d887f]">Эксплуатация</p>
          <h2 className="mt-2 text-lg font-semibold">Что доступно без разработчика</h2>
          <div className="mt-6 space-y-5 text-sm">
            {[
              [Clock3, "Всегда активный контейнер", "Включается рядом со статусом runtime."],
              [ShieldCheck, "Health-check после релиза", "Трафик переключается только после успешной проверки."],
              [RotateCcw, "Версии и откат", "Неудачную публикацию можно повторить или вернуть предыдущую."],
            ].map(([Icon, title, copy]) => {
              const ItemIcon = Icon as typeof Clock3;
              return <div key={String(title)} className="flex gap-3"><ItemIcon className="mt-0.5 size-4 shrink-0 text-[#f15a38]" /><div><p className="font-medium">{String(title)}</p><p className="mt-1 text-xs leading-5 text-[#8d887f]">{String(copy)}</p></div></div>;
            })}
          </div>
        </aside>
      </section>
    </MaxSectionShell>
  );
}
