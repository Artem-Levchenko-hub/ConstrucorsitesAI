"use client";

/**
 * V2 — runtime + deploy controls for the TopBar.
 *
 * Per state, rendered controls:
 *   - `stopped` / `failed` / null  → "Запустить" / "Повторить"
 *   - `provisioning`               → "Запускается…" (disabled, spinner)
 *   - `running`                    → "Пауза" (secondary) + "Опубликовать" (primary)
 *   - `paused`                     → "Разбудить" (primary, breath-pulse badge)
 *
 * Why split `paused` from `running` (was both lumped under `isUpish`):
 * the previous UI rendered the Pause button on top of an already-paused
 * container, so the owner would click "Пауза" again, backend would
 * idempotent-respond, and nothing visible changed — dead loop. Now paused
 * has a single primary "Разбудить" CTA, and the badge breathes so it's
 * obvious the project is asleep.
 *
 * Deploy button conditions are now explicit per state (see `deployUx`
 * helper below) with hover-tooltips explaining each disabled case.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CircleCheck,
  CircleX,
  Infinity as InfinityIcon,
  Loader2,
  Pause,
  Play,
  Rocket,
  Square,
} from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { toast } from "sonner";
import {
  cancelDeploy,
  deployProject,
  getLastDeploy,
  getRuntime,
  setRuntimeKeepAlive,
  startRuntime,
  stopRuntime,
} from "@/lib/api/runtime";
import type { RuntimeState } from "@/lib/api/types";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

const STATE_LABEL: Record<RuntimeState, string> = {
  provisioning: "Готовится…",
  running: "Запущен",
  paused: "Спит — разбудить?",
  stopped: "Остановлен",
  failed: "Ошибка",
};

const STATE_COLOR: Record<RuntimeState, string> = {
  provisioning: "text-warning",
  running: "text-success",
  paused: "text-warning",
  stopped: "text-fg-tertiary",
  failed: "text-danger",
};

function StateIcon({ state }: { state: RuntimeState }) {
  const cls = cn("h-3 w-3", STATE_COLOR[state]);
  if (state === "provisioning")
    return <Loader2 className={cn(cls, "animate-spin")} />;
  if (state === "running") return <CircleCheck className={cls} />;
  if (state === "paused")
    // Breathing dot — owner asked for clearer "this is paused, click me" cue.
    return (
      <motion.span
        aria-hidden="true"
        className="inline-block h-2 w-2 rounded-full bg-warning"
        animate={{ scale: [1, 1.25, 1], opacity: [1, 0.55, 1] }}
        transition={{ duration: 1.6, repeat: Infinity, ease: "easeInOut" }}
      />
    );
  if (state === "failed") return <CircleX className={cls} />;
  return <Square className={cls} />;
}

type DeployUx = {
  enabled: boolean;
  label: string;
  tooltip: string;
};

/**
 * Single source of truth for what the Deploy button shows and whether it's
 * clickable. Owner asked for "более явные условия" — every state maps to one
 * row here so the UI is debuggable from a glance.
 */
function deployUx(state: RuntimeState, deploying: boolean): DeployUx {
  if (deploying)
    return {
      enabled: false,
      label: "Публикуем в прод…",
      tooltip: "Сборка prod-образа уже идёт",
    };
  switch (state) {
    case "running":
      return {
        enabled: true,
        label: "Опубликовать в прод",
        tooltip: "Собрать prod-образ и переключить трафик",
      };
    case "paused":
      return {
        enabled: false,
        label: "Опубликовать в прод",
        tooltip: "Сначала разбудите контейнер",
      };
    case "provisioning":
      return {
        enabled: false,
        label: "Опубликовать в прод",
        tooltip: "Дождитесь окончания запуска",
      };
    case "failed":
      return {
        enabled: false,
        label: "Опубликовать в прод",
        tooltip: "Сначала перезапустите контейнер",
      };
    case "stopped":
    default:
      return {
        enabled: false,
        label: "Опубликовать в прод",
        tooltip: "Сначала запустите контейнер",
      };
  }
}

