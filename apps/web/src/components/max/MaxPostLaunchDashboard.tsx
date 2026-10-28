"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight, Check, ChevronDown, CircleAlert, ExternalLink, Loader2, RefreshCw } from "lucide-react";
import Link from "next/link";
import { MaxOwnerAnalytics } from "@/components/max/MaxOwnerAnalytics";
import { MaxSectionShell } from "@/components/max/MaxSectionShell";
import { Button } from "@/components/ui/button";
import { getMaxIntegration } from "@/lib/api/max-integration";
import { getMaxReadiness } from "@/lib/api/max-studio";
import { getLastDeploy, getRuntime } from "@/lib/api/runtime";
import type { DeployPhase, RuntimeState } from "@/lib/api/types";
import { getMaxPublicationState } from "@/lib/max-publication-state";
import { isMaxDeployActive } from "@/lib/max-launch-state";
import { relativeDateLabel } from "@/lib/relative-date";
import { MAX_STATUS_COPY, MAX_UNKNOWN_LABEL } from "@/lib/max-status-copy";
import "./max-dashboard.css";

const phaseLabels: Record<DeployPhase, string> = {
  idle: "Ещё не запускалась", queued: "В очереди", building: "Сборка", pushing: "Передача сборки",
  swapping: "Проверка и переключение", cancelling: "Остановка", cancelled: "Отменена", done: "Опубликовано", failed: "Ошибка",
};
const runtimeLabels: Record<RuntimeState, string> = { running: "Запущена", provisioning: "Готовится", paused: "Приостановлена", stopped: "Остановлена", failed: "Ошибка запуска" };

