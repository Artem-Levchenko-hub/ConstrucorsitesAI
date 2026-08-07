"use client";

import { useEffect, useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Loader2, Rocket } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { activateMaxIntegration } from "@/lib/api/max-integration";
import { getMaxReadiness } from "@/lib/api/max-studio";
import {
  deployProject,
  getLastDeploy,
  getRuntime,
  startRuntime,
} from "@/lib/api/runtime";
import { runMaxLaunchSingleFlight } from "@/lib/max-launch-single-flight";

const ACTIVE_PHASES = new Set(["building", "pushing", "swapping", "cancelling"]);

function delay(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

async function finishLaunch(projectId: string) {
  const launchKey = `omnia:max:launch:${projectId}`;
  const savedPhase = window.localStorage.getItem(launchKey) ?? "new";
  const runtime = await getRuntime(projectId);
  if (runtime.state !== "running") await startRuntime(projectId);

  let deployment = await getLastDeploy(projectId);
  if (
    savedPhase === "new" &&
    !ACTIVE_PHASES.has(deployment.phase)
  ) {
    deployment = await deployProject(projectId);
    window.localStorage.setItem(launchKey, "deploying");
  } else if (
    deployment.phase !== "done" &&
    !ACTIVE_PHASES.has(deployment.phase)
  ) {
    deployment = await deployProject(projectId);
    window.localStorage.setItem(launchKey, "deploying");
  }

  for (let attempt = 0; attempt < 450 && deployment.phase !== "done"; attempt += 1) {
    if (deployment.phase === "failed") {
      throw new Error(deployment.error || "Публикация не завершилась");
    }
    await delay(2_000);
    deployment = await getLastDeploy(projectId);
  }
  if (deployment.phase !== "done") {
    throw new Error("Публикация занимает слишком много времени. Студия продолжит её в фоне.");
  }
  window.localStorage.setItem(launchKey, "activating");
  return activateMaxIntegration(projectId);
}

export function MaxLaunchButton({ projectId }: { projectId: string }) {
  const qc = useQueryClient();
  const launchRequested = useRef(false);
  const readiness = useQuery({
    queryKey: ["max-readiness", projectId],
    queryFn: () => getMaxReadiness(projectId),
    retry: false,
  });
  const required = new Set([
    "business",
    "legal",
    "build",
    "max_business",
    "plan",
    "bot",
  ]);
  const blockers = (readiness.data?.items ?? []).filter(
    (item) => required.has(item.id) && !item.done,
  );
  const launch = useMutation({
    mutationFn: () => runMaxLaunchSingleFlight(projectId, () => finishLaunch(projectId)),
    onMutate: () => {
      const key = `omnia:max:launch:${projectId}`;
      if (!window.localStorage.getItem(key)) window.localStorage.setItem(key, "new");
    },
    onSuccess: () => {
      window.localStorage.removeItem(`omnia:max:launch:${projectId}`);
      void qc.invalidateQueries({ queryKey: ["max-integration", projectId] });
      void qc.invalidateQueries({ queryKey: ["max-readiness", projectId] });
      void qc.invalidateQueries({ queryKey: ["deploy", projectId] });
      toast.success("Приложение опубликовано и подключено к MAX", {
        id: `max-launch-success:${projectId}`,
        description: "Осталось вставить HTTPS-адрес в кабинете MAX и подтвердить шаг.",
      });
    },
    onError: (error: unknown) => {
      window.localStorage.removeItem(`omnia:max:launch:${projectId}`);
      toast.error("Автозапуск не завершён", {
        id: `max-launch-error:${projectId}`,
        description: error instanceof Error ? error.message : "Повторите запуск",
      });
    },
    onSettled: () => {
      launchRequested.current = false;
    },
  });

  useEffect(() => {
    if (
      !launchRequested.current &&
      blockers.length === 0 &&
      window.localStorage.getItem(`omnia:max:launch:${projectId}`) !== null
    ) {
      launchRequested.current = true;
      launch.mutate();
    }
  }, [blockers.length, launch, projectId]);

  function startLaunch() {
    if (launchRequested.current || launch.isPending) return;
    launchRequested.current = true;
    launch.mutate();
  }

  if (readiness.data?.ready_to_launch) {
    return (
      <div className="flex h-10 items-center justify-center gap-2 rounded-xl border border-success/25 bg-success/[0.06] text-xs font-medium text-success">
        <CheckCircle2 className="h-4 w-4" />
        Полностью готово к запуску в MAX
      </div>
    );
  }

  const technicalReady = ["publish", "webhook"].every(
    (id) => readiness.data?.items.find((item) => item.id === id)?.done,
  );
  const maxUrlReady =
    readiness.data?.items.find((item) => item.id === "max_url")?.done === true;
  if (technicalReady && !maxUrlReady) {
    return (
      <div className="rounded-xl border border-warning/25 bg-warning/[0.06] px-3 py-2.5 text-center text-[11px] leading-4 text-white/55">
        Вставьте HTTPS-адрес в кабинете MAX, затем подтвердите это в настройках.
      </div>
    );
  }

  return (
    <Button
      className="h-11 w-full gap-2 rounded-xl"
      disabled={readiness.isLoading || blockers.length > 0 || launch.isPending}
      onClick={startLaunch}
      title={
        blockers.length
          ? `Сначала: ${blockers.map((item) => item.label).join(", ")}`
          : "Зафиксировать зелёную версию и переключить на неё постоянный URL"
      }
      data-testid="max-one-click-launch"
    >
      {launch.isPending ? (
        <Loader2 className="h-4 w-4 animate-spin" />
      ) : (
        <Rocket className="h-4 w-4" />
      )}
      {launch.isPending ? "Публикуем и подключаем…" : "Опубликовать новую версию"}
    </Button>
  );
}
