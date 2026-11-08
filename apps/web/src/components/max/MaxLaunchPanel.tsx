"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, ChevronRight, CircleAlert, Copy, ExternalLink, Loader2, Plug, X } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { getMaxIntegration } from "@/lib/api/max-integration";
import { getMaxReadiness } from "@/lib/api/max-studio";
import { getLastDeploy } from "@/lib/api/runtime";
import type { DeployPhase, MaxIntegration, Project } from "@/lib/api/types";
import { getMaxJourney } from "@/lib/max-journey";
import { isMaxDeployActive } from "@/lib/max-launch-state";
import { copyMaxLaunchUrl } from "@/lib/max-launch-steps";
import { type HeartbeatWatch, heartbeatStale, formatElapsed, observeHeartbeat, publicationBytesLabel, publicationElapsedMs, publicationFailureText, publicationStageLabel } from "@/lib/max-publication-progress";
import { useMaxPublicationGuard } from "@/hooks/useMaxPublicationGuard";
import { guardedMaxReadiness } from "@/lib/max-publication-guard";
import { getMaxPublicationState } from "@/lib/max-publication-state";
import { MAX_STATUS_COPY } from "@/lib/max-status-copy";
import { useWorkspaceStore } from "@/store/workspace";
import { MaxLaunchButton } from "./MaxLaunchButton";
import { MaxDataMigration } from "./MaxDataMigration";
import { MaxPublicationRequirements } from "./MaxPublicationRequirements";
import "./max-studio.css";
import "./max-project-workspace.css";
import "./max-launch-page.css";

const ACTIVE_PHASES = new Set<DeployPhase>(["queued", "building", "pushing", "swapping", "cancelling"]);
const TERMINAL_PHASES = new Set<DeployPhase>(["done", "failed", "cancelled"]);

function getMaxMiniAppUrl(integration: MaxIntegration | undefined): string | null {
  if (!integration?.connected || !integration.eligible) return null;
  if (integration.status !== "verified" && integration.status !== "active") return null;

  // The API currently returns the bot's profile URL. Older responses may only
  // have the username verified by MAX. Only link to a plain MAX bot path.
  const botName = integration.deep_link == null
    ? /^([a-zA-Z0-9_-]+)$/.exec(integration.bot_username ?? "")?.[1]
    : /^https:\/\/max\.ru\/([a-zA-Z0-9_-]+)(?:\?startapp)?$/.exec(integration.deep_link)?.[1];
  return botName ? `https://max.ru/${botName}?startapp` : null;
}

