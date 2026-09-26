"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight, Check, CircleAlert, ExternalLink, Loader2, RefreshCw } from "lucide-react";
import Link from "next/link";
import { MaxSectionShell } from "@/components/max/MaxSectionShell";
import { RuntimeButton } from "@/components/workspace/RuntimeButton";
import { Button } from "@/components/ui/button";
import { getMaxIntegration } from "@/lib/api/max-integration";
import { getMaxReadiness } from "@/lib/api/max-studio";
import { getDeployHistory, getLastDeploy, getRuntime } from "@/lib/api/runtime";
import type { DeployPhase, RuntimeState } from "@/lib/api/types";
import { getMaxPublicationState } from "@/lib/max-publication-state";
import { isMaxDeployActive } from "@/lib/max-launch-state";
import { relativeDateLabel } from "@/lib/relative-date";
import { MAX_STATUS_COPY, MAX_UNKNOWN_LABEL } from "@/lib/max-status-copy";

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
  const history = useQuery({ queryKey: ["deploy-history", projectId], queryFn: () => getDeployHistory(projectId), retry: false });
  const integration = useQuery({ queryKey: ["max-integration", projectId], queryFn: () => getMaxIntegration(projectId), retry: false });
  const statusError = readiness.isError || deploy.isError;
  const publicationState = getMaxPublicationState(readiness.isSuccess ? readiness.data : undefined, deploy.data?.phase);
  const published = !statusError && publicationState === "published";
  const active = !deploy.isError && isMaxDeployActive(deploy.data?.phase ?? "idle", deploy.data?.run_id);
  const statusLabel = statusError ? MAX_STATUS_COPY.publication.title
    : readiness.isPending || deploy.isPending ? "Проверяем публикацию"
    : published ? "Текущая версия опубликована" : "Текущая версия не опубликована";
  const url = published ? deploy.data?.prod_url ?? (integration.isSuccess ? integration.data?.app_url : null) : null;
  const latest = deploy.isSuccess ? deploy.data : undefined;
  const integrationLabel = integration.isError ? MAX_UNKNOWN_LABEL : integration.isPending ? "Проверяем…" : integration.data?.connected ? integration.data.bot_name ?? "Подключён" : "Не подключён";

  // «Версия 7 · 17 сентября» вместо служебного тега образа: владельцу нужен
  // ответ «что опубликовано», а не имя сборки.
  const everPublished = Boolean(latest && latest.phase !== "idle");
  const buildTag = latest?.image_tag?.split(":").at(-1) ?? null;
  const releaseName = latest
    ? latest.phase === "done"
      ? buildTag ? `Версия ${buildTag}` : "Текущая версия приложения"
      : phaseLabels[latest.phase]
    : "Нет данных";
  const releaseWhen = latest?.finished_at
    ? relativeDateLabel(latest.finished_at) ?? new Date(latest.finished_at).toLocaleString("ru-RU")
    : latest?.started_at
      ? `начата ${relativeDateLabel(latest.started_at) ?? new Date(latest.started_at).toLocaleString("ru-RU")}`
      : "Время неизвестно";

  function refreshStatus() { void readiness.refetch(); void deploy.refetch(); void runtime.refetch(); void integration.refetch(); }

  return (
    <MaxSectionShell projectId={projectId} projectName={projectName} active="dashboard" eyebrow="Управление" title="Обзор приложения" lead="Публикация, подключения и история изменений вашего приложения.">
      <div className="max-dashboard-overview">
        <section className="max-dashboard-project" aria-label="Ваше приложение">
          <p className={statusError ? "text-danger-fg" : published ? "text-success-fg" : "text-fg-secondary"} role="status">{published ? <Check className="size-4" /> : statusError ? <CircleAlert className="size-4" /> : <Loader2 className={`size-4 ${readiness.isPending || deploy.isPending ? "animate-spin" : ""}`} />}{statusLabel}</p>
          <h2>{projectName}</h2>
          {url ? <a className="max-dashboard-url" href={url} target="_blank" rel="noreferrer">{url}<ExternalLink className="size-4 shrink-0" /></a> : <p className="text-sm text-fg-secondary">{statusError ? MAX_STATUS_COPY.publication.hint : "Постоянный адрес текущей версии появится после публикации."}</p>}
          <div className="max-dashboard-actions">
            {url ? <Button asChild><a href={url} target="_blank" rel="noreferrer">Открыть приложение <ArrowUpRight className="size-4" /></a></Button>
              : <Button asChild><Link href={`/max/${projectId}?panel=publish`}>{active ? "Ход публикации" : "Подготовить запуск"}</Link></Button>}
            <Button asChild variant="outline"><Link href={`/max/${projectId}`}>Редактировать</Link></Button>
          </div>
          {publicationState === "outdated" && !statusError && <p className="max-launch-notice">После последней публикации появились изменения. Проверьте их в редакторе и опубликуйте обновление.</p>}
          {active && <p className="max-launch-notice" role="status">{phaseLabels[deploy.data!.phase]} — публикация выполняется на сервере.</p>}
          {latest?.phase === "failed" && <div role="alert" className="max-dashboard-error"><strong>Последняя публикация не завершилась</strong><p>{latest.error ?? "Проверьте готовность и повторите попытку."}</p></div>}
          {statusError && <Button variant="outline" className="mt-3" onClick={refreshStatus}>{MAX_STATUS_COPY.publication.retry}</Button>}
          <Link className="max-dashboard-settings-link" href={`/max/${projectId}?data=details`}>Данные и настройки приложения <ArrowUpRight className="size-4" /></Link>
        </section>
        <section className="max-dashboard-release" aria-labelledby="max-release-heading">
          <h2 id="max-release-heading">Последняя публикация</h2>
          {/* Раньше здесь стояли прочерки в трёх полях из четырёх: пустой
              прочерк читается как поломка, а не как «этого ещё не было».
              Пока публикаций нет, вместо таблицы — одна честная строка. */}
          {!everPublished ? (
            <p className="max-dashboard-empty">
              {deploy.isError
                ? "Не дозвонились до сервера — состояние публикации покажем, когда он ответит."
                : deploy.isPending
                  ? "Проверяем, была ли публикация…"
                  : "Публикаций ещё не было. Здесь появится, что именно опубликовано и по какому адресу открывается."}
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
      </div>

      <section className="max-dashboard-system" aria-labelledby="max-system-heading">
        <header><div><h2 id="max-system-heading">Состояние и подключения</h2><p>Данные среды разработки и связи с MAX</p></div><Button variant="outline" onClick={refreshStatus} disabled={runtime.isFetching || integration.isFetching || readiness.isFetching || deploy.isFetching}><RefreshCw className="size-4" />Обновить</Button></header>
        <div className="max-dashboard-system-row"><div><h3>Рабочая среда редактора</h3><p>В ней открывается живое превью, пока вы правите приложение. На опубликованную версию у пользователей она не влияет.</p></div>
          <div className="max-dashboard-runtime">{runtime.isError ? <p className="text-danger-fg">{MAX_UNKNOWN_LABEL}</p> : runtime.isPending ? <p>Проверяем…</p> : <><p>{runtime.data ? runtimeLabels[runtime.data.state] : "Нет данных"}</p><RuntimeButton projectId={projectId} display="compact" /></>}</div>
        </div>
        <div className="max-dashboard-system-row"><div><h3>Безопасный вход MAX</h3><p>Подключение бота для входа пользователей</p></div><span className={integration.isError ? "text-danger-fg" : "text-fg-secondary"}>{integrationLabel}</span><Button asChild variant="outline" size="sm"><Link href={`/max/${projectId}?panel=max`}>Настроить MAX</Link></Button></div>
        <div className="max-dashboard-system-row"><div><h3>Связь с MAX</h3><p>Через неё MAX сообщает приложению о событиях: новых пользователях, нажатиях, сообщениях боту</p></div><span className={integration.isError ? "text-danger-fg" : "text-fg-secondary"}>{integration.isError ? MAX_UNKNOWN_LABEL : integration.isPending ? "Проверяем…" : integration.data?.status === "active" ? "Подключена" : "Не активна"}</span></div>
      </section>

      <section className="max-dashboard-history" aria-labelledby="max-history-heading">
        <header><h2 id="max-history-heading">История публикаций</h2><Button variant="outline" size="sm" onClick={() => void history.refetch()} disabled={history.isFetching}><RefreshCw className="size-4" />Обновить историю</Button></header>
        {history.isPending ? <p role="status" className="max-dashboard-empty">Загружаем историю…</p>
          : history.isError ? <div className="max-dashboard-empty" role="alert"><p><strong>{MAX_STATUS_COPY.history.title}.</strong> {MAX_STATUS_COPY.history.hint}</p><Button variant="outline" onClick={() => void history.refetch()}>{MAX_STATUS_COPY.history.retry}</Button></div>
          : history.data.length === 0 ? <p className="max-dashboard-empty">История появится после первой публикации.</p>
          : <ol>{history.data.slice(0, 8).map((item, index) => <li key={item.run_id ?? `${item.started_at}-${index}`}><div><strong>{item.image_tag?.split(":").at(-1) ?? "Публикация приложения"}</strong><small>{item.target_label ?? "Размещение не указано"}</small></div><time>{item.finished_at ? new Date(item.finished_at).toLocaleString("ru-RU") : item.started_at ? new Date(item.started_at).toLocaleString("ru-RU") : "—"}</time><span className={item.phase === "done" ? "text-success-fg" : item.phase === "failed" ? "text-danger-fg" : "text-fg-secondary"}>{phaseLabels[item.phase]}</span>{item.error && <p className="text-sm text-danger-fg">{item.error}</p>}</li>)}</ol>}
      </section>
    </MaxSectionShell>
  );
}
