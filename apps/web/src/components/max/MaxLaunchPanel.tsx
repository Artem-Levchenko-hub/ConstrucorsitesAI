"use client";

import { useQuery } from "@tanstack/react-query";
import {
  Check,
  ChevronRight,
  ExternalLink,
  Loader2,
  PanelRightClose,
  Plug,
  ShieldCheck,
} from "lucide-react";
import Link from "next/link";
import { toast } from "sonner";

import { getMaxIntegration } from "@/lib/api/max-integration";
import { getLastDeploy } from "@/lib/api/runtime";
import type { Project } from "@/lib/api/types";
import { Button } from "@/components/ui/button";
import { useWorkspaceStore } from "@/store/workspace";
import { MaxIntegrationButton } from "@/components/workspace/MaxIntegrationButton";
import { RuntimeButton } from "@/components/workspace/RuntimeButton";
import { MaxProjectSetupDialog } from "./MaxProjectSetupDialog";
import { MaxLaunchButton } from "./MaxLaunchButton";
import { getMaxReadiness } from "@/lib/api/max-studio";
import {
  copyMaxLaunchUrl,
} from "@/lib/max-launch-steps";
import { getMaxJourney, getMaxJourneyItemHref } from "@/lib/max-journey";
import { isMaxDeployActive } from "@/lib/max-launch-state";
import { cn } from "@/lib/utils";

