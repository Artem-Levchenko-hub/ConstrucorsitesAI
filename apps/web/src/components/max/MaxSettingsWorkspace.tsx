"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bot,
  CircleAlert,
  ExternalLink,
  FileCheck2,
  KeyRound,
  Server,
  ShieldCheck,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

import { MaxSectionShell } from "@/components/max/MaxSectionShell";
import Link from "next/link";
import { ExternalDeployWizard } from "@/components/workspace/ExternalDeployWizard";
import { MaxIntegrationButton } from "@/components/workspace/MaxIntegrationButton";
import { Button } from "@/components/ui/button";
import { getMaxIntegration } from "@/lib/api/max-integration";
import { getLastDeploy } from "@/lib/api/runtime";
import {
  getMaxProjectConfig,
  saveMaxUrlAttached,
} from "@/lib/api/max-studio";
import { copyMaxLaunchUrl } from "@/lib/max-launch-steps";
import { cn } from "@/lib/utils";

type Tab = "bot" | "vps";

export function MaxSettingsWorkspace({
  projectId,
  projectName,
  initialTab = "bot",
}: {
  projectId: string;
  projectName: string;
  initialTab?: Tab;
}) {
  const [tab, setTab] = useState<Tab>(initialTab);
  const router = useRouter();
  const queryClient = useQueryClient();
  const integration = useQuery({
    queryKey: ["max-integration", projectId],
    queryFn: () => getMaxIntegration(projectId),
    retry: false,
  });
  const deploy = useQuery({
    queryKey: ["deploy", projectId],
    queryFn: () => getLastDeploy(projectId),
    retry: false,
    refetchInterval: (query) =>
      ["building", "pushing", "swapping", "cancelling"].includes(
        query.state.data?.phase ?? "",
      )
        ? 1_500
        : false,
  });
  const config = useQuery({
    queryKey: ["max-config", projectId],
    queryFn: () => getMaxProjectConfig(projectId),
    retry: false,
  });
  const productionUrl = deploy.data?.prod_url ?? integration.data?.app_url ?? null;
  const confirmMaxUrl = useMutation({
    mutationFn: async () => {
      const appUrl = productionUrl;
      if (!appUrl) throw new Error("Сначала опубликуйте приложение и получите постоянный URL.");

      await fetch(appUrl, {
        method: "GET",
        mode: "no-cors",
        cache: "no-store",
      });
      return saveMaxUrlAttached(projectId, true);
    },
    onSuccess: (data) => {
      queryClient.setQueryData(["max-config", projectId], data);
      void queryClient.invalidateQueries({ queryKey: ["max-readiness", projectId] });
      toast.success("Адрес отвечает и подтверждён", {
        description: "Omnia проверила сетевой ответ. Вставку в MAX Partner подтвердили вы.",
      });
    },
    onError: (error) => {
      toast.error("Не удалось подтвердить URL", {
        description: error instanceof Error ? error.message : "Проверьте публикацию и повторите попытку.",
      });
    },
  });

  function openMaxCabinet(event: React.MouseEvent<HTMLAnchorElement>) {
    event.preventDefault();
    const appUrl = productionUrl;
    if (appUrl) {
      void copyMaxLaunchUrl(appUrl).then((copied) => {
        if (copied) {
          toast.success("Ссылка на приложение скопирована", {
            description: "Кабинет MAX открыт — вставьте ссылку в кнопку приложения.",
          });
        } else {
          toast.error("Не удалось скопировать ссылку", {
            description: "Скопируйте адрес приложения вручную после публикации.",
          });
        }
      });
    } else {
      toast.info("Ссылка появится после публикации", {
        description: "Кабинет MAX открыт, но приложение пока без постоянного URL.",
      });
    }
    window.open("https://business.max.ru/", "_blank", "noopener,noreferrer");
  }

  const pageCopy = {
    bot: {
      eyebrow: "Этап 4 из 6",
      title: "Подключение MAX",
      lead: "Свяжите приложение с ботом перед публикацией для безопасного входа пользователей.",
    },
    vps: {
      eyebrow: "Размещение",
      title: "Собственная VPS",
      lead: "Подключите свой сервер, если приложению не подходит управляемый хостинг Omnia.",
    },
  }[tab];

  function selectTab(next: Tab) {
    setTab(next);
    router.replace(`/max/${projectId}/settings?tab=${next}`, { scroll: false });
  }

  return (
    <MaxSectionShell
      projectId={projectId}
      projectName={projectName}
      active="bot"
      eyebrow={pageCopy.eyebrow}
      title={pageCopy.title}
      lead={pageCopy.lead}
    >
      <div className="max-settings-tabs mt-6 flex flex-wrap gap-2" aria-label="Разделы настроек">
        {[
          ["bot", Bot, "MAX"],
          ["vps", Server, "Своя VPS"],
        ].map(([id, Icon, label]) => (
          <button aria-pressed={tab === id} key={String(id)} onClick={() => selectTab(id as Tab)} className={cn("inline-flex h-11 shrink-0 items-center gap-2 rounded-[8px] border px-4 text-sm sm:h-10", tab === id ? "border-accent bg-accent-subtle text-accent-secondary" : "border-border-default bg-surface text-fg-secondary")}>
            <Icon className="size-4" />{String(label)}
          </button>
        ))}
        <Button asChild variant="outline"><Link href={`/max/${projectId}?data=details`}><FileCheck2 className="size-4" />Данные приложения</Link></Button>
      </div>

      {tab === "bot" && (
        <>
          <section className="mt-6 grid gap-5 lg:grid-cols-[1fr_320px]">
            <div className="rounded-[12px] border border-border-default bg-surface p-5 sm:p-6">
              <div className="flex items-start justify-between gap-5">
                <div>
                  <span className="grid size-11 place-items-center rounded-[8px] bg-surface-3 text-accent">
                    <Bot className="size-5" />
                  </span>
                  <h2 className="mt-4 text-xl font-semibold">
                    Безопасный вход пользователей
                  </h2>
                  <p className="mt-3 max-w-[620px] text-sm leading-6 text-fg-secondary">
                    Подключите секрет бота перед публикацией. Он нужен для
                    проверки входа и разделения данных пользователей.
                  </p>
                </div>
                <span
                  className={`shrink-0 rounded-full px-3 py-1 text-xs font-medium ${
                    integration.isSuccess && integration.data?.connected
                      ? "bg-[#248a4b]/10 text-success-fg"
                      : "bg-[#e8c547]/15 text-warning"
                  }`}
                >
                  {integration.isError ? "Статус недоступен" : integration.isPending ? "Проверяем…" : integration.data?.connected
                    ? "Безопасный вход подключён"
                    : "Нужен перед публикацией"}
                </span>
              </div>

              <div className="mt-7 grid gap-3 sm:grid-cols-3">
                {[
                  "Создайте и промодерируйте бота в MAX Partner",
                  "Скопируйте секрет и подключите его в защищённой форме Omnia",
                  "После публикации вставьте адрес в настройки приложения MAX",
                ].map((item, index) => (
                  <div
                    key={item}
                    className="rounded-[10px] border border-border-default bg-surface-base px-4 py-4"
                  >
                    <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-fg-tertiary">
                      Шаг {index + 1}
                    </p>
                    <p className="mt-2 text-sm leading-6 text-fg-primary">{item}</p>
                  </div>
                ))}
              </div>

              {productionUrl && (
                <div className="mt-6 rounded-[10px] border border-border-default bg-surface-base px-4 py-3">
                  <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-fg-tertiary">
                    Постоянный адрес
                  </p>
                  <p className="mt-2 truncate font-mono text-xs text-fg-secondary">
                    {productionUrl}
                  </p>
                </div>
              )}

              <div className="mt-6 flex flex-wrap gap-3">
                <MaxIntegrationButton
                  maxStudio
                  projectId={projectId}
                  initialTemplate="max_miniapp"
                  display="panel"
                  emphasized={!integration.data?.connected}
                  label={
                    integration.data?.connected
                      ? "Проверить безопасный вход"
                      : "Подключить безопасный вход"
                  }
                />
                <Button asChild variant="outline" className="h-11 border-border-default">
                  <a
                    href="https://business.max.ru/"
                    target="_blank"
                    rel="noreferrer"
                    onClick={openMaxCabinet}
                  >
                    Скопировать URL и открыть MAX
                    <ExternalLink className="size-3.5" />
                  </a>
                </Button>
              </div>
              <p className="mt-4 flex items-center gap-2 text-xs text-fg-tertiary">
                <ShieldCheck className="size-4 text-success-fg" />
                Секрет хранится зашифрованно, не попадает в код и не показывается повторно.
              </p>
            </div>

            <aside className="rounded-[12px] border border-border-default bg-surface p-6">
              <p className="omnia-kicker text-fg-tertiary">Что даёт подключение</p>
              <ol className="mt-5 space-y-4 text-sm">
                {[
                  "Проверка подписи initData на сервере",
                  "Автоматические сообщения от имени бота",
                  "Webhook и серверные события MAX",
                  "Разделение профилей и истории по реальным MAX-пользователям",
                ].map((item, index) => (
                  <li key={item} className="flex gap-3">
                    <span className="grid size-6 shrink-0 place-items-center rounded-full border border-border-default font-mono text-[9px] text-fg-tertiary">
                      {index + 1}
                    </span>
                    <span className="pt-0.5 text-fg-secondary">{item}</span>
                  </li>
                ))}
              </ol>
              <p className="mt-4 text-[10px] leading-4 text-fg-tertiary">
                ИНН и ОГРН в Omnia для этого не нужны: бизнес проверяется на стороне MAX.
              </p>
            </aside>
          </section>

          <section className="mt-5 rounded-[12px] border border-border-default bg-surface p-5 sm:p-6">
            <div className="flex flex-col gap-6 lg:flex-row lg:items-center lg:justify-between">
              <div className="max-w-[680px]">
                <div className="flex items-center gap-2">
                  <span
                    className={`size-2 rounded-full ${
                    config.isSuccess && config.data?.config.max_url_attached
                        ? "bg-[#248a4b]"
                        : "bg-[#e8c547]"
                    }`}
                  />
                  <p className="text-xs font-semibold uppercase tracking-[0.12em] text-fg-tertiary">
                    URL приложения в MAX
                  </p>
                </div>
                <h2 className="mt-3 text-xl font-semibold">
                  {config.isError ? "Не удалось проверить подтверждение" : config.isPending ? "Проверяем подтверждение…" : config.data?.config.max_url_attached
                    ? "Подтверждено пользователем"
                    : "Добавьте HTTPS-адрес в MAX Partner"}
                </h2>
                <p className="mt-2 text-sm leading-6 text-fg-secondary">
                  Omnia скопирует постоянный адрес и откроет кабинет MAX. После
                  вставки вернитесь сюда: мы проверим доступность приложения и
                  сохраним ваше подтверждение. MAX пока не сообщает эту
                  настройку через публичный API.
                </p>
                {productionUrl && (
                  <p className="mt-3 truncate font-mono text-[11px] text-fg-tertiary">
                    {productionUrl}
                  </p>
                )}
              </div>
              <div className="flex w-full shrink-0 flex-col gap-2 lg:w-[260px]">
                <Button asChild variant="outline" className="h-11 border-border-default">
                  <a
                    href="https://business.max.ru/"
                    target="_blank"
                    rel="noreferrer"
                    onClick={openMaxCabinet}
                  >
                    Скопировать и открыть MAX
                    <ExternalLink className="size-3.5" />
                  </a>
                </Button>
                <Button
                  type="button"
                  className="h-11 bg-accent text-fg-on-accent hover:bg-accent-hover"
                  disabled={!productionUrl || confirmMaxUrl.isPending}
                  onClick={() => confirmMaxUrl.mutate()}
                >
                  {confirmMaxUrl.isPending
                    ? "Проверяем адрес…"
                    : config.data?.config.max_url_attached
                      ? "Проверить ещё раз"
                      : "Я вставил URL — проверить"}
                </Button>
              </div>
            </div>
          </section>
        </>
      )}

      {tab === "vps" && (
        <section className="mt-6 grid gap-5 lg:grid-cols-[1fr_300px]">
          <div className="[&>section]:rounded-[12px] [&>section]:border-border-default [&>section]:bg-surface [&>section]:p-6">
            <ExternalDeployWizard projectId={projectId} maxStudio />
          </div>
          <aside className="rounded-[12px] border border-border-default bg-surface p-6">
            <p className="omnia-kicker text-fg-tertiary">Безопасность</p>
            <div className="mt-5 space-y-5 text-xs leading-5 text-fg-secondary">
              <p className="flex gap-3"><KeyRound className="mt-0.5 size-4 shrink-0 text-accent" />Пароль или SSH-ключ шифруется и используется только серверным provisioner.</p>
              <p className="flex gap-3"><ShieldCheck className="mt-0.5 size-4 shrink-0 text-accent" />Перед установкой показываем fingerprint хоста и просим подтвердить его.</p>
              <p className="flex gap-3"><CircleAlert className="mt-0.5 size-4 shrink-0 text-accent" />Сначала проверяем Docker, порт, DNS и права. Деплой не начнётся на неподходящем сервере.</p>
            </div>
          </aside>
        </section>
      )}
    </MaxSectionShell>
  );
}
