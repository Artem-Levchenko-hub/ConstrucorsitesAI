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
import type { DeployPhase, Project } from "@/lib/api/types";
import { getMaxJourney } from "@/lib/max-journey";
import { isMaxDeployActive } from "@/lib/max-launch-state";
import { copyMaxLaunchUrl } from "@/lib/max-launch-steps";
import { type HeartbeatWatch, heartbeatStale, formatElapsed, observeHeartbeat, publicationBytesLabel, publicationElapsedMs, publicationFailureText, publicationStageLabel } from "@/lib/max-publication-progress";
import { getMaxPublicationState } from "@/lib/max-publication-state";
import { MAX_STATUS_COPY, MAX_UNKNOWN_LABEL } from "@/lib/max-status-copy";
import { useWorkspaceStore } from "@/store/workspace";
import { MaxLaunchButton } from "./MaxLaunchButton";
import { MaxPublicationRequirements, PUBLICATION_REQUIREMENTS } from "./MaxPublicationRequirements";
import "./max-studio.css";
import "./max-project-workspace.css";

const ACTIVE_PHASES = new Set<DeployPhase>(["queued", "building", "pushing", "swapping", "cancelling"]);
const TERMINAL_PHASES = new Set<DeployPhase>(["done", "failed", "cancelled"]);

