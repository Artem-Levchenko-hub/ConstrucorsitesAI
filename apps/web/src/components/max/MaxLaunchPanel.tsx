"use client";

import { useQuery } from "@tanstack/react-query";
import { ChevronRight, CircleAlert, Copy, ExternalLink, Loader2, Plug, Server, X } from "lucide-react";
import Link from "next/link";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { getMaxIntegration } from "@/lib/api/max-integration";
import { getMaxReadiness } from "@/lib/api/max-studio";
import { getLastDeploy } from "@/lib/api/runtime";
import type { DeployPhase, Project } from "@/lib/api/types";
import { getMaxJourney } from "@/lib/max-journey";
import { isMaxDeployActive } from "@/lib/max-launch-state";
import { copyMaxLaunchUrl } from "@/lib/max-launch-steps";
import { getMaxPublicationState } from "@/lib/max-publication-state";
import { useWorkspaceStore } from "@/store/workspace";
import { MaxLaunchButton } from "./MaxLaunchButton";
import { MaxPublicationRequirements, PUBLICATION_REQUIREMENTS } from "./MaxPublicationRequirements";
import "./max-studio.css";
import "./max-project-workspace.css";

const phaseLabels: Partial<Record<DeployPhase, string>> = {
  queued: "В очереди на публикацию", building: "Собираем приложение", pushing: "Передаём сборку на сервер",
  swapping: "Проверяем и переключаем версию", cancelling: "Останавливаем публикацию",
};

export function MaxLaunchPanel({ project, onClose, standalone = false }: {
  project: Pick<Project, "id" | "name" | "template">; onClose?: () => void; standalone?: boolean;
}) {
  const toggleTimeline = useWorkspaceStore(state => state.toggleTimeline);
  const integration = useQuery({ queryKey: ["max-integration", project.id], queryFn: () => getMaxIntegration(project.id), retry: false });
  const deploy = useQuery({ queryKey: ["deploy", project.id], queryFn: () => getLastDeploy(project.id), retry: false,
    refetchInterval: query => isMaxDeployActive(query.state.data?.phase ?? "idle", query.state.data?.run_id) ? 1_500 : false });
  const busyDeploy = !deploy.isError && isMaxDeployActive(deploy.data?.phase ?? "idle", deploy.data?.run_id);
  const readiness = useQuery({ queryKey: ["max-readiness", project.id], queryFn: () => getMaxReadiness(project.id), retry: false, refetchInterval: busyDeploy ? 2_000 : 10_000 });
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
  const title = stateError ? "Не удалось проверить готовность"
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
        <div className="max-launch-readiness"><span>Готовность к публикации</span><strong>{readiness.isError ? "Статус недоступен" : available ? `Готово ${requiredDone} из ${PUBLICATION_REQUIREMENTS.length}` : "Проверяем…"}</strong>
          {!readiness.isError && <progress data-testid="max-launch-progress" aria-label="Готовность к публикации" value={available ? requiredDone / PUBLICATION_REQUIREMENTS.length * 100 : 0} max={100} />}
        </div>
        <section aria-live="polite" role={stateError ? "alert" : undefined} data-testid="max-launch-current-step" className="max-launch-focus">
          <span className="max-project-eyebrow">{busyDeploy ? "Публикуем" : published ? "Публикация" : "Следующий шаг"}</span>
          <h2>{stateError && <CircleAlert className="size-5 shrink-0 text-danger-fg" />}{busyDeploy && <Loader2 className="size-5 animate-spin" />}{title}</h2>
          <p>{stateError ? "Повторите проверку, чтобы получить актуальный статус сервера." : busyDeploy ? phaseLabels[deploy.data!.phase] : !available ? "Статусы появятся после ответа сервера." : deploy.isPending ? "Уточняем статус публикации и постоянный адрес приложения." : published ? "Эта версия доступна пользователям по постоянному адресу." : currentStage?.description ?? "Проверьте данные приложения перед запуском."}</p>
          {!published && publication === "outdated" && !stateError && <p className="max-launch-notice">Текущая версия не опубликована. После последней публикации появились изменения.</p>}
          {failed && <p role="alert" className="text-sm text-danger-fg">{deploy.data?.error ?? "Проверьте настройки и повторите публикацию."}</p>}
          {busyDeploy && <p className="text-sm">Можно закрыть окно — процесс выполняется на сервере.</p>}
          <div className="max-launch-primary-action">
            {stateError ? <Button onClick={() => { void readiness.refetch(); void deploy.refetch(); }}>Повторить проверку</Button>
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
        <MaxPublicationRequirements projectId={project.id} items={items} status={readiness.isError ? "error" : available ? "ready" : "loading"} />
        <section aria-label="Другие разделы проекта" data-testid="max-launch-actions" className="max-launch-options">
          <header><h3>Сервисы и размещение</h3><p>Необязательно для запуска</p></header>
          <div className="max-launch-option"><Plug className="size-4" /><div><h4>Подключить сервисы</h4><p>Платежи, CRM и аналитика</p></div><Button asChild variant="outline" size="sm"><Link href={`/max/${project.id}?panel=services`}>Выбрать сервисы</Link></Button></div>
          <div className="max-launch-option"><Server className="size-4" /><div><h4>Собственный сервер</h4><p>Размещение на вашей VPS</p></div><Button asChild variant="outline" size="sm"><Link href={`/max/${project.id}?panel=hosting`}>Настроить сервер</Link></Button></div>
        </section>
        <div className="max-launch-configuration"><Button asChild variant="outline"><Link href={`/max/${project.id}?panel=max`}>Подключение MAX</Link></Button></div>
        {busyDeploy && (deploy.data?.logs.length ?? 0) > 0 && <details className="max-launch-checks"><summary>Подробности публикации</summary><pre className="max-launch-logs">{deploy.data!.logs.slice(-12).join("\n")}</pre></details>}
      </div>
    </aside>
  );
}
