"use client";

import { useEffect, useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Loader2, Rocket } from "lucide-react";

import { Button } from "@/components/ui/button";
import { getMaxReadiness } from "@/lib/api/max-studio";
import { getLastDeploy } from "@/lib/api/runtime";
import { useMaxPublicationGuard } from "@/hooks/useMaxPublicationGuard";
import { guardedMaxReadiness } from "@/lib/max-publication-guard";
import { PUBLICATION_REASON_LABELS } from "@/lib/max-publication-progress";
import { getMaxLaunchErrorDescription } from "@/lib/max-launch-error";
import { MaxLaunchCheckpointError, launchMaxProject, prepareMaxLaunch, readMaxLaunch } from "@/lib/max-launch-runner";

export function MaxLaunchButton({ projectId }: { projectId: string }) {
  const qc = useQueryClient();
  const launchRequested = useRef(false);
  const readiness = useQuery({
    queryKey: ["max-readiness", projectId],
    queryFn: () => getMaxReadiness(projectId),
    retry: false,
  });
  const deploy = useQuery({ queryKey: ["deploy", projectId], queryFn: () => getLastDeploy(projectId),
    enabled: readiness.isSuccess, retry: false });
  const guard = useMaxPublicationGuard(projectId, deploy.data);
  const effectiveReadiness = guardedMaxReadiness(readiness.data, guard);
  const canCheck = readiness.isSuccess && guard !== "checking";
  const required = new Set(["legal", "build", "bot"]);
  const blockers = (effectiveReadiness?.items ?? []).filter(
    (item) => required.has(item.id) && !item.done,
  );
  const launch = useMutation({
    mutationFn: () => launchMaxProject(projectId,
      (status) => qc.setQueryData(["deploy", projectId], status)),
    onSuccess: () => {
      window.localStorage.removeItem(`omnia:max:launch:${projectId}`);
      void qc.invalidateQueries({ queryKey: ["max-integration", projectId] });
      void qc.invalidateQueries({ queryKey: ["max-readiness", projectId] });
      void qc.invalidateQueries({ queryKey: ["deploy", projectId] });
    },
    onSettled: () => {
      launchRequested.current = false;
    },
  });

  useEffect(() => {
    const saved = readMaxLaunch(projectId);
    if (
      !launchRequested.current &&
      canCheck &&
      blockers.length === 0 &&
      saved && !saved.paused
    ) {
      launchRequested.current = true;
      launch.mutate();
    }
  }, [blockers.length, canCheck, launch, projectId]);

  function startLaunch() {
    if (launchRequested.current || launch.isPending || !canCheck || blockers.length > 0) return;
    prepareMaxLaunch(projectId);
    launchRequested.current = true;
    launch.mutate();
  }

  if (canCheck && effectiveReadiness?.ready_to_launch && !launch.isPending && !launch.isError) {
    return (
      <div className="flex h-10 items-center justify-center gap-2 rounded-xl border border-success/25 bg-success/[0.06] text-xs font-medium text-success">
        <CheckCircle2 className="h-4 w-4" />
        Полностью готово к запуску в MAX
      </div>
    );
  }

  const technicalReady = ["bot", "publish"].every(
    (id) => effectiveReadiness?.items.find((item) => item.id === id)?.done,
  );
  const maxUrlReady =
    effectiveReadiness?.items.find((item) => item.id === "max_url")?.done === true;
  if (canCheck && technicalReady && !maxUrlReady && !launch.isPending && !launch.isError && guard === "none") {
    return (
      <div className="rounded-xl border border-warning/25 bg-warning/[0.06] px-3 py-2.5 text-center text-[11px] leading-4 text-fg-secondary">
        Вставьте HTTPS-адрес в кабинете MAX, затем подтвердите это в настройках.
      </div>
    );
  }

  return (
    <div className="w-full">
      <Button
        className="h-11 w-full gap-2 rounded-xl"
        disabled={!canCheck || blockers.length > 0 || launch.isPending}
        onClick={startLaunch}
        title={
          blockers.length
            ? `Сначала: ${blockers.map((item) => item.label).join(", ")}`
            : "Опубликовать проверенное приложение по постоянному адресу"
        }
        data-testid="max-one-click-launch"
      >
        {launch.isPending ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : (
          <Rocket className="h-4 w-4" />
        )}
        {launch.isPending ? "Публикуем…" : launch.isError && readMaxLaunch(projectId) ? "Повторить проверку" : "Опубликовать приложение"}
      </Button>
      {guard === "migration_required" && <p role="alert" className="mt-2 text-xs leading-5 text-danger-fg">{PUBLICATION_REASON_LABELS.migration_required} Публикация недоступна до проверки данных текущей версии.</p>}
      {guard === "checking" && <p role="status" className="mt-2 text-xs leading-5 text-fg-secondary">Проверяем, относится ли требование миграции к текущей версии приложения.</p>}
      {launch.isError && (
        <p role={launch.error instanceof MaxLaunchCheckpointError ? "status" : "alert"} className={`mt-2 text-xs leading-5 ${launch.error instanceof MaxLaunchCheckpointError ? "text-fg-secondary" : "text-danger-fg"}`}>
          {getMaxLaunchErrorDescription(launch.error)}
        </p>
      )}
    </div>
  );
}
