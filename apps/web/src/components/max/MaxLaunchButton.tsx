"use client";

import { useEffect, useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Loader2, Rocket } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { getMaxReadiness } from "@/lib/api/max-studio";
import { getMaxLaunchErrorDescription } from "@/lib/max-launch-error";
import { finishMaxLaunch, prepareMaxLaunch, readMaxLaunch } from "@/lib/max-launch-runner";
import { runMaxLaunchSingleFlight } from "@/lib/max-launch-single-flight";

export function MaxLaunchButton({ projectId }: { projectId: string }) {
  const qc = useQueryClient();
  const launchRequested = useRef(false);
  const readiness = useQuery({
    queryKey: ["max-readiness", projectId],
    queryFn: () => getMaxReadiness(projectId),
    retry: false,
  });
  const required = new Set(["business", "legal", "build", "bot"]);
  const blockers = (readiness.data?.items ?? []).filter(
    (item) => required.has(item.id) && !item.done,
  );
  const launch = useMutation({
    mutationFn: () => runMaxLaunchSingleFlight(projectId, () => finishMaxLaunch(projectId,
      (status) => qc.setQueryData(["deploy", projectId], status))),
    onSuccess: () => {
      window.localStorage.removeItem(`omnia:max:launch:${projectId}`);
      void qc.invalidateQueries({ queryKey: ["max-integration", projectId] });
      void qc.invalidateQueries({ queryKey: ["max-readiness", projectId] });
      void qc.invalidateQueries({ queryKey: ["deploy", projectId] });
      toast.success("Приложение опубликовано и подключено", {
        id: `max-launch-success:${projectId}`,
        description:
          "Production URL готов, безопасный вход и webhook подключены. Осталось вставить адрес в MAX Partner.",
      });
    },
    onError: (error: unknown) => {
      toast.error("Автозапуск не завершён", {
        id: `max-launch-error:${projectId}`,
        description: getMaxLaunchErrorDescription(error),
      });
    },
    onSettled: () => {
      launchRequested.current = false;
    },
  });

  useEffect(() => {
    const saved = readMaxLaunch(projectId);
    if (
      !launchRequested.current &&
      readiness.isSuccess &&
      blockers.length === 0 &&
      saved && !saved.paused
    ) {
      launchRequested.current = true;
      launch.mutate();
    }
  }, [blockers.length, launch, projectId, readiness.isSuccess]);

  function startLaunch() {
    if (launchRequested.current || launch.isPending) return;
    prepareMaxLaunch(projectId);
    launchRequested.current = true;
    launch.mutate();
  }

  if (readiness.data?.ready_to_launch && !launch.isPending && !launch.isError) {
    return (
      <div className="flex h-10 items-center justify-center gap-2 rounded-xl border border-success/25 bg-success/[0.06] text-xs font-medium text-success">
        <CheckCircle2 className="h-4 w-4" />
        Полностью готово к запуску в MAX
      </div>
    );
  }

  const technicalReady = ["bot", "publish"].every(
    (id) => readiness.data?.items.find((item) => item.id === id)?.done,
  );
  const maxUrlReady =
    readiness.data?.items.find((item) => item.id === "max_url")?.done === true;
  if (technicalReady && !maxUrlReady && !launch.isPending && !launch.isError) {
    return (
      <div className="rounded-xl border border-warning/25 bg-warning/[0.06] px-3 py-2.5 text-center text-[11px] leading-4 text-white/55">
        Вставьте HTTPS-адрес в кабинете MAX, затем подтвердите это в настройках.
      </div>
    );
  }

  return (
    <div className="w-full">
      <Button
        className="h-11 w-full gap-2 rounded-xl"
        disabled={!readiness.isSuccess || blockers.length > 0 || launch.isPending}
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
        {launch.isPending ? "Публикуем…" : launch.isError && readMaxLaunch(projectId) ? "Повторить проверку" : "Опубликовать новую версию"}
      </Button>
      {launch.isError && (
        <p role="alert" className="mt-2 text-xs leading-5 text-danger-fg">
          {getMaxLaunchErrorDescription(launch.error)}
        </p>
      )}
    </div>
  );
}
