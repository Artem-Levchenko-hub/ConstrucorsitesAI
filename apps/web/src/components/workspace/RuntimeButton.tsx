"use client";

/**
 * V2 — runtime + deploy controls for the TopBar.
 *
 * Renders one compact button that shows the current runtime state and morphs
 * its action based on context:
 *   - `stopped` / `failed` / `null`  → "Запустить" (POST /runtime/start)
 *   - `provisioning`                  → "Запускается…" (disabled, spinner)
 *   - `running`                       → "Остановить" (secondary) +
 *                                       primary "Деплой" button next to it
 *   - `paused`                        → "Разбудить" (POST /runtime/start)
 *
 * Phase A scope: we don't render a full RuntimePanel with logs yet — that's
 * Phase A.5. This button is the smallest possible surface that lets a beta
 * tester provision + see a dev container, plus trigger a deploy.
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
  paused: "На паузе",
  stopped: "Остановлен",
  failed: "Ошибка",
};

const STATE_COLOR: Record<RuntimeState, string> = {
  provisioning: "text-warning",
  running: "text-success",
  paused: "text-fg-secondary",
  stopped: "text-fg-tertiary",
  failed: "text-danger",
};

function StateIcon({ state }: { state: RuntimeState }) {
  const cls = cn("h-3 w-3", STATE_COLOR[state]);
  if (state === "provisioning") return <Loader2 className={cn(cls, "animate-spin")} />;
  if (state === "running") return <CircleCheck className={cls} />;
  if (state === "paused") return <Pause className={cls} />;
  if (state === "failed") return <CircleX className={cls} />;
  return <Square className={cls} />;
}

export function RuntimeButton({ projectId }: { projectId: string }) {
  const qc = useQueryClient();

  const { data: runtime, isPending } = useQuery({
    queryKey: ["runtime", projectId],
    queryFn: () => getRuntime(projectId),
    // First call is heavy on orchestrator side — don't poll while paused.
    refetchInterval: (q) =>
      q.state.data?.state === "provisioning" ? 2_000 : false,
    // GET /runtime returns 503 until first runtime/start (provision-on-call).
    // Treat 503 as "stopped" so the UI shows a sensible default rather than
    // an error toast on first paint.
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
          d.phase === "done"
            ? d.prod_url ?? "готово"
            : `фаза: ${d.phase}`,
      });
    },
    onError: (err: unknown) => {
      const msg = err instanceof Error ? err.message : "не удалось задеплоить";
      toast.error("Деплой не удался", { description: msg });
    },
  });

  // Until /runtime returns a real state the safe default is "stopped" — that
  // makes the button say "Запустить", which provisions on first click.
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

  // Primary action depends on current state. Single button keeps the TopBar
  // dense — full RuntimePanel ships in a later iteration.
  const isUpish = state === "running" || state === "paused";

  return (
    <div className="flex items-center gap-1">
      <Badge
        variant="outline"
        className="gap-1.5 px-2 py-1 text-[11px] font-normal whitespace-nowrap"
        title={
          runtime?.dev_url ? `dev: ${runtime.dev_url}` : "контейнер не запущен"
        }
      >
        <StateIcon state={state} />
        {STATE_LABEL[state]}
        {runtime?.port && (
          <span className="font-mono text-fg-tertiary">:{runtime.port}</span>
        )}
      </Badge>

      {isUpish ? (
        <>
          <Button
            size="sm"
            variant="secondary"
            disabled={busy}
            onClick={() => stopMut.mutate()}
            className="gap-1.5 h-7 px-2 text-xs"
            title="Приостановить dev-контейнер"
          >
            <Pause className="h-3 w-3" />
            Пауза
          </Button>
          <Button
            size="sm"
            disabled={busy}
            onClick={() => deployMut.mutate()}
            className="gap-1.5 h-7 px-2.5 text-xs"
            title="Собрать prod-образ и заменить трафик"
          >
            {deployMut.isPending ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <Rocket className="h-3 w-3" />
            )}
            Деплой
          </Button>
        </>
      ) : (
        <Button
          size="sm"
          disabled={busy}
          onClick={() => startMut.mutate()}
          className="gap-1.5 h-7 px-2.5 text-xs"
          title="Запустить или восстановить dev-контейнер"
        >
          {startMut.isPending ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <Play className="h-3 w-3" />
          )}
          {state === "stopped" || state === "failed" ? "Запустить" : "Разбудить"}
        </Button>
      )}
    </div>
  );
}
