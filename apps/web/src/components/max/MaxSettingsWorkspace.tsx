"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
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

import { MaxSectionShell } from "@/components/max/MaxSectionShell";
import { MaxProjectSetupDialog } from "@/components/max/MaxProjectSetupDialog";
import { ExternalDeployWizard } from "@/components/workspace/ExternalDeployWizard";
import { MaxIntegrationButton } from "@/components/workspace/MaxIntegrationButton";
import { getMaxIntegration } from "@/lib/api/max-integration";
import { getMaxProjectConfig } from "@/lib/api/max-studio";
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

  return (
    <MaxSectionShell
      projectId={projectId}
      projectName={projectName}
      active="settings"
      eyebrow="07 / Bot and MAX setup"
      title="MAX и приложение"
      lead="Здесь собраны три независимых шага: проверка бота, данные приложения и инфраструктура. Каждый статус основан на ответе сервера, а не на локальной галочке."
    >
      <div className="mt-8 flex gap-2 overflow-x-auto pb-1">
        {[
          ["bot", Bot, "MAX-бот"],
          ["app", FileCheck2, "Данные приложения"],
          ["vps", Server, "Своя VPS"],
        ].map(([id, Icon, label]) => (
          <button key={String(id)} onClick={() => setTab(id as Tab)} className={cn("inline-flex h-10 shrink-0 items-center gap-2 rounded-[8px] border px-4 text-sm", tab === id ? "border-[#171716] bg-[#171716] text-white" : "border-[#d8d4cb] bg-[#fcfbf7] text-[#6d6962]")}>
            <Icon className="size-4" />{String(label)}
          </button>
        ))}
      </div>

      {tab === "bot" && (
        <section className="mt-6 grid gap-5 lg:grid-cols-[1fr_320px]">
          <div className="rounded-[12px] border border-[#d8d4cb] bg-[#fcfbf7] p-6 sm:p-8">
            <div className="flex items-start justify-between gap-5">
              <div>
                <span className="grid size-11 place-items-center rounded-[8px] bg-[#ece8df] text-[#f15a38]"><Bot className="size-5" /></span>
                <h2 className="mt-6 text-2xl font-semibold">Подключение MAX-бота</h2>
                <p className="mt-3 max-w-[580px] text-sm leading-6 text-[#6d6962]">Бот создаётся и проходит модерацию в платформе MAX для партнёров. Токен нужен для проверки API, webhook и сервисных сообщений.</p>
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
            <p className="omnia-kicker text-[#8d887f]">Что проверить</p>
            <ol className="mt-5 space-y-4 text-sm">
              {["Бот создан владельцем бизнеса", "Бот прошёл модерацию MAX", "Токен скопирован без пробелов", "API отвечает из production"].map((item, index) => (
                <li key={item} className="flex gap-3"><span className="grid size-6 shrink-0 place-items-center rounded-full border border-[#d8d4cb] font-mono text-[9px] text-[#8d887f]">{index + 1}</span><span className="pt-0.5 text-[#6d6962]">{item}</span></li>
              ))}
            </ol>
            <a href="https://business.max.ru/" target="_blank" rel="noreferrer" className="mt-6 inline-flex items-center gap-1.5 text-xs font-medium text-[#c84528]">Открыть кабинет MAX <ExternalLink className="size-3" /></a>
          </aside>
        </section>
      )}

      {tab === "app" && (
        <section className="mt-6 grid gap-5 lg:grid-cols-[1fr_320px]">
          <div className="rounded-[12px] border border-[#d8d4cb] bg-[#fcfbf7] p-6 sm:p-8">
            <span className="grid size-11 place-items-center rounded-[8px] bg-[#ece8df] text-[#f15a38]"><FileCheck2 className="size-5" /></span>
            <h2 className="mt-6 text-2xl font-semibold">Данные готового приложения</h2>
            <p className="mt-3 max-w-[620px] text-sm leading-6 text-[#6d6962]">Название, сценарий, оператор, поддержка, возрастной рейтинг и обязательные юридические страницы. Эти изменения версионируются без расходов на модель.</p>
            <div className="mt-7 max-w-[260px]"><MaxProjectSetupDialog projectId={projectId} display="panel" emphasized={!config.data?.config.legal.terms_accepted} label="Открыть данные приложения" /></div>
          </div>
          <aside className="rounded-[12px] border border-[#d8d4cb] bg-[#fcfbf7] p-6">
            <p className="omnia-kicker text-[#8d887f]">Готовность</p>
            <div className="mt-5 space-y-4 text-sm">
              {[
                ["Название и сценарий", Boolean(config.data?.config.app_name && config.data?.config.summary)],
                ["Данные оператора", Boolean(config.data?.config.operator.legal_name && config.data?.config.operator.inn)],
                ["Контакты поддержки", Boolean(config.data?.config.support.email || config.data?.config.support.phone)],
                ["Условия приняты", Boolean(config.data?.config.legal.terms_accepted)],
                ["URL добавлен в MAX", Boolean(config.data?.config.max_url_attached)],
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
              <p className="flex gap-3"><KeyRound className="mt-0.5 size-4 shrink-0 text-[#f15a38]" />Пароль или SSH-ключ шифруется и используется только серверным provisioner.</p>
              <p className="flex gap-3"><ShieldCheck className="mt-0.5 size-4 shrink-0 text-[#f15a38]" />Перед установкой показываем fingerprint хоста и просим подтвердить его.</p>
              <p className="flex gap-3"><CircleAlert className="mt-0.5 size-4 shrink-0 text-[#f15a38]" />Сначала проверяем Docker, порт, DNS и права. Деплой не начнётся на неподходящем сервере.</p>
            </div>
          </aside>
        </section>
      )}
    </MaxSectionShell>
  );
}
