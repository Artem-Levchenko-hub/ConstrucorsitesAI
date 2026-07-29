"use client";

import { useQuery } from "@tanstack/react-query";
import {
  AlertCircle,
  Bot,
  Check,
  Circle,
  ExternalLink,
  Loader2,
  PanelRightClose,
  Rocket,
  ShieldCheck,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { getMaxIntegration } from "@/lib/api/max-integration";
import { getLatestGeneration } from "@/lib/api/messages";
import { getLastDeploy } from "@/lib/api/runtime";
import { listSnapshots } from "@/lib/api/snapshots";
import type { Project } from "@/lib/api/types";
import { useWorkspaceStore } from "@/store/workspace";
import { MaxIntegrationButton } from "@/components/workspace/MaxIntegrationButton";
import { RuntimeButton } from "@/components/workspace/RuntimeButton";
import {
  isGenerationActive,
  isMaxBuildReady,
} from "@/lib/generation-lifecycle";
import { cn } from "@/lib/utils";

type LaunchStep = {
  label: string;
  description: string;
  done: boolean;
  active?: boolean;
  error?: boolean;
};

export function MaxLaunchPanel({ project }: { project: Project }) {
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
  const snapshots = useQuery({
    queryKey: ["snapshots", project.id],
    queryFn: () => listSnapshots(project.id),
  });
  const generation = useQuery({
    queryKey: ["generation", project.id],
    queryFn: () => getLatestGeneration(project.id),
    refetchInterval: (query) =>
      ["pending", "running", "cancel_requested"].includes(
        query.state.data?.status ?? "",
      )
        ? 1_500
        : false,
  });

  const connected = integration.data?.connected === true;
  const published = deploy.data?.phase === "done" && !!deploy.data.prod_url;
  const webhookActive = integration.data?.status === "active";
  const busyDeploy = ["building", "pushing", "swapping", "cancelling"].includes(
    deploy.data?.phase ?? "",
  );
  const hasGeneratedSnapshot = (snapshots.data ?? []).some(
    (snapshot) => snapshot.prompt_text !== null,
  );
  const generationActive = isGenerationActive(generation.data);
  const latestBuildFailed =
    generation.data?.response_mode === "build" &&
    generation.data.status === "failed";
  const buildReady = isMaxBuildReady({
    snapshotsLoaded: snapshots.isSuccess,
    generationLoaded: generation.isSuccess,
    hasGeneratedSnapshot,
    generation: generation.data,
  });
  const steps: LaunchStep[] = [
    {
      label: generationActive
        ? hasGeneratedSnapshot
          ? "Обновляем приложение…"
          : "Собираем приложение…"
        : latestBuildFailed
          ? hasGeneratedSnapshot
            ? "Последнее обновление не завершено"
            : "Сборка не завершена"
          : hasGeneratedSnapshot
            ? "Приложение собрано"
            : "Соберите приложение",
      description: generationActive
        ? "Продолжаем текущую генерацию — перезагрузка её не запустит заново."
        : latestBuildFailed
          ? "Исправьте ошибку и повторите сборку."
          : hasGeneratedSnapshot
            ? "Проверьте экраны в мобильном превью."
            : "Первая генерация ещё не запускалась.",
      done: buildReady && !latestBuildFailed,
      active: generationActive || (!hasGeneratedSnapshot && !latestBuildFailed),
      error: latestBuildFailed,
    },
    {
      label: connected ? "MAX-бот подключён" : "Подключите MAX-бота",
      description: "Нужен секрет прошедшего модерацию бота.",
      done: connected,
      active: buildReady && !connected,
    },
    {
      label: published ? "Версия опубликована" : "Опубликуйте версию",
      description: "Получаем постоянный защищённый HTTPS-адрес.",
      done: published,
      active: buildReady && connected && !published,
    },
    {
      label: webhookActive ? "Webhook активирован" : "Активируйте webhook",
      description: "События MAX начнут приходить в приложение.",
      done: webhookActive,
      active: buildReady && connected && published && !webhookActive,
    },
  ];

  return (
    <aside className="flex h-full min-h-0 flex-col border-l border-white/[0.07] bg-[#0d0f16]">
      <div className="flex h-11 shrink-0 items-center justify-between border-b border-white/[0.07] px-4">
        <div className="flex items-center gap-2">
          <Rocket className="h-3.5 w-3.5 text-[#8d83ff]" />
          <span className="text-[10px] font-mono uppercase tracking-[0.16em] text-white/45">
            Запуск в MAX
          </span>
        </div>
        <button
          type="button"
          onClick={toggleTimeline}
          aria-label="Свернуть панель запуска"
          title="Свернуть панель запуска"
          className="-mr-1 flex h-7 w-7 items-center justify-center rounded-md text-white/35 transition-colors hover:bg-white/[0.06] hover:text-white/70"
        >
          <PanelRightClose className="h-3.5 w-3.5" />
        </button>
      </div>

      <div className="max-studio-scroll flex-1 space-y-5 overflow-y-auto p-4">
        <div>
          <h2 className="text-sm font-semibold">Готовность к публикации</h2>
          <p className="mt-1 text-xs leading-5 text-white/40">
            Делайте шаги сверху вниз. Технические настройки студия выполнит сама.
          </p>
        </div>

        <ol className="space-y-1">
          {steps.map((step, index) => (
            <li
              key={step.label}
              className={cn(
                "relative grid grid-cols-[24px_1fr] gap-2.5 rounded-xl px-2 py-2.5",
                step.active && "bg-[#7468ff]/8",
              )}
            >
              {index < steps.length - 1 && (
                <span className="absolute left-[19px] top-8 h-[calc(100%-16px)] w-px bg-white/[0.08]" />
              )}
              <span
                className={cn(
                  "relative z-10 mt-0.5 flex h-5 w-5 items-center justify-center rounded-full border",
                  step.done
                    ? "border-[#7468ff] bg-[#7468ff] text-white"
                    : step.error
                      ? "border-danger/70 bg-danger/10 text-danger"
                    : step.active
                      ? "border-[#7468ff]/60 bg-[#7468ff]/10 text-[#9b92ff]"
                      : "border-white/[0.12] bg-[#0d0f16] text-white/25",
                )}
              >
                {step.done ? (
                  <Check className="h-3 w-3" />
                ) : step.error ? (
                  <AlertCircle className="h-3 w-3" />
                ) : step.active ? (
                  <Circle className="h-2 w-2 fill-current" />
                ) : (
                  <span className="text-[9px]">{index + 1}</span>
                )}
              </span>
              <span>
                <span
                  className={cn(
                    "block text-xs font-medium",
                    step.done ? "text-white/75" : "text-white",
                  )}
                >
                  {step.label}
                </span>
                <span className="mt-0.5 block text-[11px] leading-4 text-white/35">
                  {step.description}
                </span>
              </span>
            </li>
          ))}
        </ol>

        <div className="space-y-2.5 border-t border-white/[0.07] pt-5">
          <MaxIntegrationButton
            projectId={project.id}
            initialTemplate={project.template}
            display="panel"
          />
          <RuntimeButton projectId={project.id} display="panel" />
        </div>

        {busyDeploy && (
          <div className="flex items-center gap-2 rounded-xl border border-warning/25 bg-warning/[0.06] p-3 text-xs text-white/55">
            <Loader2 className="h-3.5 w-3.5 animate-spin text-warning" />
            Публикация идёт. Панель обновится автоматически.
          </div>
        )}

        {webhookActive && integration.data?.app_url && (
          <div className="rounded-2xl border border-success/25 bg-success/[0.055] p-4">
            <div className="flex items-center gap-2 text-xs font-medium text-success">
              <ShieldCheck className="h-4 w-4" />
              Техническая часть готова
            </div>
            <p className="mt-2 text-[11px] leading-5 text-white/45">
              Остался один ручной шаг: вставьте URL приложения в настройках
              вашего бота на платформе MAX и выберите кнопку «Открыть».
            </p>
            <a
              href="https://business.max.ru/"
              target="_blank"
              rel="noreferrer"
              className="mt-3 inline-flex items-center gap-1 text-xs font-medium text-[#9b92ff] hover:underline"
            >
              Открыть кабинет MAX
              <ExternalLink className="h-3 w-3" />
            </a>
          </div>
        )}

        {!connected && (
          <div className="rounded-2xl border border-white/[0.08] bg-white/[0.025] p-4">
            <div className="flex items-center gap-2 text-xs font-medium">
              <Bot className="h-4 w-4 text-[#8d83ff]" />
              Что останется сделать в MAX
            </div>
            <p className="mt-2 text-[11px] leading-5 text-white/40">
              Создать и отправить бота на модерацию, затем один раз скопировать
              его секрет сюда. Создание и модерацию бота MAX пока не открывает
              через публичный API.
            </p>
          </div>
        )}

        {integration.data?.status === "active" && (
          <Badge variant="success" className="w-full justify-center py-2">
            MAX Mini App подключён
          </Badge>
        )}
      </div>
    </aside>
  );
}