export function RuntimeButton({
  projectId,
  display = "toolbar",
}: {
  projectId: string;
  display?: "toolbar" | "panel";
}) {
  const qc = useQueryClient();
  const panel = display === "panel";

  const { data: runtime, isPending } = useQuery({
    queryKey: ["runtime", projectId],
    queryFn: () => getRuntime(projectId),
    refetchInterval: (q) =>
      q.state.data?.state === "provisioning" ? 2_000 : false,
    retry: false,
  });
  const deployQuery = useQuery({
    queryKey: ["deploy", projectId],
    queryFn: () => getLastDeploy(projectId),
    refetchInterval: (query) =>
      ["building", "pushing", "swapping", "cancelling"].includes(
        query.state.data?.phase ?? "",
      )
        ? 1_500
        : false,
    retry: false,
  });

  const startMut = useMutation({
    mutationFn: () => startRuntime(projectId),
    onSuccess: (s) => {
      qc.setQueryData(["runtime", projectId], s);
      toast.success("Контейнер запущен", {
        description: s.dev_url ? `dev preview: ${s.dev_url}` : undefined,
      });
    },
    onError: (err: unknown) => {
      const msg = err instanceof Error ? err.message : "не удалось стартовать";
      toast.error("Не удалось запустить", { description: msg });
    },
  });

  const stopMut = useMutation({
    mutationFn: () => stopRuntime(projectId, true),
    onSuccess: (s) => {
      qc.setQueryData(["runtime", projectId], s);
      toast.success("Контейнер приостановлен");
    },
    onError: (err: unknown) => {
      const msg = err instanceof Error ? err.message : "не удалось остановить";
      toast.error("Не удалось остановить", { description: msg });
    },
  });

  const keepAliveMut = useMutation({
    mutationFn: (enabled: boolean) => setRuntimeKeepAlive(projectId, enabled),
    onSuccess: (s) => {
      qc.setQueryData(["runtime", projectId], s);
      toast.success(
        s.keep_alive ? "Контейнер всегда запущен" : "Автоусыпление включено",
        {
          description: s.keep_alive
            ? "Он не уснёт от бездействия и восстановится после остановки."
            : "Контейнер продолжит работать сейчас, но сможет уснуть при простое.",
        },
      );
    },
    onError: (err: unknown) => {
      toast.error("Не удалось изменить режим контейнера", {
        description: err instanceof Error ? err.message : undefined,
      });
    },
  });

  const deployMut = useMutation({
    mutationFn: () => deployProject(projectId),
    onSuccess: (d) => {
      qc.setQueryData(["deploy", projectId], d);
      toast.success("Деплой запущен", {
        description:
          d.phase === "done" ? d.prod_url ?? "готово" : `фаза: ${d.phase}`,
      });
    },
    onError: (err: unknown) => {
      const msg = err instanceof Error ? err.message : "не удалось задеплоить";
      toast.error("Деплой не удался", { description: msg });
    },
  });
  const cancelMut = useMutation({
    mutationFn: () => cancelDeploy(projectId),
    onSuccess: (d) => {
      qc.setQueryData(["deploy", projectId], d);
      toast.message("Деплой остановлен", {
        description: "Предыдущая опубликованная версия продолжает работать.",
      });
    },
    onError: (err: unknown) => {
      toast.error("Не удалось остановить деплой", {
        description: err instanceof Error ? err.message : undefined,
      });
    },
  });

  const state: RuntimeState = runtime?.state ?? "stopped";
  const activeDeploy = ["building", "pushing", "swapping", "cancelling"].includes(
    deployQuery.data?.phase ?? "",
  );
  const busy =
    startMut.isPending ||
    stopMut.isPending ||
    keepAliveMut.isPending ||
    deployMut.isPending ||
    activeDeploy;

  if (isPending) {
    return (
      <Badge
        variant="outline"
        className={cn(
          "gap-1.5 px-2 py-1 text-[11px]",
          panel && "h-10 w-full justify-center rounded-xl",
        )}
      >
        <Loader2 className="h-3 w-3 animate-spin" />
        {panel ? "Проверяем готовность…" : "runtime…"}
      </Badge>
    );
  }

  const deploy = deployUx(state, deployMut.isPending || activeDeploy);
  const deployPhaseLabel: Record<string, string> = {
    building: "Собираем…",
    pushing: "Передаём…",
    swapping: "Проверяем и переключаем…",
    cancelling: "Останавливаем…",
  };

  return (
    <div
      className={cn(
        "flex gap-1",
        panel ? "flex-col items-stretch gap-2" : "items-center",
      )}
    >
      <Badge
        variant="outline"
        className={cn(
          "gap-1.5 px-2 py-1 text-[11px] font-normal whitespace-nowrap transition-colors",
          state === "paused" && "border-warning/40 bg-warning/[0.06]",
          panel && "h-9 w-full justify-center rounded-xl",
        )}
        title={
          runtime?.dev_url ? `dev: ${runtime.dev_url}` : "контейнер не запущен"
        }
      >
        <StateIcon state={state} />
        {/* Crossfade label on state change so transitions feel intentional */}
        <AnimatePresence mode="wait" initial={false}>
          <motion.span
            key={state}
            initial={{ opacity: 0, y: 2 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -2 }}
            transition={{ duration: 0.15 }}
          >
            {STATE_LABEL[state]}
          </motion.span>
        </AnimatePresence>
        {runtime?.port && (
          <span className="font-mono text-fg-tertiary">:{runtime.port}</span>
        )}
      </Badge>

      <Button
        size="sm"
        variant={runtime?.keep_alive ? "secondary" : "ghost"}
        aria-pressed={runtime?.keep_alive ?? false}
        disabled={busy}
        onClick={() => keepAliveMut.mutate(!runtime?.keep_alive)}
        className={cn(
          "gap-1.5 h-7 px-2 text-xs",
          runtime?.keep_alive && "text-success",
          panel && "h-9 w-full rounded-xl",
        )}
        title={
          runtime?.keep_alive
            ? "Отключить постоянную работу и снова разрешить автоусыпление"
            : "Не усыплять dev-контейнер и автоматически поднимать его после остановки"
        }
      >
        {keepAliveMut.isPending ? (
          <Loader2 className="h-3 w-3 animate-spin" />
        ) : (
          <InfinityIcon className="h-3 w-3" />
        )}
        {runtime?.keep_alive ? "Всегда запущено" : "Не усыплять"}
      </Button>

      {activeDeploy && (
        <Badge
          variant="accent"
          className={cn(
            "gap-1.5 px-2 py-1 text-[11px]",
            panel && "h-9 w-full justify-center rounded-xl",
          )}
          title={deployQuery.data?.detail ?? "Деплой выполняется"}
        >
          <Loader2 className="h-3 w-3 animate-spin" />
          {deployPhaseLabel[deployQuery.data?.phase ?? ""] ?? "Публикуем…"}
        </Badge>
      )}
      {deployQuery.data?.phase === "failed" && (
        <Badge
          variant="danger"
          className={cn(
            "max-w-56 truncate px-2 py-1 text-[11px]",
            panel && "h-9 w-full max-w-none justify-center rounded-xl",
          )}
          title={deployQuery.data.error ?? undefined}
        >
          Ошибка публикации
        </Badge>
      )}
      {deployQuery.data?.phase === "done" && deployQuery.data.prod_url && (
        <a
          href={deployQuery.data.prod_url}
          target="_blank"
          rel="noreferrer"
          className={cn(
            "text-[11px] text-accent hover:underline",
            panel && "text-center",
          )}
        >
          {panel ? "Открыть опубликованное приложение" : "Открыть сайт"}
        </a>
      )}
      {activeDeploy && deployQuery.data?.can_cancel && (
        <Button
          size="sm"
          variant="ghost"
          className={cn("h-7 px-2 text-xs", panel && "h-9 w-full")}
          disabled={cancelMut.isPending}
          onClick={() => cancelMut.mutate()}
          title="Остановить текущую сборку; опубликованная версия останется доступна"
        >
          {cancelMut.isPending ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            "Остановить"
          )}
        </Button>
      )}

      {state === "running" && (
        <>
          {!panel && !runtime?.keep_alive && (
            <Button
              size="sm"
              variant="secondary"
              disabled={busy}
              onClick={() => stopMut.mutate()}
              className="gap-1.5 h-7 px-2 text-xs"
              title="Приостановить dev-контейнер (можно будет разбудить одним кликом)"
            >
              <Pause className="h-3 w-3" />
              Пауза
            </Button>
          )}
          <Button
            size="sm"
            disabled={!deploy.enabled}
            onClick={() => deployMut.mutate()}
            className={cn(
              "gap-1.5 h-7 px-2.5 text-xs",
              panel && "h-10 w-full rounded-xl",
            )}
            title={deploy.tooltip}
          >
            {deployMut.isPending || activeDeploy ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <Rocket className="h-3 w-3" />
            )}
            {deploy.label}
          </Button>
        </>
      )}

      {state === "paused" && (
        <Button
          size="sm"
          disabled={busy}
          onClick={() => startMut.mutate()}
          className={cn(
            "gap-1.5 h-7 px-2.5 text-xs",
            panel && "h-10 w-full rounded-xl",
          )}
          title="Поднять контейнер из паузы — секунда, не полный перезапуск"
        >
          {startMut.isPending ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <Play className="h-3 w-3" />
          )}
          Разбудить
        </Button>
      )}

      {(state === "stopped" ||
        state === "failed" ||
        state === "provisioning") && (
        <Button
          size="sm"
          disabled={busy || state === "provisioning"}
          onClick={() => startMut.mutate()}
          className={cn(
            "gap-1.5 h-7 px-2.5 text-xs",
            panel && "h-10 w-full rounded-xl",
          )}
          title={
            state === "provisioning"
              ? "Контейнер уже поднимается, подождите"
              : state === "failed"
                ? "Перезапустить — предыдущий запуск завершился ошибкой"
                : "Запустить dev-контейнер"
          }
        >
          {startMut.isPending || state === "provisioning" ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <Play className="h-3 w-3" />
          )}
          {state === "failed" ? "Повторить" : "Запустить"}
        </Button>
      )}
    </div>
  );
}
