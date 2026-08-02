"use client";

import { useQuery } from "@tanstack/react-query";
import {
  Check,
  CircleAlert,
  Cloud,
  ExternalLink,
  Loader2,
  RefreshCw,
  Rocket,
  Server,
  ShieldCheck,
} from "lucide-react";
import Link from "next/link";

import { MaxLaunchButton } from "@/components/max/MaxLaunchButton";
import { MaxSectionShell } from "@/components/max/MaxSectionShell";
import { RuntimeButton } from "@/components/workspace/RuntimeButton";
import { Button } from "@/components/ui/button";
import { getMaxReadiness } from "@/lib/api/max-studio";
import { getLastDeploy, getRuntime } from "@/lib/api/runtime";
import { getMaxJourney } from "@/lib/max-journey";
import { getMaxPublicationState } from "@/lib/max-publication-state";

const activePhases = new Set(["queued", "building", "pushing", "swapping", "cancelling"]);

const phaseSteps = [
  ["queued", "Задача поставлена в очередь"],
  ["building", "Собираем production-образ"],
  ["pushing", "Загружаем образ и конфигурацию"],
  ["swapping", "Переключаем трафик и проверяем HTTPS"],
] as const;

export function MaxPublishWorkspace({ projectId, projectName }: { projectId: string; projectName: string }) {
  const deploy = useQuery({
    queryKey: ["deploy", projectId],
    queryFn: () => getLastDeploy(projectId),
    refetchInterval: (query) => activePhases.has(query.state.data?.phase ?? "") ? 1_500 : false,
    retry: false,
  });
  const runtime = useQuery({
    queryKey: ["runtime", projectId],
    queryFn: () => getRuntime(projectId),
    retry: false,
  });
  const readiness = useQuery({
    queryKey: ["max-readiness", projectId],
    queryFn: () => getMaxReadiness(projectId),
    refetchInterval: 10_000,
    retry: false,
  });

  const phase = deploy.data?.phase;
  const inProgress = activePhases.has(phase ?? "");
  const publicationState = getMaxPublicationState(readiness.data, phase);
  const complete = publicationState === "published";
  const outdated = publicationState === "outdated";
  const checkingPublication = phase === "done" && readiness.isLoading;
  const failed = phase === "failed";
  const currentIndex = Math.max(0, phaseSteps.findIndex(([id]) => id === phase));
  const journey = getMaxJourney(projectId, readiness.data?.items ?? []);

  return (
    <MaxSectionShell
      projectId={projectId}
      projectName={projectName}
      active="publish"
      eyebrow="Этап 5 из 6"
      title="Публикация"
      lead="Публикация заменяет версию за тем же постоянным HTTPS-адресом: Studio фиксирует зелёный snapshot, собирает production-образ и только потом переключает трафик."
    >
      {checkingPublication && (
        <section className="mt-8 flex items-center gap-4 rounded-[12px] border border-[#d8d4cb] bg-[#fcfbf7] p-6">
          <Loader2 className="size-5 shrink-0 animate-spin text-accent" />
          <div>
            <h2 className="font-semibold">Проверяем актуальность публикации</h2>
            <p className="mt-1 text-sm text-[#6d6962]">Сверяем последний деплой с текущей версией проекта.</p>
          </div>
        </section>
      )}

      {!inProgress && !complete && !checkingPublication && (
        <>
          {outdated && (
            <section className="mt-8 flex items-start gap-4 rounded-[12px] border border-[#e8a127]/40 bg-[#fffaf0] p-6">
              <CircleAlert className="mt-0.5 size-5 shrink-0 text-[#c47b12]" />
              <div>
                <h2 className="font-semibold">Текущая версия не опубликована</h2>
                <p className="mt-2 max-w-[760px] text-sm leading-6 text-[#6d6962]">
                  В истории остался предыдущий успешный деплой, но после изменений текущая версия проекта отличается от него. Опубликуйте её снова — постоянный HTTPS-адрес останется тем же.
                </p>
              </div>
            </section>
          )}
          <section className="mt-8 grid gap-4 lg:grid-cols-2">
            <article className="rounded-[12px] border-2 border-accent bg-[#fcfbf7] p-6 sm:p-8">
              <div className="flex items-start justify-between">
                <span className="grid size-11 place-items-center rounded-[8px] bg-accent text-white"><Cloud className="size-5" /></span>
                <span className="rounded-full bg-accent/10 px-3 py-1 text-[10px] font-semibold text-accent">Рекомендуется</span>
              </div>
              <h2 className="mt-8 text-2xl font-semibold">Хостинг Omnia</h2>
              <p className="mt-3 text-sm leading-6 text-[#6d6962]">Адрес не меняется: новый зелёный образ атомарно подменяет предыдущую production-версию.</p>
              <div className="mt-6 space-y-3 text-xs text-[#6d6962]">
                {["Не нужно настраивать сервер", "Контейнер можно оставить активным всегда", "Версии и откат доступны из кабинета"].map((item) => <p key={item} className="flex items-center gap-2"><Check className="size-3.5 text-[#248a4b]" />{item}</p>)}
              </div>
              <div className="mt-7"><MaxLaunchButton projectId={projectId} /></div>
            </article>
            <article className="rounded-[12px] border border-[#d8d4cb] bg-[#fcfbf7] p-6 sm:p-8">
              <span className="grid size-11 place-items-center rounded-[8px] bg-[#ece8df] text-accent"><Server className="size-5" /></span>
              <h2 className="mt-8 text-2xl font-semibold">Собственная VPS</h2>
              <p className="mt-3 text-sm leading-6 text-[#6d6962]">Укажите IP, SSH-доступ и домен. Omnia проверит сервер, Docker, DNS и развернёт проект end-to-end.</p>
              <div className="mt-6 space-y-3 text-xs text-[#6d6962]">
                {["Пароль или SSH-ключ", "Подтверждение fingerprint хоста", "Автоматический HTTPS для домена"].map((item) => <p key={item} className="flex items-center gap-2"><Check className="size-3.5 text-[#248a4b]" />{item}</p>)}
              </div>
              <Button asChild variant="outline" className="mt-7 w-full border-[#d8d4cb]">
                <Link href={`/max/${projectId}/settings?tab=vps`}>Настроить свою VPS</Link>
              </Button>
            </article>
          </section>

          <section className="mt-5 rounded-[12px] border border-[#d8d4cb] bg-[#fcfbf7] p-6">
            <div className="flex flex-col gap-5 lg:flex-row lg:items-center lg:justify-between">
              <div className="min-w-0">
                <p className="text-sm font-semibold">Рабочий контейнер</p>
                <p className="mt-1 text-xs text-[#6d6962]">Текущий статус: {runtime.data?.state ?? "проверяем"}. Режим «Всегда активен» управляется здесь.</p>
              </div>
              <div className="w-full lg:min-w-[260px] lg:w-auto"><RuntimeButton projectId={projectId} display="compact" /></div>
            </div>
          </section>
        </>
      )}

      {inProgress && (
        <section className="mt-8 grid gap-5 lg:grid-cols-[1fr_330px]">
          <div className="rounded-[12px] border border-[#d8d4cb] bg-[#fcfbf7] p-6 sm:p-8">
            <div className="flex items-start gap-4">
              <span className="grid size-11 place-items-center rounded-[8px] bg-accent text-white"><Loader2 className="size-5 animate-spin" /></span>
              <div><p className="omnia-kicker text-accent">Deployment in progress</p><h2 className="mt-1 text-2xl font-semibold">Публикация продолжается</h2><p className="mt-2 text-sm text-[#6d6962]">Можно закрыть страницу: процесс хранится и выполняется на сервере.</p></div>
            </div>
            <div className="mt-8 space-y-1">
              {phaseSteps.map(([id, label], index) => {
                const done = index < currentIndex;
                const active = id === phase;
                return (
                  <div key={id} className={`flex items-center gap-4 rounded-[10px] px-4 py-4 ${active ? "bg-accent/[.07]" : ""}`}>
                    <span className={`grid size-7 place-items-center rounded-full border ${done ? "border-[#248a4b] bg-[#248a4b] text-white" : active ? "border-accent text-accent" : "border-[#d8d4cb] text-[#aaa59b]"}`}>
                      {done ? <Check className="size-4" /> : active ? <Loader2 className="size-4 animate-spin" /> : index + 1}
                    </span>
                    <span className={`text-sm ${active ? "font-semibold" : "text-[#6d6962]"}`}>{label}</span>
                  </div>
                );
              })}
            </div>
          </div>
          <aside data-graphite-shell className="rounded-[12px] border border-[#d8d4cb] bg-[#171716] p-6 text-white">
            <p className="omnia-kicker text-white/35">Серверный статус</p>
            <p className="mt-4 font-mono text-xs text-accent">{deploy.data?.detail ?? deploy.data?.phase}</p>
            <div className="mt-5 max-h-[280px] space-y-2 overflow-y-auto font-mono text-[10px] leading-5 text-white/40">
              {(deploy.data?.logs ?? []).slice(-12).map((line, index) => <p key={`${line}-${index}`}>{line}</p>)}
            </div>
          </aside>
        </section>
      )}

      {complete && (
        <section className="mt-8">
          <div className="rounded-[12px] border border-[#248a4b]/30 bg-[#fcfbf7] p-6 sm:p-8">
            <span className="grid size-12 place-items-center rounded-full bg-[#248a4b] text-white"><Check className="size-6" /></span>
            <p className="omnia-kicker mt-6 text-[#248a4b]">Deployment complete</p>
            <h2 className="mt-2 text-3xl font-semibold tracking-[-.035em]">Версия опубликована</h2>
            <p className="mt-3 max-w-[650px] text-sm leading-6 text-[#6d6962]">Production-контейнер запущен, health-check пройден. Тот же URL теперь открывает новую версию; повторно менять ссылку в MAX Partner не нужно.</p>
            {deploy.data?.prod_url && (
              <a href={deploy.data.prod_url} target="_blank" rel="noreferrer" className="mt-6 flex max-w-[620px] items-center justify-between rounded-[10px] border border-[#d8d4cb] bg-white px-4 py-3 font-mono text-xs">
                <span className="truncate">{deploy.data.prod_url}</span><ExternalLink className="size-4 shrink-0 text-accent" />
              </a>
            )}
            <div className="mt-7 flex flex-wrap gap-3">
              <Button asChild className="bg-accent text-white hover:bg-accent-hover"><Link href={`/max/${projectId}/dashboard`}>Открыть управление <Rocket className="size-4" /></Link></Button>
              <Button asChild variant="outline"><Link href={`/max/${projectId}/settings`}>Настроить MAX-бота</Link></Button>
            </div>
          </div>
        </section>
      )}

      {failed && (
        <section className="mt-8 rounded-[12px] border border-[#c63d35]/30 bg-[#fcfbf7] p-6">
          <div className="flex items-start gap-4"><CircleAlert className="mt-0.5 size-5 text-[#c63d35]" /><div><h2 className="font-semibold">Публикация не завершилась</h2><p className="mt-2 text-sm leading-6 text-[#6d6962]">{deploy.data?.error ?? "Повторите попытку после проверки сервера."}</p></div></div>
          <Button onClick={() => deploy.refetch()} variant="outline" className="mt-5"><RefreshCw className="size-4" />Обновить статус</Button>
        </section>
      )}

      <section className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {journey.stages.map((stage) => (
          <div key={stage.id} className="rounded-[10px] border border-[#d8d4cb] bg-[#fcfbf7] p-4">
            <span className={`grid size-6 place-items-center rounded-full border ${stage.done ? "border-[#248a4b] text-[#248a4b]" : "border-[#d8d4cb] text-[#aaa59b]"}`}>{stage.done ? <Check className="size-3.5" /> : <ShieldCheck className="size-3" />}</span>
            <p className="mt-3 text-xs font-medium">{stage.shortLabel}</p>
          </div>
        ))}
      </section>
    </MaxSectionShell>
  );
}