export function MaxLaunchPanel({
  project,
  onClose,
}: {
  project: Project;
  onClose?: () => void;
}) {
  const toggleTimeline = useWorkspaceStore((state) => state.toggleTimeline);
  const integration = useQuery({
    queryKey: ["max-integration", project.id],
    queryFn: () => getMaxIntegration(project.id),
    retry: false,
  });
  const deploy = useQuery({
    queryKey: ["deploy", project.id],
    queryFn: () => getLastDeploy(project.id),
    retry: false,
    refetchInterval: (query) =>
      isMaxDeployActive(query.state.data?.phase ?? "idle", query.state.data?.run_id)
        ? 1_500
        : false,
  });
  const readiness = useQuery({
    queryKey: ["max-readiness", project.id],
    queryFn: () => getMaxReadiness(project.id),
    retry: false,
    refetchInterval: ["building", "pushing", "swapping"].includes(
      deploy.data?.phase ?? "",
    )
      ? 2_000
      : 10_000,
  });

  const busyDeploy = isMaxDeployActive(deploy.data?.phase ?? "idle", deploy.data?.run_id);
  const productionUrl = deploy.data?.prod_url ?? integration.data?.app_url ?? null;
  const items = readiness.data?.items ?? [];
  const readinessAvailable = readiness.isSuccess && items.length > 0;
  const journey = getMaxJourney(project.id, items);
  const currentStage = readinessAvailable ? journey.currentStage : undefined;
  const nextItem = currentStage
    ? items.find(
        (item) =>
          !item.done &&
          getMaxJourneyItemHref(project.id, item.id) === currentStage.href,
      )
    : undefined;
  const nextStepLabel = readiness.isError
    ? "Не удалось проверить готовность"
    : !readinessAvailable
      ? "Проверяем готовность…"
      : currentStage?.label ?? "Всё готово";
  const nextStepCopy = readiness.isError
    ? "Обновите страницу или повторите попытку чуть позже."
      : !readinessAvailable
      ? "Статусы появятся после ответа сервера."
      : currentStage
        ? currentStage.description
        : "Приложение готово к работе в MAX.";
  const configurationEmphasis = ["business", "legal"].includes(
    nextItem?.id ?? "",
  );
  const botEmphasis = nextItem?.id === "bot";

  async function copyAppUrl() {
    const appUrl = productionUrl;
    if (!appUrl) return;

    if (await copyMaxLaunchUrl(appUrl)) {
      toast.success("Ссылка на приложение скопирована", {
        description: "В кабинете MAX добавьте её к кнопке «Открыть».",
      });
    } else {
      toast.error("Не удалось скопировать ссылку", {
        description: "Ссылка остаётся доступна ниже — скопируйте её вручную.",
      });
    }
  }

  function openMaxCabinet(event: React.MouseEvent<HTMLAnchorElement>) {
    event.preventDefault();
    void copyAppUrl();
    window.open(
      "https://business.max.ru/",
      "_blank",
      "noopener,noreferrer",
    );
  }

  return (
    <aside data-product-shell data-testid="max-launch-panel" className="max-launch-panel flex h-full min-h-0 flex-col border-l border-[#2b2d32] bg-[#191b20] text-white">
      <div className="flex h-12 shrink-0 items-center justify-between border-b border-[#2b2d32] px-4">
        <div className="flex items-center gap-2.5">
          <span
            className={cn(
              "h-1.5 w-1.5 rounded-full",
              readiness.data?.ready_to_launch ? "bg-success" : "bg-[#4f81f7]",
            )}
          />
          <span className="text-[10px] font-semibold uppercase tracking-[0.16em] text-[#828491]">Путь до запуска</span>
        </div>
        <button
          type="button"
          onClick={onClose ?? toggleTimeline}
          aria-label="Свернуть панель запуска"
          title="Свернуть панель запуска"
          className="-mr-1 flex h-11 w-11 items-center justify-center rounded-md text-[#828491] transition-colors hover:bg-[#121519] hover:text-white sm:h-8 sm:w-8"
        >
          <PanelRightClose className="h-3.5 w-3.5" />
        </button>
      </div>

      <div className="max-launch-panel-scroll max-studio-scroll flex-1 overflow-y-auto p-4">
        <section aria-labelledby="max-launch-heading">
          <div className="flex items-end justify-between gap-3">
            <div>
              <p className="text-[10px] font-medium uppercase tracking-[0.16em] text-[#828491]">
                Готовность
              </p>
              <h2 id="max-launch-heading" className="mt-1 text-base font-semibold">
                Запуск в MAX
              </h2>
            </div>
            <span className="tabular-nums text-sm font-medium text-[#9fa1b1]">
              {readinessAvailable ? `${journey.progress}%` : "—"}
            </span>
          </div>
          <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-[#25272b]">
            <div
              aria-label={`Готовность к запуску: ${readinessAvailable ? journey.progress : 0}%`}
              aria-valuemax={100}
              aria-valuemin={0}
              aria-valuenow={readinessAvailable ? journey.progress : 0}
              data-testid="max-launch-progress"
              role="progressbar"
              className="h-full rounded-full bg-[#4f81f7] transition-[width]"
              style={{ width: `${readinessAvailable ? journey.progress : 0}%` }}
            />
          </div>

          <div aria-live="polite" data-testid="max-launch-current-step" className="mt-5 border-l-2 border-[#4f81f7]/70 pl-3">
            <p className="text-[10px] font-medium uppercase tracking-[0.14em] text-[#828491]">
              {currentStage ? `Этап ${currentStage.position} из ${journey.total}` : "Запуск завершён"}
            </p>
            <p className="mt-1 text-sm font-medium text-white">
              {nextStepLabel}
            </p>
            <p className="mt-1 text-[11px] leading-4 text-[#9fa1b1]">
              {nextStepCopy}
            </p>
            {nextItem?.action && (
              <p className="mt-2 text-[11px] font-semibold text-[#6a95fa]">
                Действие: {nextItem.action}
              </p>
            )}
          </div>
          {currentStage ? (
            <Button asChild className="mt-4 h-11 w-full bg-[#4f81f7] text-[#121519] hover:bg-[#6a95fa]">
              <Link href={currentStage.href}>
                {currentStage.actionLabel}
                <ChevronRight className="size-4" />
              </Link>
            </Button>
          ) : (
            <Button asChild className="mt-4 h-11 w-full bg-[#4f81f7] text-[#121519] hover:bg-[#6a95fa]">
              <Link href={`/max/${project.id}/dashboard`}>
                Открыть управление
                <ChevronRight className="size-4" />
              </Link>
            </Button>
          )}
        </section>

        {readinessAvailable && (
          <ol aria-label="Шаги публикации в MAX" className="mt-5 space-y-1 border-y border-[#25272b] py-3">
            {journey.stages.map((step) => (
              <li
                key={step.id}
                aria-current={step.status === "current" ? "step" : undefined}
                data-status={step.status}
                data-testid={`max-launch-step-${step.id}`}
                className={cn(
                  "flex items-start gap-2.5 rounded-md px-2 py-2 text-[11px]",
                  step.status === "current" && "bg-[#4f81f7]/[.08]",
                )}
              >
                <span
                  aria-hidden="true"
                  className={cn(
                    "mt-px flex h-4 w-4 shrink-0 items-center justify-center rounded-full border text-[9px] font-semibold",
                    step.status === "completed" && "border-success/45 bg-success/10 text-success",
                    step.status === "current" && "border-[#4f81f7] bg-[#4f81f7] text-[#121519]",
                    step.status === "upcoming" && "border-[#2b2d32] text-[#828491]",
                  )}
                >
                  {step.status === "completed" ? <Check className="h-2.5 w-2.5" /> : step.position}
                </span>
                <span className="min-w-0">
                  <span className={cn("block", step.status === "completed" ? "text-[#828491]" : "text-white", step.status === "current" && "font-semibold")}>
                    {step.label}
                  </span>
                  {step.status === "current" && (
                    <span className="mt-0.5 block text-[#6a95fa]">Сейчас: {step.actionLabel}</span>
                  )}
                  {step.status === "upcoming" && <span className="mt-0.5 block text-[#828491]">Далее</span>}
                </span>
              </li>
            ))}
          </ol>
        )}

        <section className="mt-5 space-y-2.5" aria-label="Другие разделы проекта" data-testid="max-launch-actions">
          <p className="text-[10px] font-medium uppercase tracking-[0.14em] text-[#828491]">
            Другие разделы
          </p>
          <div className="grid grid-cols-2 gap-2">
            <MaxProjectSetupDialog
              projectId={project.id}
              emphasized={configurationEmphasis}
              label="Данные"
            />
            <MaxIntegrationButton
              projectId={project.id}
              initialTemplate={project.template}
              display="panel"
              emphasized={botEmphasis}
              label="MAX"
            />
          </div>
          <Button
            asChild
            variant="secondary"
            className="h-10 w-full justify-between border-[#2b2d32] bg-[#191b20] px-3 text-xs text-[#9fa1b1] hover:bg-[#121519] hover:text-white"
          >
            <Link href={`/max/${project.id}/integrations`}>
              <span className="flex items-center gap-2">
                <Plug className="h-3.5 w-3.5 text-[#4f81f7]" />
                Интеграции
              </span>
              <ChevronRight className="h-3.5 w-3.5 text-[#828491]" />
            </Link>
          </Button>
          {currentStage?.id === "publish" && (
            <MaxLaunchButton projectId={project.id} />
          )}
          <RuntimeButton projectId={project.id} display="compact" />
        </section>

        {busyDeploy && (
          <div className="mt-4 flex items-center gap-2 border-t border-[#25272b] pt-4 text-[11px] text-[#9fa1b1]">
            <Loader2 className="h-3.5 w-3.5 animate-spin text-warning" />
            Публикация продолжается в фоне.
          </div>
        )}

        {productionUrl && (
          <div className="mt-5 border-t border-[#25272b] pt-4">
            <div className="flex items-center gap-2 text-xs font-medium text-success">
              <ShieldCheck className="h-4 w-4" />
              Production URL готов
            </div>
            <p className="mt-2 text-[11px] leading-4 text-[#9fa1b1]">
              Добавьте адрес приложения к кнопке «Открыть» в кабинете MAX.
            </p>
            <a
              href={productionUrl}
              target="_blank"
              rel="noreferrer"
              data-testid="max-launch-app-url"
              className="mt-2 block truncate font-mono text-[10px] text-[#9fa1b1] hover:text-white hover:underline"
              title={productionUrl}
            >
              {productionUrl}
            </a>
            <a
              href="https://business.max.ru/"
              target="_blank"
              rel="noreferrer"
              onClick={openMaxCabinet}
              data-testid="max-open-business-cabinet"
              className="mt-2 inline-flex min-h-11 items-center gap-1 text-xs font-medium text-[#6a95fa] hover:underline"
            >
              Открыть кабинет MAX
              <ExternalLink className="h-3 w-3" aria-hidden="true" />
            </a>
          </div>
        )}
      </div>
    </aside>
  );
}