export function MaxLaunchPanel({ project, onClose, standalone = false }: {
  project: Pick<Project, "id" | "name" | "template">; onClose?: () => void; standalone?: boolean;
}) {
  const toggleTimeline = useWorkspaceStore(state => state.toggleTimeline);
  const queryClient = useQueryClient();
  const integration = useQuery({ queryKey: ["max-integration", project.id], queryFn: () => getMaxIntegration(project.id), retry: false });
  const deploy = useQuery({ queryKey: ["deploy", project.id], queryFn: () => getLastDeploy(project.id), retry: false,
    refetchInterval: query => isMaxDeployActive(query.state.data?.phase ?? "idle", query.state.data?.run_id) ? 1_500 : false });
  const busyDeploy = !deploy.isError && isMaxDeployActive(deploy.data?.phase ?? "idle", deploy.data?.run_id);
  // Prerequisites are static while a publication runs; they are re-read when it ends, not every 2 s.
  const readiness = useQuery({ queryKey: ["max-readiness", project.id], queryFn: () => getMaxReadiness(project.id), retry: false, refetchInterval: 10_000 });
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
  const items = readiness.data?.items ?? [];
  const available = readiness.isSuccess && items.length > 0;
  const requiredDone = PUBLICATION_REQUIREMENTS.filter(required => items.find(item => item.id === required.id)?.done).length;
  const journey = getMaxJourney(project.id, items);
  const currentStage = available ? journey.currentStage : undefined;
  const stateError = readiness.isError || deploy.isError;
  const publication = getMaxPublicationState(readiness.isSuccess ? readiness.data : undefined, deploy.data?.phase);
  const published = !stateError && deploy.isSuccess && !busyDeploy && publication === "published";
  const failed = !deploy.isError && deploy.data?.phase === "failed";
  const productionUrl = published ? deploy.data?.prod_url ?? (integration.isSuccess ? integration.data?.app_url : null) : null;
  const title = stateError ? MAX_STATUS_COPY.readiness.title
    : busyDeploy ? "Публикация продолжается"
    : !available ? "Проверяем готовность…"
    : deploy.isPending ? "Проверяем публикацию…"
    : published ? "Текущая версия опубликована"
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
    <aside data-product-shell data-max-studio data-testid="max-launch-panel" className={`max-launch-panel max-studio-launch${standalone ? " max-studio-launch-standalone" : ""}`}>
      {!standalone && <header className="max-launch-dialog-heading"><div><p className="max-project-eyebrow">{project.name}</p><h2>Запуск в MAX</h2></div><button type="button" onClick={onClose ?? toggleTimeline} aria-label="Свернуть панель запуска" className="max-project-back"><X className="size-5" /></button></header>}
      <div className="max-launch-panel-scroll max-studio-launch-body">
        <section aria-live="polite" role={stateError ? "alert" : undefined} data-testid="max-launch-current-step" className="max-launch-focus">
          <span className="max-project-eyebrow">{busyDeploy ? "Публикуем" : published ? "Публикация" : "Следующий шаг"}</span>
          <h2>{stateError && <CircleAlert className="size-5 shrink-0 text-danger-fg" />}{busyDeploy && <Loader2 className="size-5 animate-spin" />}{title}</h2>
          <p>{stateError ? MAX_STATUS_COPY.readiness.hint : busyDeploy ? stageLabel || "Публикация выполняется на сервере." : !available ? "Статусы появятся после ответа сервера." : deploy.isPending ? "Уточняем статус публикации и постоянный адрес приложения." : published ? "Эта версия доступна пользователям по постоянному адресу." : currentStage?.description ?? "Проверьте данные приложения перед запуском."}</p>
          {busyDeploy && <div data-testid="max-launch-publication-progress" className="max-launch-publication-progress" aria-live="polite">
            <strong>{stageLabel || "Публикуем"}</strong>
            {elapsedMs !== null && <span>идёт {formatElapsed(elapsedMs)}</span>}
            {bytesLabel && <span>{bytesLabel}</span>}
            {stale && <p className="max-launch-notice">Сервер давно не сообщал о ходе публикации — проверяем состояние. Новая публикация не запускается.</p>}
          </div>}
          {!published && publication === "outdated" && !stateError && <p className="max-launch-notice">Текущая версия не опубликована. После последней публикации появились изменения.</p>}
          {!busyDeploy && !stateError && deploy.data?.phase === "done" && deploy.data.detail === "already_current" && <p className="max-launch-notice" data-testid="max-launch-noop">Эта версия уже работала — повторная сборка не потребовалась.</p>}
          {!busyDeploy && !stateError && deploy.data?.phase === "done" && deploy.data.detail === "config_only" && <p className="max-launch-notice" data-testid="max-launch-noop">Обновлены только настройки — версия не пересобиралась.</p>}
          {failed && <div role="alert" className="text-sm text-danger-fg"><p>{failure.title}</p>{failure.detail && <p className="max-launch-failure-detail">{failure.detail}</p>}</div>}
          {busyDeploy && <p className="text-sm">Можно закрыть окно — процесс выполняется на сервере.</p>}
          <div className="max-launch-primary-action">
            {stateError ? <Button onClick={() => { void readiness.refetch(); void deploy.refetch(); }}>{MAX_STATUS_COPY.readiness.retry}</Button>
              : deploy.isPending ? <Button disabled><Loader2 className="size-4 animate-spin" />Проверяем публикацию…</Button>
              : published ? productionUrl && <Button asChild><a href={productionUrl} target="_blank" rel="noreferrer">Открыть приложение <ExternalLink className="size-4" /></a></Button>
              : busyDeploy || currentStage?.id === "publish" || !available ? <MaxLaunchButton projectId={project.id} />
              : currentStage && <Button asChild><Link href={currentStage.href} onClick={onClose}>{currentStage.actionLabel}<ChevronRight className="size-4" /></Link></Button>}
            {published && <Button asChild variant="outline"><Link href={`/max/${project.id}/dashboard`}>Управление</Link></Button>}
            <Button asChild variant="outline"><Link href={`/max/${project.id}`} onClick={onClose}>{published ? "Подготовить обновление" : "В редактор"}</Link></Button>
          </div>
          {productionUrl && <div className="max-launch-address"><a data-testid="max-launch-app-url" href={productionUrl} target="_blank" rel="noreferrer">{productionUrl}</a><Button variant="ghost" size="icon" aria-label="Скопировать адрес приложения" onClick={() => void copyUrl()}><Copy className="size-4" /></Button></div>}
          {published && !items.find(item => item.id === "max_url")?.done && <div className="max-launch-notice"><p>Добавьте адрес в кнопку приложения в MAX Partner, затем подтвердите его в настройках.</p><a href="https://business.max.ru/" target="_blank" rel="noreferrer" onClick={openMaxCabinet} data-testid="max-open-business-cabinet">Открыть кабинет MAX ↗</a><Link href={`/max/${project.id}?panel=max`}>Подтвердить адрес</Link></div>}
        </section>
        <MaxPublicationRequirements projectId={project.id} items={items} status={readiness.isError ? "error" : available ? "ready" : "loading"} promotedId={published || busyDeploy ? null : currentStage?.id ?? null} />
        <section aria-label="Другие разделы проекта" data-testid="max-launch-actions" className="max-launch-options">
          <header><h3>Необязательное</h3><p>Для запуска не требуется</p></header>
          <div className="max-launch-option"><Plug className="size-4" /><div><h4>Подключить сервисы</h4><p>Платежи, CRM и аналитика</p></div><Button asChild variant="outline" size="sm"><Link href={`/max/${project.id}?panel=services`}>Выбрать сервисы</Link></Button></div>
          <div className="max-launch-option"><Bot className="size-4" /><div><h4>Настройки подключения MAX</h4><p>Замена токена, повторная проверка связи</p></div><Button asChild variant="outline" size="sm"><Link href={`/max/${project.id}?panel=max`}>Открыть</Link></Button></div>
        </section>
        {busyDeploy && (deploy.data?.logs.length ?? 0) > 0 && <details className="max-launch-checks"><summary>Подробности публикации</summary><pre className="max-launch-logs">{deploy.data!.logs.slice(-12).join("\n")}</pre></details>}
      </div>
    </aside>
  );
}