export function MaxPostLaunchDashboard({ projectId, projectName }: { projectId: string; projectName: string }) {
  const runtime = useQuery({ queryKey: ["runtime", projectId], queryFn: () => getRuntime(projectId), retry: false });
  const deploy = useQuery({ queryKey: ["deploy", projectId], queryFn: () => getLastDeploy(projectId), retry: false,
    refetchInterval: query => isMaxDeployActive(query.state.data?.phase ?? "idle", query.state.data?.run_id) ? 1_500 : false });
  const readiness = useQuery({ queryKey: ["max-readiness", projectId], queryFn: () => getMaxReadiness(projectId), retry: false, refetchInterval: 10_000 });
  const integration = useQuery({ queryKey: ["max-integration", projectId], queryFn: () => getMaxIntegration(projectId), retry: false });
  const statusError = readiness.isError || deploy.isError;
  const publicationState = getMaxPublicationState(readiness.isSuccess ? readiness.data : undefined, deploy.data?.phase);
  const latest = deploy.isSuccess ? deploy.data : undefined;
  const hasActiveProjection = latest?.active_publication !== undefined;
  const activePublication = !statusError ? latest?.active_publication : null;
  const currentPublished = readiness.isSuccess && readiness.data.items.some(item => item.id === "publish" && item.done);
  const published = !statusError && (hasActiveProjection
    ? Boolean(activePublication && currentPublished)
    : publicationState === "published");
  const active = !deploy.isError && isMaxDeployActive(deploy.data?.phase ?? "idle", deploy.data?.run_id);
  const statusLabel = statusError ? MAX_STATUS_COPY.publication.title
    : readiness.isPending || deploy.isPending ? "Проверяем публикацию"
    : published ? "Приложение опубликовано" : "Изменения не опубликованы";
  const url = hasActiveProjection ? activePublication?.prod_url
    : published ? latest?.prod_url ?? (integration.isSuccess ? integration.data?.app_url : null) : null;
  const integrationLabel = integration.isError ? MAX_UNKNOWN_LABEL : integration.isPending ? "Проверяем…" : integration.data?.connected ? integration.data.bot_name ?? "Подключён" : "Не подключён";

  const everPublished = hasActiveProjection ? Boolean(activePublication) : Boolean(latest && latest.phase !== "idle");
  const releaseName = hasActiveProjection ? "Ваше приложение" : latest
    ? latest.phase === "done" ? "Ваше приложение" : phaseLabels[latest.phase]
    : MAX_UNKNOWN_LABEL;
  const releaseFinishedAt = hasActiveProjection ? activePublication?.finished_at : latest?.finished_at;
  const releaseStartedAt = hasActiveProjection ? null : latest?.started_at;
  const releaseWhen = releaseFinishedAt
    ? relativeDateLabel(releaseFinishedAt) ?? new Date(releaseFinishedAt).toLocaleString("ru-RU")
    : releaseStartedAt
      ? `начата ${relativeDateLabel(releaseStartedAt) ?? new Date(releaseStartedAt).toLocaleString("ru-RU")}`
      : "Время неизвестно";

  function refreshStatus() { void readiness.refetch(); void deploy.refetch(); void runtime.refetch(); void integration.refetch(); }

  return (
    <MaxSectionShell projectId={projectId} projectName={projectName} active="dashboard" eyebrow={projectName} title="Статистика" lead="Пользователи и действия в приложении">
      <MaxOwnerAnalytics projectId={projectId} />
      <section className="max-dashboard-operations" aria-label="Публикация и подключения">
        <section className="max-dashboard-project" aria-label="Ваше приложение">
          <div className="max-dashboard-status-line">
          <p className={statusError ? "text-danger-fg" : published ? "text-success-fg" : "text-fg-secondary"} role="status">{published ? <Check className="size-4" /> : statusError ? <CircleAlert className="size-4" /> : <Loader2 className={`size-4 ${readiness.isPending || deploy.isPending ? "animate-spin" : ""}`} />}{statusLabel}</p>
          <div className="max-dashboard-actions">
            {url ? <Button asChild><a href={url} target="_blank" rel="noreferrer">Открыть приложение <ArrowUpRight className="size-4" /></a></Button>
              : <Button asChild><Link href={`/max/${projectId}?panel=publish`}>{active ? "Ход публикации" : "Подготовить запуск"}</Link></Button>}
            <Button asChild variant="outline"><Link href={`/max/${projectId}`}>Редактировать</Link></Button>
          </div>
          </div>
          {statusError && <p className="max-dashboard-status-hint">{MAX_STATUS_COPY.publication.hint}</p>}
          {!statusError && (hasActiveProjection ? activePublication && readiness.isSuccess && !currentPublished : publicationState === "outdated") && <p className="max-launch-notice">Есть неопубликованные изменения.</p>}
          {active && <p className="max-launch-notice" role="status">{phaseLabels[deploy.data!.phase]} — публикация выполняется на сервере.</p>}
          {latest?.phase === "failed" && <div role="alert" className="max-dashboard-error"><strong>Последняя публикация не завершилась</strong><p>{latest.error ?? "Проверьте готовность и повторите попытку."}</p></div>}
          {statusError && <Button variant="outline" className="mt-3" onClick={refreshStatus}>{MAX_STATUS_COPY.publication.retry}</Button>}
        </section>
        <details className="max-dashboard-inspect">
          <summary>Публикация и подключения<ChevronDown className="size-4" aria-hidden="true" /></summary>
          <div className="max-dashboard-overview">
        <section className="max-dashboard-release" aria-labelledby="max-release-heading">
          <h2 id="max-release-heading">Опубликованная версия</h2>
          {!everPublished ? (
            <p className="max-dashboard-empty">
              {statusError
                ? "Не дозвонились до сервера — состояние публикации покажем, когда он ответит."
                : deploy.isPending
                  ? "Проверяем, была ли публикация…"
                  : hasActiveProjection
                    ? "Опубликованная версия не подтверждена. Адрес появится после успешной публикации."
                    : "Публикаций ещё не было."}
            </p>
          ) : (
            <dl>
              <div><dt>Что опубликовано</dt><dd>{releaseName}</dd></div>
              <div><dt>Где открывается</dt><dd>{url
                ? <a className="max-dashboard-url" href={url} target="_blank" rel="noreferrer">{url}<ExternalLink className="size-4 shrink-0" /></a>
                : "Адрес появится после успешной публикации"}</dd></div>
              <div><dt>Когда</dt><dd>{releaseWhen}</dd></div>
            </dl>
          )}
          <p className="max-dashboard-monitoring-note">Приложение проверяется при каждой публикации. Постоянного наблюдения за доступностью пока нет: если приложение перестанет открываться между публикациями, мы не узнаем об этом сами — напишите нам.</p>
        </section>
      <section className="max-dashboard-system" aria-labelledby="max-system-heading">
        <header><h2 id="max-system-heading">Подключения</h2><Button variant="outline" aria-label="Обновить подключения" onClick={refreshStatus} disabled={runtime.isFetching || integration.isFetching || readiness.isFetching || deploy.isFetching}><RefreshCw className="size-4" /></Button></header>
        <div className="max-dashboard-system-row"><div><h3>Рабочая среда редактора</h3><p>В ней открывается живое превью, пока вы правите приложение. На опубликованное приложение у пользователей она не влияет.</p></div>
          <div className="max-dashboard-runtime">{runtime.isError ? <p className="text-danger-fg">{MAX_UNKNOWN_LABEL}</p> : runtime.isPending ? <p>Проверяем…</p> : <p>{runtime.data ? runtimeLabels[runtime.data.state] : "Нет данных"}</p>}</div>
        </div>
        <div className="max-dashboard-system-row"><div><h3>Безопасный вход MAX</h3><p>Подключение бота для входа пользователей</p></div><span className={integration.isError ? "text-danger-fg" : "text-fg-secondary"}>{integrationLabel}</span><Button asChild variant="outline" size="sm"><Link href={`/max/${projectId}?panel=max`}>Настроить MAX</Link></Button></div>
        <div className="max-dashboard-system-row"><div><h3>Связь с MAX</h3></div><span className={integration.isError ? "text-danger-fg" : "text-fg-secondary"}>{integration.isError ? MAX_UNKNOWN_LABEL : integration.isPending ? "Проверяем…" : integration.data?.status === "active" ? "Подключена" : "Не активна"}</span></div>
      </section>
          </div>
          <Link className="max-dashboard-settings-link" href={`/max/${projectId}?data=details`}>Данные и настройки<ArrowUpRight className="size-4" /></Link>
        </details>
      </section>
    </MaxSectionShell>
  );
}
