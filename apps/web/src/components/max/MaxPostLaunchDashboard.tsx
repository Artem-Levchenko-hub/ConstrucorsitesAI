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
  const statusLabel = statusError ? "Не удалось проверить публикацию"
    : readiness.isPending || deploy.isPending ? "Проверяем публикацию"
    : published ? "Текущая версия опубликована" : "Текущая версия не опубликована";
  const url = published ? deploy.data?.prod_url ?? (integration.isSuccess ? integration.data?.app_url : null) : null;
  const latest = deploy.isSuccess ? deploy.data : undefined;
  const integrationLabel = integration.isError ? "Не удалось проверить" : integration.isPending ? "Проверяем…" : integration.data?.connected ? integration.data.bot_name ?? "Подключён" : "Не подключён";

  function refreshStatus() { void readiness.refetch(); void deploy.refetch(); void runtime.refetch(); void integration.refetch(); }

  return (
    <MaxSectionShell projectId={projectId} projectName={projectName} active="dashboard" eyebrow="Управление" title="Обзор приложения" lead="Публикация, подключения и история изменений вашего приложения.">
      <div className="max-dashboard-overview">
        <section className="max-dashboard-project" aria-label="Ваше приложение">
          <p className={statusError ? "text-danger-fg" : published ? "text-success-fg" : "text-fg-secondary"} role="status">{published ? <Check className="size-4" /> : statusError ? <CircleAlert className="size-4" /> : <Loader2 className={`size-4 ${readiness.isPending || deploy.isPending ? "animate-spin" : ""}`} />}{statusLabel}</p>
          <h2>{projectName}</h2>
          {url ? <a className="max-dashboard-url" href={url} target="_blank" rel="noreferrer">{url}<ExternalLink className="size-4 shrink-0" /></a> : <p className="text-sm text-fg-secondary">{statusError ? "Повторите проверку, чтобы узнать актуальный статус и адрес приложения." : "Постоянный адрес текущей версии появится после публикации."}</p>}
          <div className="max-dashboard-actions">
            {url ? <Button asChild><a href={url} target="_blank" rel="noreferrer">Открыть приложение <ArrowUpRight className="size-4" /></a></Button>
              : <Button asChild><Link href={`/max/${projectId}/publish`}>{active ? "Ход публикации" : "Подготовить запуск"}</Link></Button>}
            <Button asChild variant="outline"><Link href={`/max/${projectId}`}>Редактировать</Link></Button>
          </div>
          {publicationState === "outdated" && !statusError && <p className="max-launch-notice">После последней публикации появились изменения. Проверьте их в редакторе и опубликуйте обновление.</p>}
          {active && <p className="max-launch-notice" role="status">{phaseLabels[deploy.data!.phase]} — публикация выполняется на сервере.</p>}
          {latest?.phase === "failed" && <div role="alert" className="max-dashboard-error"><strong>Последняя публикация не завершилась</strong><p>{latest.error ?? "Проверьте готовность и повторите попытку."}</p></div>}
          {statusError && <Button variant="outline" className="mt-3" onClick={refreshStatus}>Повторить проверку</Button>}
          <Link className="max-dashboard-settings-link" href={`/max/${projectId}/settings?tab=app`}>Данные и настройки приложения <ArrowUpRight className="size-4" /></Link>
        </section>
        <section className="max-dashboard-release" aria-labelledby="max-release-heading">
          <h2 id="max-release-heading">Последняя публикация</h2>
          <p>Сведения о последней операции на сервере</p>
          <dl>
            <div><dt>Статус операции</dt><dd>{deploy.isError ? "Не удалось загрузить" : deploy.isPending ? "Проверяем…" : latest ? phaseLabels[latest.phase] : "Нет данных"}</dd></div>
            <div><dt>Версия сборки</dt><dd>{latest?.image_tag?.split(":").at(-1) ?? "—"}</dd></div>
            <div><dt>Размещение</dt><dd>{latest?.target_label ?? "—"}</dd></div>
            <div><dt>Операция завершена</dt><dd>{latest?.finished_at ? new Date(latest.finished_at).toLocaleString("ru-RU") : "—"}</dd></div>
          </dl>
          <p className="max-dashboard-monitoring-note">Постоянный мониторинг доступности не подключён. Успешная публикация подтверждает проверку при выпуске версии.</p>
        </section>
      </div>

      <section className="max-dashboard-system" aria-labelledby="max-system-heading">
        <header><div><h2 id="max-system-heading">Состояние и подключения</h2><p>Данные среды разработки и связи с MAX</p></div><Button variant="outline" onClick={refreshStatus} disabled={runtime.isFetching || integration.isFetching || readiness.isFetching || deploy.isFetching}><RefreshCw className="size-4" />Обновить</Button></header>
        <div className="max-dashboard-system-row"><div><h3>Среда разработки</h3><p>Используется редактором и превью. Её активность не определяет публикацию приложения.</p></div>
          <div className="max-dashboard-runtime">{runtime.isError ? <p className="text-danger-fg">Не удалось проверить среду</p> : runtime.isPending ? <p>Проверяем…</p> : <><p>{runtime.data ? runtimeLabels[runtime.data.state] : "Нет данных"}</p><RuntimeButton projectId={projectId} display="compact" /></>}</div>
        </div>
        <div className="max-dashboard-system-row"><div><h3>Безопасный вход MAX</h3><p>Подключение бота для входа пользователей</p></div><span className={integration.isError ? "text-danger-fg" : "text-fg-secondary"}>{integrationLabel}</span><Button asChild variant="outline" size="sm"><Link href={`/max/${projectId}/settings?tab=bot`}>Настроить MAX</Link></Button></div>
        <div className="max-dashboard-system-row"><div><h3>Связь с MAX</h3><p>Серверные события приложения</p></div><span className={integration.isError ? "text-danger-fg" : "text-fg-secondary"}>{integration.isError ? "Не удалось проверить" : integration.isPending ? "Проверяем…" : integration.data?.status === "active" ? "Подключена" : "Не активна"}</span></div>
      </section>

      <section className="max-dashboard-history" aria-labelledby="max-history-heading">
        <header><h2 id="max-history-heading">История публикаций</h2><Button variant="outline" size="sm" onClick={() => void history.refetch()} disabled={history.isFetching}><RefreshCw className="size-4" />Обновить историю</Button></header>
        {history.isPending ? <p role="status" className="max-dashboard-empty">Загружаем историю…</p>
          : history.isError ? <div className="max-dashboard-empty" role="alert"><p>Не удалось загрузить историю публикаций.</p><Button variant="outline" onClick={() => void history.refetch()}>Повторить загрузку</Button></div>
          : history.data.length === 0 ? <p className="max-dashboard-empty">История появится после первой публикации.</p>
          : <ol>{history.data.slice(0, 8).map((item, index) => <li key={item.run_id ?? `${item.started_at}-${index}`}><div><strong>{item.image_tag?.split(":").at(-1) ?? "Публикация приложения"}</strong><small>{item.target_label ?? "Размещение не указано"}</small></div><time>{item.finished_at ? new Date(item.finished_at).toLocaleString("ru-RU") : item.started_at ? new Date(item.started_at).toLocaleString("ru-RU") : "—"}</time><span className={item.phase === "done" ? "text-success-fg" : item.phase === "failed" ? "text-danger-fg" : "text-fg-secondary"}>{phaseLabels[item.phase]}</span>{item.error && <p className="text-sm text-danger-fg">{item.error}</p>}</li>)}</ol>}
      </section>
    </MaxSectionShell>
  );
}
