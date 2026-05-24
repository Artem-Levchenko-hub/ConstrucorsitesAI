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
  Loader2,
  Pause,
  Play,
  Rocket,
  Square,
} from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { toast } from "sonner";
import {
  deployProject,
  getRuntime,
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
      label: "Публикуется…",
      tooltip: "Сборка prod-образа уже идёт",
    };
  switch (state) {
    case "running":
      return {
        enabled: true,
        label: "Опубликовать",
        tooltip: "Собрать prod-образ и переключить трафик",
      };
    case "paused":
      return {
        enabled: false,
        label: "Опубликовать",
        tooltip: "Сначала разбудите контейнер",
      };
    case "provisioning":
      return {
        enabled: false,
        label: "Опубликовать",
        tooltip: "Дождитесь окончания запуска",
      };
    case "failed":
      return {
        enabled: false,
        label: "Опубликовать",
        tooltip: "Сначала перезапустите контейнер",
      };
    case "stopped":
    default:
      return {
        enabled: false,
        label: "Опубликовать",
        tooltip: "Сначала запустите контейнер",
      };
  }
}

export function RuntimeButton({ projectId }: { projectId: string }) {
  const qc = useQueryClient();

  const { data: runtime, isPending } = useQuery({
    queryKey: ["runtime", projectId],
    queryFn: () => getRuntime(projectId),
    refetchInterval: (q) =>
      q.state.data?.state === "provisioning" ? 2_000 : false,
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

  const deployMut = useMutation({
    mutationFn: () => deployProject(projectId),
    onSuccess: (d) => {
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

  const state: RuntimeState = runtime?.state ?? "stopped";
  const busy = startMut.isPending || stopMut.isPending || deployMut.isPending;

  if (isPending) {
    return (
      <Badge variant="outline" className="gap-1.5 px-2 py-1 text-[11px]">
        <Loader2 className="h-3 w-3 animate-spin" />
        runtime…
      </Badge>
    );
  }

  const deploy = deployUx(state, deployMut.isPending);

  return (
    <div className="flex items-center gap-1">
      <Badge
        variant="outline"
        className={cn(
          "gap-1.5 px-2 py-1 text-[11px] font-normal whitespace-nowrap transition-colors",
          state === "paused" && "border-warning/40 bg-warning/[0.06]",
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

      {state === "running" && (
        <>
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
          <Button
            size="sm"
            disabled={!deploy.enabled}
            onClick={() => deployMut.mutate()}
            className="gap-1.5 h-7 px-2.5 text-xs"
            title={deploy.tooltip}
          >
            {deployMut.isPending ? (
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
          className="gap-1.5 h-7 px-2.5 text-xs"
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
          className="gap-1.5 h-7 px-2.5 text-xs"
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