export function MaxLaunchPanel({ project, onClose, standalone = false }: {
  project: Pick<Project, "id" | "name" | "template">; onClose?: () => void; standalone?: boolean;
}) {
  const toggleTimeline = useWorkspaceStore(state => state.toggleTimeline);
  const queryClient = useQueryClient();
  const integration = useQuery({ queryKey: ["max-integration", project.id], queryFn: () => getMaxIntegration(project.id), retry: false, refetchOnMount: "always" });
  const deploy = useQuery({ queryKey: ["deploy", project.id], queryFn: () => getLastDeploy(project.id), retry: false,
    refetchInterval: query => isMaxDeployActive(query.state.data?.phase ?? "idle", query.state.data?.run_id) ? 1_500 : false });
  const busyDeploy = !deploy.isError && isMaxDeployActive(deploy.data?.phase ?? "idle", deploy.data?.run_id);
  // Prerequisites are static while a publication runs; they are re-read when it ends, not every 2 s.
  const readiness = useQuery({ queryKey: ["max-readiness", project.id], queryFn: () => getMaxReadiness(project.id), retry: false, refetchOnMount: "always", refetchInterval: 10_000 });
  const deployPhase = deploy.data?.phase;
  const previousPhase = useRef<DeployPhase | undefined>(undefined);
  useEffect(() => {
    const before = previousPhase.current;
    previousPhase.current = deployPhase;
    if (before && ACTIVE_PHASES.has(before) && deployPhase && TERMINAL_PHASES.has(deployPhase)) void queryClient.invalidateQueries({ queryKey: ["max-readiness", project.id] });
  }, [deployPhase, project.id, queryClient]);
  // Liveness: the heartbeat is judged by when it last changed as seen here (the
  // client time of the fetch that brought it), never by comparing clocks.
  const heartbeatAt = deploy.data?.heartbeat_at ?? null;
  const observedAt = deploy.dataUpdatedAt;
  const [watch, setWatch] = useState<HeartbeatWatch | null>(null);
  const nextWatch = observeHeartbeat(watch, heartbeatAt, observedAt);
  if (nextWatch !== watch) setWatch(nextWatch);
  const stale = busyDeploy && heartbeatStale(nextWatch, observedAt);
  const stageLabel = busyDeploy ? publicationStageLabel(deploy.data) : "";
  const elapsedMs = busyDeploy && observedAt ? publicationElapsedMs(deploy.data, observedAt) : null;
  const bytesLabel = busyDeploy ? publicationBytesLabel(deploy.data?.progress) : null;
  const failure = publicationFailureText(deploy.data);
  const guard = useMaxPublicationGuard(project.id, deploy.data);
  const effectiveReadiness = guardedMaxReadiness(readiness.data, guard);
  const items = effectiveReadiness?.items ?? [];
  const available = readiness.isSuccess && guard !== "checking" && items.length > 0;
  const journey = getMaxJourney(project.id, items);
  const currentStage = available ? journey.currentStage : undefined;
  const stateError = readiness.isError || deploy.isError;
  const publication = getMaxPublicationState(available ? effectiveReadiness : undefined, deploy.data?.phase);
  const published = !stateError && deploy.isSuccess && !busyDeploy && guard !== "migration_required" && !effectiveReadiness?.publication_migration && publication === "published";
  const failed = !deploy.isError && deploy.data?.phase === "failed";
  const migrationPending = !busyDeploy && !stateError && (guard === "migration_required" || Boolean(effectiveReadiness?.publication_migration));
  const [migrationReviewOpen, setMigrationReviewOpen] = useState(false);
  const maxIntegration = integration.isSuccess ? integration.data : undefined;
  const productionUrl = published ? deploy.data?.prod_url ?? maxIntegration?.app_url ?? null : null;
  const maxAddressConfirmed = items.find(item => item.id === "max_url")?.done === true;
  const confirmedBotId = effectiveReadiness?.max_url_bot_id;
  const maxLaunchReady = maxAddressConfirmed && productionUrl && integration.isFetchedAfterMount && readiness.isFetchedAfterMount
    && confirmedBotId && confirmedBotId === maxIntegration?.bot_id;
  const maxMiniAppUrl = maxLaunchReady ? getMaxMiniAppUrl(maxIntegration) : null;
  const openUrl = maxMiniAppUrl ?? productionUrl;
  const title = stateError ? MAX_STATUS_COPY.readiness.title
    : busyDeploy ? "Публикация продолжается"
    : migrationPending ? "Нужна проверка изменения данных"
    : !available ? "Проверяем готовность…"
    : deploy.isPending ? "Проверяем публикацию…"
    : published ? "Приложение опубликовано"
    : failed ? "Публикация не завершилась"
    : currentStage?.id === "publish" ? "Всё готово к публикации" : currentStage?.label ?? "Проверьте готовность";

  async function copyUrl() {
    if (!productionUrl) return;
    const copied = await copyMaxLaunchUrl(productionUrl);
    if (copied) toast.success("Ссылка на приложение скопирована");
    else toast.error("Не удалось скопировать ссылку", { description: "Скопируйте адрес вручную." });
  }
  function openMaxCabinet(event: React.MouseEvent<HTMLAnchorElement>) {
    event.preventDefault(); void copyUrl(); window.open("https://business.max.ru/", "_blank", "noopener,noreferrer");
  }

  return (
    <aside data-product-shell data-max-studio data-testid="max-launch-panel" className={`max-launch-panel max-studio-launch${standalone ? " max-studio-launch-standalone max-launch-page" : ""}`}>
      {!standalone && <header className="max-launch-dialog-heading"><div><p className="max-project-eyebrow">{project.name}</p><h2>Запуск в MAX</h2></div><button type="button" onClick={onClose ?? toggleTimeline} aria-label="Свернуть панель запуска" className="max-project-back"><X className="size-5" /></button></header>}
      <div className="max-launch-panel-scroll max-studio-launch-body">
        <section aria-live="polite" role={stateError ? "alert" : undefined} data-testid="max-launch-current-step" className="max-launch-focus">
          <h2>{stateError && <CircleAlert className="size-5 shrink-0 text-danger-fg" />}{busyDeploy && <Loader2 className="size-5 animate-spin" />}{title}</h2>
          <p>{stateError ? MAX_STATUS_COPY.readiness.hint : busyDeploy ? stageLabel || "Публикация выполняется на сервере." : migrationPending ? "Перед публикацией нужны проверка изменения структуры данных и резервная копия." : !available ? "Статусы появятся после ответа сервера." : deploy.isPending ? "Уточняем статус публикации и постоянный адрес приложения." : published ? "Приложение доступно пользователям по постоянному адресу." : currentStage?.description ?? "Проверьте данные приложения перед запуском."}</p>
          {busyDeploy && <div data-testid="max-launch-publication-progress" className="max-launch-publication-progress" aria-live="polite">
            <strong>{stageLabel || "Публикуем"}</strong>
            {elapsedMs !== null && <span>идёт {formatElapsed(elapsedMs)}</span>}
            {bytesLabel && <span>{bytesLabel}</span>}
            {stale && <p className="max-launch-notice">Сервер давно не сообщал о ходе публикации — проверяем состояние. Новая публикация не запускается.</p>}
          </div>}
          {!published && publication === "outdated" && !stateError && <p className="max-launch-notice">Изменения не опубликованы. После последней публикации появились изменения.</p>}
          {!busyDeploy && !stateError && deploy.data?.phase === "done" && deploy.data.detail === "already_current" && <p className="max-launch-notice" data-testid="max-launch-noop">Приложение уже опубликовано — повторная сборка не потребовалась.</p>}
          {!busyDeploy && !stateError && deploy.data?.phase === "done" && deploy.data.detail === "config_only" && <p className="max-launch-notice" data-testid="max-launch-noop">Обновлены только настройки — приложение не пересобиралось.</p>}
          {failed && <div role="alert" className="text-sm text-danger-fg"><p>{failure.title}</p>{failure.detail && <p className="max-launch-failure-detail">{failure.detail}</p>}</div>}
          {migrationPending && migrationReviewOpen && <MaxDataMigration projectId={project.id} snapshotId={effectiveReadiness?.publication_migration?.snapshot_id ?? deploy.data?.snapshot_id ?? null} />}
          {busyDeploy && <p className="text-sm">Публикация продолжится на сервере, даже если вы уйдёте со страницы.</p>}
          <div className="max-launch-primary-action">
            {stateError ? <Button onClick={() => { void readiness.refetch(); void deploy.refetch(); }}>{MAX_STATUS_COPY.readiness.retry}</Button>
              : deploy.isPending ? <Button disabled><Loader2 className="size-4 animate-spin" />Проверяем публикацию…</Button>
              : migrationPending ? <Button data-testid="max-migration-review-open" onClick={() => setMigrationReviewOpen(true)}>Проверить изменения данных</Button>
              : published ? openUrl && <Button asChild><a href={openUrl} target="_blank" rel="noreferrer">{maxMiniAppUrl ? "Открыть в MAX" : "Открыть сайт"} <ExternalLink className="size-4" /></a></Button>
              : busyDeploy || currentStage?.id === "publish" || !available ? <MaxLaunchButton projectId={project.id} />
              : currentStage && <Button asChild><Link href={currentStage.href} onClick={onClose}>{currentStage.actionLabel}<ChevronRight className="size-4" /></Link></Button>}
            {!standalone && published && <Button asChild variant="outline"><Link href={`/max/${project.id}/manage`}>Управление</Link></Button>}
            {!standalone && <Button asChild variant="outline"><Link href={`/max/${project.id}`} onClick={onClose}>{published ? "Подготовить обновление" : "В редактор"}</Link></Button>}
          </div>
          {productionUrl && <div className="max-launch-address"><span data-testid="max-launch-app-url" title={productionUrl}>{productionUrl}</span><Button variant="ghost" size="icon" aria-label="Скопировать адрес приложения" onClick={() => void copyUrl()}><Copy className="size-4" /></Button></div>}
          {published && !maxAddressConfirmed && <div className="max-launch-notice"><p>Добавьте адрес в кнопку приложения в кабинете MAX, затем подтвердите его в «Подключениях».</p><a href="https://business.max.ru/" target="_blank" rel="noreferrer" onClick={openMaxCabinet} data-testid="max-open-business-cabinet">Открыть кабинет MAX ↗</a><Link href={`/max/${project.id}/integrations`}>Подтвердить адрес</Link></div>}
        </section>
        <MaxPublicationRequirements projectId={project.id} items={items} status={readiness.isError ? "error" : available ? "ready" : "loading"} promotedId={published || busyDeploy ? null : currentStage?.id ?? null} />
        {!standalone && <section aria-label="Другие разделы проекта" data-testid="max-launch-actions" className="max-launch-options">
          <header><h3>Необязательное</h3><p>Для запуска не требуется</p></header>
          <div className="max-launch-option"><Plug className="size-4" /><div><h4>Подключить сервисы</h4><p>Платежи, CRM и аналитика</p></div><Button asChild variant="outline" size="sm"><Link href={`/max/${project.id}/integrations?tab=services`}>Выбрать сервисы</Link></Button></div>
          <div className="max-launch-option"><Bot className="size-4" /><div><h4>Настройки подключения MAX</h4><p>Замена токена, повторная проверка связи</p></div><Button asChild variant="outline" size="sm"><Link href={`/max/${project.id}/integrations`}>Открыть</Link></Button></div>
        </section>}
        {busyDeploy && (deploy.data?.logs.length ?? 0) > 0 && <details className="max-launch-checks"><summary>Подробности публикации</summary><pre className="max-launch-logs">{deploy.data!.logs.slice(-12).join("\n")}</pre></details>}
      </div>
    </aside>
  );
}
