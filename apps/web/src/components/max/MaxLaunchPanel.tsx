"use client";

import { useQuery } from "@tanstack/react-query";
import {
  Check,
  ChevronDown,
  ChevronRight,
  ExternalLink,
  Loader2,
  PanelRightClose,
  Plug,
  ShieldCheck,
} from "lucide-react";
import Link from "next/link";

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
import { cn } from "@/lib/utils";

const NEXT_STEP_COPY: Record<string, string> = {
  business: "Добавьте описание продукта и контакты поддержки.",
  legal: "Заполните данные оператора и подтвердите обязательные условия.",
  build: "Завершите текущую сборку приложения в чате.",
  bot: "Вставьте секрет прошедшего модерацию MAX-бота.",
  publish: "Запустите публикацию — студия сама подготовит HTTPS-адрес.",
  webhook: "После публикации студия автоматически подключит webhook.",
  max_url: "Добавьте HTTPS-адрес в кабинете MAX и подтвердите этот шаг.",
};

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
      ["building", "pushing", "swapping", "cancelling"].includes(
        query.state.data?.phase ?? "",
      )
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

  const webhookActive = integration.data?.status === "active";
  const busyDeploy = ["building", "pushing", "swapping", "cancelling"].includes(
    deploy.data?.phase ?? "",
  );
  const items = readiness.data?.items ?? [];
  const readinessAvailable = readiness.isSuccess && items.length > 0;
  const completedCount = items.filter((item) => item.done).length;
  const nextItem = readinessAvailable
    ? items.find((item) => !item.done)
    : undefined;
  const nextStepLabel = readiness.isError
    ? "Не удалось проверить готовность"
    : !readinessAvailable
      ? "Проверяем готовность…"
      : nextItem?.label ?? "Всё готово";
  const nextStepCopy = readiness.isError
    ? "Обновите страницу или повторите попытку чуть позже."
    : !readinessAvailable
      ? "Статусы появятся после ответа сервера."
      : nextItem
        ? NEXT_STEP_COPY[nextItem.id] ?? "Завершите этот шаг, чтобы продолжить."
        : "Приложение готово к работе в MAX.";
  const configurationEmphasis = ["business", "legal", "max_url"].includes(
    nextItem?.id ?? "",
  );
  const botEmphasis = nextItem?.id === "bot";

  return (
    <aside className="max-launch-panel flex h-full min-h-0 flex-col border-l border-[#1e243f] bg-[#13172a]">
      <div className="flex h-12 shrink-0 items-center justify-between border-b border-[#1e243f] px-4">
        <div className="flex items-center gap-2.5">
          <span
            className={cn(
              "h-1.5 w-1.5 rounded-full",
              readiness.data?.ready_to_launch ? "bg-success" : "bg-[#8b5cf6]",
            )}
          />
          <span className="text-[10px] font-semibold uppercase tracking-[0.16em] text-slate-400">Публикация</span>
        </div>
        <button
          type="button"
          onClick={onClose ?? toggleTimeline}
          aria-label="Свернуть панель запуска"
          title="Свернуть панель запуска"
          className="-mr-1 flex h-7 w-7 items-center justify-center rounded-md text-white/35 transition-colors hover:bg-white/[0.06] hover:text-white/70"
        >
          <PanelRightClose className="h-3.5 w-3.5" />
        </button>
      </div>

      <div className="max-launch-panel-scroll max-studio-scroll flex-1 overflow-y-auto p-4">
        <section aria-labelledby="max-launch-heading">
          <div className="flex items-end justify-between gap-3">
            <div>
              <p className="text-[10px] font-medium uppercase tracking-[0.16em] text-white/30">
                Готовность
              </p>
              <h2 id="max-launch-heading" className="mt-1 text-base font-semibold">
                Запуск в MAX
              </h2>
            </div>
            <span className="tabular-nums text-sm font-medium text-white/65">
              {readinessAvailable ? `${readiness.data.progress}%` : "—"}
            </span>
          </div>
          <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-white/[0.08]">
            <div
              className="h-full rounded-full bg-[#3b82f6] transition-[width]"
              style={{ width: `${readiness.data?.progress ?? 0}%` }}
            />
          </div>

          <div className="mt-5 border-l-2 border-[#8b5cf6]/70 pl-3">
            <p className="text-[10px] font-medium uppercase tracking-[0.14em] text-white/30">
              Следующий шаг
            </p>
            <p className="mt-1 text-sm font-medium text-white/85">
              {nextStepLabel}
            </p>
            <p className="mt-1 text-[11px] leading-4 text-white/40">
              {nextStepCopy}
            </p>
          </div>
        </section>

        {items.length > 0 && (
          <details className="group mt-5 border-y border-white/[0.07] py-3">
            <summary className="flex cursor-pointer list-none items-center justify-between gap-3 text-xs text-white/55">
              <span>
                {completedCount} из {items.length} шагов готово
              </span>
              <ChevronDown className="h-3.5 w-3.5 transition-transform group-open:rotate-180" />
            </summary>
            <div className="space-y-2.5 pt-3">
              {items.map((item) => (
                <div key={item.id} className="flex items-start gap-2 text-[11px]">
                  <span
                    className={cn(
                      "mt-px flex h-4 w-4 shrink-0 items-center justify-center rounded-full border",
                      item.done
                        ? "border-success/45 bg-success/10 text-success"
                        : "border-white/[0.13] text-transparent",
                    )}
                  >
                    <Check className="h-2.5 w-2.5" />
                  </span>
                  <span className={item.done ? "text-white/35" : "text-white/65"}>
                    {item.label}
                  </span>
                </div>
              ))}
            </div>
          </details>
        )}

        <section className="mt-5 space-y-2.5" aria-label="Действия публикации">
          <div className="grid grid-cols-2 gap-2">
            <MaxProjectSetupDialog
              projectId={project.id}
              emphasized={configurationEmphasis}
              label={nextItem?.id === "max_url" ? "URL в MAX" : "Приложение"}
            />
            <MaxIntegrationButton
              projectId={project.id}
              initialTemplate={project.template}
              display="panel"
              emphasized={botEmphasis}
              label="MAX-бот"
            />
          </div>
          <Button
            asChild
            variant="secondary"
            className="h-10 w-full justify-between border-white/[0.1] bg-white/[0.035] px-3 text-xs text-white/70 hover:bg-white/[0.07] hover:text-white"
          >
            <Link href={`/max/${project.id}/integrations`}>
              <span className="flex items-center gap-2">
                <Plug className="h-3.5 w-3.5 text-[#8b5cf6]" />
                Интеграции
              </span>
              <ChevronRight className="h-3.5 w-3.5 text-white/25" />
            </Link>
          </Button>
          <MaxLaunchButton projectId={project.id} />
          <RuntimeButton projectId={project.id} display="compact" />
        </section>

        {busyDeploy && (
          <div className="mt-4 flex items-center gap-2 border-t border-white/[0.07] pt-4 text-[11px] text-white/45">
            <Loader2 className="h-3.5 w-3.5 animate-spin text-warning" />
            Публикация продолжается в фоне.
          </div>
        )}

        {webhookActive && integration.data?.app_url && (
          <div className="mt-5 border-t border-white/[0.07] pt-4">
            <div className="flex items-center gap-2 text-xs font-medium text-success">
              <ShieldCheck className="h-4 w-4" />
              Техническая часть готова
            </div>
            <p className="mt-2 text-[11px] leading-4 text-white/40">
              Добавьте URL приложения к кнопке «Открыть» в кабинете MAX.
            </p>
            <a
              href="https://business.max.ru/"
              target="_blank"
              rel="noreferrer"
              className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-[#8b5cf6] hover:underline"
            >
              Открыть кабинет MAX
              <ExternalLink className="h-3 w-3" />
            </a>
          </div>
        )}
      </div>
    </aside>
  );
}
