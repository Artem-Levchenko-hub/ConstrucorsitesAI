"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bot,
  Check,
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
import { MaxProjectSetupDialog } from "@/components/max/MaxProjectSetupDialog";
import { ExternalDeployWizard } from "@/components/workspace/ExternalDeployWizard";
import { MaxIntegrationButton } from "@/components/workspace/MaxIntegrationButton";
import { Button } from "@/components/ui/button";
import { getMaxIntegration } from "@/lib/api/max-integration";
import {
  getMaxProjectConfig,
  saveMaxUrlAttached,
} from "@/lib/api/max-studio";
import { copyMaxLaunchUrl } from "@/lib/max-launch-steps";
import { cn } from "@/lib/utils";

type Tab = "bot" | "app" | "vps";

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
  const config = useQuery({
    queryKey: ["max-config", projectId],
    queryFn: () => getMaxProjectConfig(projectId),
    retry: false,
  });
  const confirmMaxUrl = useMutation({
    mutationFn: async () => {
      const appUrl = integration.data?.app_url;
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
    const appUrl = integration.data?.app_url;
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
      title: "MAX-бот",
      lead: "Подключите прошедшего модерацию бота, проверьте доступ к API и подготовьте webhook для production.",
    },
    app: {
      eyebrow: "Этап 3 из 6",
      title: "Данные приложения",
      lead: "Заполните продукт, контент, владельца, поддержку и политики. Статусы основаны на серверной проверке.",
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
      active={tab === "app" ? "app" : "bot"}
      eyebrow={pageCopy.eyebrow}
      title={pageCopy.title}
      lead={pageCopy.lead}
    >
      <div className="mt-8 flex flex-wrap gap-2">
        {[
          ["bot", Bot, "MAX-бот"],
          ["app", FileCheck2, "Данные приложения"],
          ["vps", Server, "Своя VPS"],
        ].map(([id, Icon, label]) => (
          <button key={String(id)} onClick={() => selectTab(id as Tab)} className={cn("inline-flex h-11 shrink-0 items-center gap-2 rounded-[8px] border px-4 text-sm sm:h-10", tab === id ? "border-[#171716] bg-[#171716] text-white" : "border-[#d8d4cb] bg-[#fcfbf7] text-[#6d6962]")}>
            <Icon className="size-4" />{String(label)}
          </button>
        ))}
      </div>

      {tab === "bot" && (
        <>
          <section className="mt-6 grid gap-5 lg:grid-cols-[1fr_320px]">
            <div className="rounded-[12px] border border-[#d8d4cb] bg-[#fcfbf7] p-6 sm:p-8">
            <div className="flex items-start justify-between gap-5">
              <div>
                <span className="grid size-11 place-items-center rounded-[8px] bg-[#ece8df] text-accent"><Bot className="size-5" /></span>
                <h2 className="mt-6 text-2xl font-semibold">Подключение MAX-бота</h2>
                <p className="mt-3 max-w-[580px] text-sm leading-6 text-[#6d6962]">
                  В само мини-приложение токен не попадает. Он нужен только
                  backend Omnia для Bot API, webhook и сообщений от имени бота. Если
                  это не нужно, можно просто вставить production URL в MAX Partner вручную.
                </p>
              </div>
              <span className={`shrink-0 rounded-full px-3 py-1 text-xs font-medium ${integration.data?.connected ? "bg-[#248a4b]/10 text-[#248a4b]" : "bg-[#e8c547]/15 text-[#745f16]"}`}>
                {integration.data?.connected ? "Подключён" : "Не подключён"}
              </span>
            </div>
            <div className="mt-7 max-w-[260px]">
              <MaxIntegrationButton projectId={projectId} initialTemplate="max_miniapp" display="panel" emphasized={!integration.data?.connected} label={integration.data?.connected ? "Открыть настройки" : "Проверить и подключить"} />
            </div>
            <p className="mt-5 flex items-center gap-2 text-xs text-[#8d887f]"><ShieldCheck className="size-4 text-[#248a4b]" />Секрет хранится зашифрованно и не отображается повторно.</p>
            </div>
            <aside className="rounded-[12px] border border-[#d8d4cb] bg-[#fcfbf7] p-6">
            <p className="omnia-kicker text-[#8d887f]">Что сделать в MAX</p>
            <ol className="mt-5 space-y-4 text-sm">
              {["Войдите в MAX Partner как подтверждённый здесь владелец", "Создайте бота, заполните карточку и отправьте её на модерацию", "После одобрения откройте Bot API и скопируйте секрет", "Вернитесь сюда и нажмите «Проверить и подключить»"].map((item, index) => (
                <li key={item} className="flex gap-3"><span className="grid size-6 shrink-0 place-items-center rounded-full border border-[#d8d4cb] font-mono text-[9px] text-[#8d887f]">{index + 1}</span><span className="pt-0.5 text-[#6d6962]">{item}</span></li>
              ))}
            </ol>
            <a
              href="https://business.max.ru/"
              target="_blank"
              rel="noreferrer"
              onClick={openMaxCabinet}
              className="mt-6 inline-flex min-h-11 items-center gap-1.5 text-xs font-medium text-accent"
            >
              Открыть кабинет MAX
              <ExternalLink className="size-3" />
            </a>
            <p className="mt-1 text-[10px] leading-4 text-[#8d887f]">
              Если приложение опубликовано, его ссылка скопируется автоматически.
            </p>
            </aside>
          </section>

          <section className="mt-5 rounded-[12px] border border-[#d8d4cb] bg-[#fcfbf7] p-6 sm:p-8">
            <div className="flex flex-col gap-6 lg:flex-row lg:items-center lg:justify-between">
              <div className="max-w-[680px]">
                <div className="flex items-center gap-2">
                  <span
                    className={`size-2 rounded-full ${config.data?.config.max_url_attached ? "bg-[#248a4b]" : "bg-[#e8c547]"}`}
                  />
                  <p className="text-xs font-semibold uppercase tracking-[0.12em] text-[#8d887f]">
                    URL приложения в MAX
                  </p>
                </div>
                <h2 className="mt-3 text-xl font-semibold">
                  {config.data?.config.max_url_attached
                    ? "Подтверждено пользователем"
                    : "Добавьте HTTPS-адрес в MAX Partner"}
                </h2>
                <p className="mt-2 text-sm leading-6 text-[#6d6962]">
                  Omnia скопирует постоянный адрес и откроет кабинет MAX. После вставки
                  вернитесь сюда: мы проверим доступность приложения и сохраним ваше
                  подтверждение. MAX пока не сообщает эту настройку через публичный API.
                </p>
                <ol className="mt-4 grid gap-2 text-xs leading-5 text-[#6d6962] sm:grid-cols-3">
                  {[
                    "1. Откройте Чат-боты и выберите своего бота",
                    "2. Расширенные настройки → Мини-приложение → вставьте URL",
                    "3. Сохраните в MAX и нажмите здесь «Я вставил URL»",
                  ].map((item) => (
                    <li key={item} className="rounded-[8px] bg-[#f5f3ee] px-3 py-2">
                      {item}
                    </li>
                  ))}
                </ol>
                {integration.data?.app_url && (
                  <p className="mt-3 truncate font-mono text-[11px] text-[#8d887f]">
                    {integration.data.app_url}
                  </p>
                )}
              </div>
              <div className="flex w-full shrink-0 flex-col gap-2 lg:w-[260px]">
                <Button asChild variant="outline" className="h-11 border-[#d8d4cb]">
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
                  className="h-11 bg-accent text-white hover:bg-accent-hover"
                  disabled={!integration.data?.app_url || confirmMaxUrl.isPending}
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

      {tab === "app" && (
        <section className="mt-6 grid gap-5 lg:grid-cols-[1fr_320px]">
          <div className="rounded-[12px] border border-[#d8d4cb] bg-[#fcfbf7] p-6 sm:p-8">
            <span className="grid size-11 place-items-center rounded-[8px] bg-[#ece8df] text-accent"><FileCheck2 className="size-5" /></span>
            <h2 className="mt-6 text-2xl font-semibold">Данные готового приложения</h2>
            <p className="mt-3 max-w-[620px] text-sm leading-6 text-[#6d6962]">Название, сценарий, функции, стиль, управляемый контент, оператор, поддержка, возрастной рейтинг и обязательные юридические страницы. Эти изменения версионируются без расходов на модель.</p>
            <div className="mt-7 max-w-[260px]"><MaxProjectSetupDialog projectId={projectId} display="panel" emphasized={!config.data?.config.legal.terms_accepted} label="Открыть данные приложения" /></div>
          </div>
          <aside className="rounded-[12px] border border-[#d8d4cb] bg-[#fcfbf7] p-6">
            <p className="omnia-kicker text-[#8d887f]">Готовность</p>
            <div className="mt-5 space-y-4 text-sm">
              {[
                ["Название и сценарий", Boolean(config.data?.config.app_name && config.data?.config.summary)],
                ["Функции и стиль", Boolean(config.data?.config.features.length || config.data?.config.brand_colors)],
                ["Управляемый контент", Boolean(config.data?.config.content.length)],
                ["Данные оператора", Boolean(config.data?.config.operator.legal_name && config.data?.config.operator.inn)],
                ["Контакты поддержки", Boolean(config.data?.config.support.email || config.data?.config.support.phone)],
                ["Условия приняты", Boolean(config.data?.config.legal.terms_accepted)],
              ].map(([label, done]) => (
                <p key={String(label)} className="flex items-center gap-3">
                  <span className={`grid size-5 place-items-center rounded-full border ${done ? "border-[#248a4b] bg-[#248a4b]/5 text-[#248a4b]" : "border-[#d8d4cb] text-transparent"}`}><Check className="size-3" /></span>
                  <span className={done ? "text-[#6d6962]" : "text-[#171716]"}>{String(label)}</span>
                </p>
              ))}
            </div>
          </aside>
        </section>
      )}

      {tab === "vps" && (
        <section className="mt-6 grid gap-5 lg:grid-cols-[1fr_300px]">
          <div className="[&>section]:rounded-[12px] [&>section]:border-[#d8d4cb] [&>section]:bg-[#fcfbf7] [&>section]:p-6">
            <ExternalDeployWizard projectId={projectId} />
          </div>
          <aside className="rounded-[12px] border border-[#d8d4cb] bg-[#fcfbf7] p-6">
            <p className="omnia-kicker text-[#8d887f]">Безопасность</p>
            <div className="mt-5 space-y-5 text-xs leading-5 text-[#6d6962]">
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
