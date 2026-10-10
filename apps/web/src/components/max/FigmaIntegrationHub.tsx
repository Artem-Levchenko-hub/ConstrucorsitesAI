"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { LucideIcon } from "lucide-react";
import {
  ArrowLeft,
  BarChart3,
  CalendarDays,
  Check,
  ChevronRight,
  CircleDollarSign,
  CloudCog,
  ExternalLink,
  Loader2,
  PackageSearch,
  Plug,
  RefreshCw,
  Search,
  ShieldCheck,
  Sparkles,
  Store,
  Trash2,
  Truck,
  UsersRound,
} from "lucide-react";
import { toast } from "sonner";
import { useRouter } from "next/navigation";

import { MaxSectionShell } from "@/components/max/MaxSectionShell";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  applyIntegrationPack,
  bindAppIntegration,
  claimMoyskladIntegration,
  connectAppIntegration,
  deleteAccountIntegration,
  disconnectAppIntegration,
  getAmocrmOptions,
  getIntegrationCatalog,
  getMoyskladInstall,
  getMoyskladOptions,
  saveAmocrmSettings,
  saveMoyskladSettings,
  startMoyskladInstall,
  startIntegrationOAuth,
  setPlatformAiEnabled,
  verifyAppIntegration,
} from "@/lib/api/app-integrations";
import { useOwnerChatQuota } from "@/hooks/useOwnerChatQuota";
import { FREE_CHAT_UPGRADE_MESSAGE, isFreeChatRefusal, refreshOwnerAfterAcceptedPrompt } from "@/lib/owner-chat-quota";
import { OwnerChatQuotaNotice } from "@/components/workspace/OwnerChatQuotaNotice";
import { describeApiError } from "@/lib/api/errors";
import { syncMaxManagedKit } from "@/lib/api/max-studio";
import { sendPrompt } from "@/lib/api/messages";
import type { AppIntegration, IntegrationCategory, IntegrationProvider } from "@/lib/api/types";
import { containsChatSecret } from "@/lib/max-chat-credentials";
import { cn } from "@/lib/utils";
import { getBuiltinIntegrationRequest, recognizeBuiltinIntegrationRequest } from "@/lib/builtin-integration-prompts";
import { IntegrationRequestCard } from "@/components/workspace/IntegrationRequestCard";

const categories: Record<IntegrationCategory | "all", { label: string; icon: LucideIcon }> = {
  all: { label: "Все сервисы", icon: Plug },
  ai: { label: "ИИ", icon: Sparkles },
  payments: { label: "Оплата", icon: CircleDollarSign },
  restaurant: { label: "Рестораны", icon: Store },
  crm: { label: "CRM", icon: UsersRound },
  inventory: { label: "Товары", icon: PackageSearch },
  analytics: { label: "Аналитика", icon: BarChart3 },
  booking: { label: "Запись", icon: CalendarDays },
  delivery: { label: "Доставка", icon: Truck },
};

const providerIcons: Record<string, LucideIcon> = {
  llmgw: Sparkles,
  yookassa: CircleDollarSign,
  iiko: Store,
  rkeeper: Store,
  bitrix24: UsersRound,
  amocrm: UsersRound,
  moysklad: PackageSearch,
  one_c: PackageSearch,
  yandex_metrica: BarChart3,
  yclients: CalendarDays,
  cdek: Truck,
};

const readyForImplementation = (
  provider: IntegrationProvider | undefined,
  connection: AppIntegration | undefined,
) => Boolean(provider && (provider.connection_mode === "platform"
  ? provider.available && provider.enabled === true
  : connection?.status === "active" && connection.bound_to_project && connection.binding_status === "ready"));

// Plan refusals (`entitlement_exceeded`, `subscription_entitlement_required`)
// are spelled out from their details; other API answers pass through.
const message = (error: unknown) => describeApiError(error, "Не удалось выполнить действие");

type ImplementationAttempt = { provider: string; prompt: string; key: string; terminal: boolean };

export function FigmaIntegrationHub({ projectId, projectName, embedded = false, onExit, onBusyChange }: {
  projectId: string;
  projectName: string;
  embedded?: boolean;
  onExit?: () => void;
  onBusyChange?: (busy: boolean) => void;
}) {
  const qc = useQueryClient();
  const router = useRouter();
  const quota = useOwnerChatQuota();
  const [implementationProvider, setImplementationProvider] = useState<string | null>(null);
  const [implementationPrompt, setImplementationPrompt] = useState("");
  const implementationSubmitting = useRef(false);
  const implementationAttempt = useRef<ImplementationAttempt | null>(null);
  const [replayCandidate, setReplayCandidate] = useState<ImplementationAttempt | null>(null);
  const [terminalFailure, setTerminalFailure] = useState(false);
  const [category, setCategory] = useState<IntegrationCategory | "all">("all");
  const [search, setSearch] = useState("");
  const [selectedChoice, setSelected] = useState<IntegrationProvider | null>(null);
  const [moyskladReturnProject, setMoyskladReturnProject] = useState<string | null>(() =>
    typeof window !== "undefined" && new URLSearchParams(window.location.search).get("integration") === "moysklad" ? projectId : null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [moyskladCode, setMoyskladCode] = useState("");
  const [moyskladOrganization, setMoyskladOrganization] = useState("");
  const [moyskladStore, setMoyskladStore] = useState("");
  const [moyskladReplaceConfirmed, setMoyskladReplaceConfirmed] = useState(false);
  const [moyskladDeleteOpen, setMoyskladDeleteOpen] = useState(false);
  const [moyskladDeleteConfirmed, setMoyskladDeleteConfirmed] = useState(false);
  const [moyskladInstallLink, setMoyskladInstallLink] = useState<{ projectId: string; url: string } | null>(null);
  const moyskladInstallSubmitting = useRef(false);
  const currentProject = useRef(projectId);
  useEffect(() => { currentProject.current = projectId; }, [projectId]);
  const [amocrmPipeline, setAmocrmPipeline] = useState("");
  const [amocrmStatus, setAmocrmStatus] = useState("");
  const queryKey = ["app-integrations", projectId];
  const catalog = useQuery({
    queryKey,
    queryFn: () => getIntegrationCatalog(projectId),
    retry: false,
  });
  const selected = selectedChoice ?? (moyskladReturnProject === projectId
    ? catalog.data?.providers.find((provider) => provider.key === "moysklad") ?? null : null);
  const closeProvider = () => { setSelected(null); setMoyskladReturnProject(null); setValues({}); };

  useEffect(() => {
    void syncMaxManagedKit(projectId).catch(() => undefined);
  }, [projectId]);

  const connections = useMemo(
    () => new Map((catalog.data?.connections ?? []).map((item) => [item.provider, item])),
    [catalog.data?.connections],
  );
  const moyskladConnection = connections.get("moysklad");
  const moyskladInstall = useQuery({
    queryKey: ["moysklad-install", projectId],
    queryFn: () => getMoyskladInstall(projectId),
    enabled: selected?.key === "moysklad",
    retry: false,
  });
  const moyskladOptions = useQuery({
    queryKey: ["moysklad-options", projectId],
    queryFn: () => getMoyskladOptions(projectId),
    enabled: selected?.key === "moysklad" && moyskladConnection?.status === "active",
    retry: false,
  });
  const amocrmConnection = connections.get("amocrm");
  const amocrmOptions = useQuery({
    queryKey: ["amocrm-options", projectId],
    queryFn: () => getAmocrmOptions(projectId),
    enabled: selected?.key === "amocrm" && amocrmConnection?.status === "active",
    retry: false,
  });
  const selectedAmocrmPipeline = amocrmOptions.data?.pipelines.find(
    (pipeline) => String(pipeline.id) === amocrmPipeline,
  );
  const selectedAmocrmStatus = selectedAmocrmPipeline?.statuses.find(
    (status) => String(status.id) === amocrmStatus,
  );
  const visible = useMemo(() => {
    const needle = search.trim().toLocaleLowerCase("ru-RU");
    return (catalog.data?.providers ?? []).filter(
      (provider) =>
        (category === "all" || provider.category === category) &&
        (!needle ||
          provider.name.toLocaleLowerCase("ru-RU").includes(needle) ||
          provider.description.toLocaleLowerCase("ru-RU").includes(needle)),
    );
  }, [catalog.data?.providers, category, search]);

  const sync = async () => {
    try {
      await syncMaxManagedKit(projectId);
    } catch (error) {
      toast.warning("Подключение сохранено, доработка приложения ещё не применена", { description: message(error) });
    }
  };
  const connect = useMutation({
    mutationFn: ({ provider, payload }: { provider: string; payload: Record<string, string> }) =>
      connectAppIntegration(projectId, provider, payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["app-integrations"] });
      void qc.invalidateQueries({ queryKey: ["moysklad-options"] });
      void sync();
      closeProvider();
      setValues({});
      toast.success("Доступ к сервису подтверждён");
    },
    onError: (error) => toast.error("Проверка не пройдена", { description: message(error) }),
  });
  const claimMoysklad = useMutation({
    mutationFn: (code: string) => claimMoyskladIntegration(projectId, code),
    onSuccess: ({ status }) => {
      void qc.invalidateQueries({ queryKey });
      void sync();
      if (status === "connected") {
        closeProvider();
        setMoyskladCode("");
        toast.success("МойСклад подключён к проекту");
      } else {
        toast.warning("Склад подключён в Yleum, подтверждение в МойСклад задержалось", {
          description: "Повторите этот код позднее. Если он истечёт, откройте решение в МойСклад снова.",
        });
      }
    },
    onError: (error) => toast.error("Не удалось подключить МойСклад", { description: message(error) }),
  });
  const installMoysklad = useMutation({
    mutationFn: (targetProject: string) => startMoyskladInstall(targetProject),
    onSuccess: ({ install_url }, targetProject) => {
      if (currentProject.current !== targetProject) return;
      setMoyskladInstallLink({ projectId: targetProject, url: install_url });
      window.open(install_url, "_blank", "noopener,noreferrer");
    },
  });
  const startMoyskladInstallation = () => {
    if (moyskladInstallSubmitting.current || !moyskladInstall.data?.available || !moyskladInstall.data.install_url) return;
    moyskladInstallSubmitting.current = true;
    void installMoysklad.mutateAsync(projectId).catch(() => undefined)
      .finally(() => { moyskladInstallSubmitting.current = false; });
  };
  const resetMoyskladInstall = installMoysklad.reset;
  const saveMoysklad = useMutation({
    mutationFn: () => saveMoyskladSettings(projectId, moyskladOrganization, moyskladStore),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey });
      toast.success("Организация и склад для заказов сохранены");
    },
    onError: (error) => toast.error("Не удалось сохранить настройки склада", { description: message(error) }),
  });
  const saveAmocrm = useMutation({
    mutationFn: () => saveAmocrmSettings(projectId, Number(amocrmPipeline), Number(amocrmStatus)),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey });
      void amocrmOptions.refetch();
      toast.success("Воронка и этап amoCRM сохранены");
    },
    onError: (error) => toast.error("Не удалось сохранить настройки amoCRM", { description: message(error) }),
  });
  const bind = useMutation({
    mutationFn: (provider: string) => bindAppIntegration(projectId, provider),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey });
      void sync();
      toast.success("Подключение включено для проекта");
    },
    onError: (error) => toast.error("Не удалось включить", { description: message(error) }),
  });
  const pack = useMutation({
    mutationFn: () => applyIntegrationPack(projectId),
    onSuccess: ({ remaining_provider_keys }) => {
      void qc.invalidateQueries({ queryKey });
      void sync();
      toast.success(remaining_provider_keys.length ? `Осталось авторизовать: ${remaining_provider_keys.length}` : "Набор интеграций готов");
    },
    onError: (error) => toast.error("Не удалось подготовить набор", { description: message(error) }),
  });
  const platformAi = useMutation({
    mutationFn: (enabled: boolean) => setPlatformAiEnabled(projectId, enabled),
    onSuccess: ({ enabled }) => {
      void qc.invalidateQueries({ queryKey });
      toast.success(enabled ? "ИИ включён для проекта" : "ИИ выключен для проекта");
    },
    onError: (error) => toast.error("Не удалось изменить доступ к ИИ", { description: message(error) }),
  });
  const oauth = useMutation({
    mutationFn: (provider: string) => startIntegrationOAuth(projectId, provider),
    onSuccess: ({ authorization_url }) => window.location.assign(authorization_url),
    onError: (error) => toast.error("Не удалось начать авторизацию", { description: message(error) }),
  });
  const verify = useMutation({
    mutationFn: (provider: string) => verifyAppIntegration(projectId, provider),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey });
      toast.success("Доступ к сервису подтверждён");
    },
    onError: (error) => toast.error("Интеграция не отвечает", { description: message(error) }),
  });
  const disconnect = useMutation({
    mutationFn: (provider: string) => disconnectAppIntegration(projectId, provider),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey });
      toast.success("Интеграция отключена в этом приложении");
    },
    onError: (error) => toast.error("Не удалось отключить", { description: message(error) }),
  });
  const deleteConnection = useMutation({
    mutationFn: () => deleteAccountIntegration(projectId, "moysklad"),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["app-integrations"] });
      void qc.invalidateQueries({ queryKey: ["moysklad-options"] });
      void sync();
      closeProvider();
      toast.success("Общее подключение МойСклад удалено из Yleum");
    },
    onError: (error) => toast.error("Не удалось удалить подключение", { description: message(error) }),
  });

  const attemptStorageKey = (provider: string) => `omnia:max:integration-attempt:${projectId}:${provider}`;
  const readAttempt = (provider: string): ImplementationAttempt | null => {
    try {
      const saved = JSON.parse(sessionStorage.getItem(attemptStorageKey(provider)) || "null") as ImplementationAttempt | null;
      return saved?.provider === provider && typeof saved.key === "string" && Boolean(saved.key)
        && typeof saved.prompt === "string" && Boolean(saved.prompt.trim()) && !containsChatSecret(saved.prompt)
        && typeof saved.terminal === "boolean" ? saved : null;
    } catch { return null; }
  };
  const implement = useMutation({
    mutationFn: async ({ provider, prompt }: { provider: string; prompt: string }) => {
      if (containsChatSecret(prompt)) throw new Error("Удалите ключи и токены из задания. Используйте защищённую форму подключения.");
      const previous = implementationAttempt.current;
      const replay = previous?.provider === provider && previous.prompt === prompt && !previous.terminal;
      if (quota.exhausted && !replay) throw new Error(FREE_CHAT_UPGRADE_MESSAGE);
      const attempt = previous?.provider === provider && previous.prompt === prompt && !previous.terminal
        ? previous : { provider, prompt, key: `max-integration-${projectId}-${crypto.randomUUID()}`, terminal: false };
      // Persist the logical request before dispatch so a lost response or modal
      // remount replays it instead of charging for another generation.
      try { sessionStorage.setItem(attemptStorageKey(provider), JSON.stringify(attempt)); }
      catch { throw new Error("Не удалось сохранить попытку. Разрешите хранилище браузера и повторите попытку."); }
      implementationAttempt.current = attempt;
      setReplayCandidate({ ...attempt });
      setTerminalFailure(false);
      return sendPrompt(projectId, prompt, "topmix-v1", [], { skipClarify: true, idempotencyKey: attempt.key });
    },
    onSuccess: result => {
      refreshOwnerAfterAcceptedPrompt(qc);
      for (const key of ["messages", "generation", "project-versions"]) {
        void qc.invalidateQueries({ queryKey: [key, projectId] });
      }
      if (result.run_status === "failed" || result.run_status === "cancelled") {
        const attempt = implementationAttempt.current;
        if (attempt) {
          attempt.terminal = true;
          setReplayCandidate({ ...attempt });
          try { sessionStorage.setItem(attemptStorageKey(attempt.provider), JSON.stringify(attempt)); } catch { /* The saved key still safely replays the terminal result. */ }
        }
        setTerminalFailure(true);
        return;
      }
      toast.success("Задание передано в чат приложения");
      onExit?.();
    },
    onError: error => {
      if (isFreeChatRefusal(error)) {
        const attempt = implementationAttempt.current;
        if (attempt) { try { sessionStorage.removeItem(attemptStorageKey(attempt.provider)); } catch { /* Server still rejects new requests. */ } }
        implementationAttempt.current = null;
        setReplayCandidate(null);
        void quota.refresh();
      }
    },
  });
  const busy = connect.isPending || installMoysklad.isPending || claimMoysklad.isPending || saveMoysklad.isPending || saveAmocrm.isPending || bind.isPending || pack.isPending || platformAi.isPending || oauth.isPending || verify.isPending || disconnect.isPending || deleteConnection.isPending || implement.isPending;
  useEffect(() => { onBusyChange?.(busy); }, [busy, onBusyChange]);
  useEffect(() => () => { onBusyChange?.(false); }, [onBusyChange]);

  const openProvider = (provider: IntegrationProvider) => {
    if (provider.connection_mode === "platform") return;
    const connection = connections.get(provider.key);
    setSelected(provider);
    setMoyskladReturnProject(null);
    setMoyskladCode("");
    setMoyskladReplaceConfirmed(false);
    setMoyskladDeleteOpen(false);
    setMoyskladDeleteConfirmed(false);
    connect.reset();
    deleteConnection.reset();
    setMoyskladInstallLink(null);
    resetMoyskladInstall();
    if (provider.key === "moysklad") {
      setMoyskladOrganization(String(connection?.public_config.organization_id ?? ""));
      setMoyskladStore(String(connection?.public_config.store_id ?? ""));
    }
    if (provider.key === "amocrm") {
      const pipelineId = connection?.binding_config.pipeline_id;
      const statusId = connection?.binding_config.status_id;
      setAmocrmPipeline(typeof pipelineId === "number" ? String(pipelineId) : "");
      setAmocrmStatus(typeof statusId === "number" ? String(statusId) : "");
    }
    setValues(
      Object.fromEntries(
        provider.fields.map((field) => [field.key, field.secret ? "" : connection?.public_config[field.key] ?? ""]),
      ),
    );
  };

  const canUseProvider = (providerKey: string) => readyForImplementation(
    catalog.data?.providers.find((provider) => provider.key === providerKey),
    connections.get(providerKey),
  );
  const openImplementation = (providerKey: string) => {
    const request = getBuiltinIntegrationRequest(providerKey);
    if (!request || !canUseProvider(providerKey)) return;
    const saved = embedded ? readAttempt(providerKey) : null;
    implementationAttempt.current = saved;
    setReplayCandidate(saved);
    implement.reset();
    setTerminalFailure(saved?.terminal ?? false);
    setImplementationPrompt(saved?.prompt ?? request.prompt);
    setImplementationProvider(providerKey);
  };
  const proposalHasSecret = embedded && containsChatSecret(implementationPrompt);
  const replayableAttempt = embedded && replayCandidate?.provider === implementationProvider
    && replayCandidate.prompt === implementationPrompt.trim() && !replayCandidate.terminal;
  const quotaBlocksImplementation = quota.exhausted && !replayableAttempt;
  const canImplement = !quotaBlocksImplementation && Boolean(implementationProvider && canUseProvider(implementationProvider) && implementationPrompt.trim() && !proposalHasSecret);
  const startImplementation = () => {
    if (!canImplement || implementationSubmitting.current) return;
    if (embedded && implementationProvider) {
      implementationSubmitting.current = true;
      void implement.mutateAsync({ provider: implementationProvider, prompt: implementationPrompt.trim() })
        .catch(() => undefined).finally(() => { implementationSubmitting.current = false; });
      return;
    }
    try {
      window.sessionStorage.setItem(`omnia:max:starter:${projectId}`, implementationPrompt.trim());
    } catch {
      toast.error("Не удалось передать задание в студию", { description: "Разрешите хранилище браузера и повторите попытку." });
      return;
    }
    onExit?.();
    router.push(`/max/${projectId}?starter=1`);
  };
  const connectedCount = (catalog.data?.providers ?? []).filter((provider) => canUseProvider(provider.key)).length;
  const canSubmit = selected?.fields.every((field) => !field.required || Boolean(values[field.key]?.trim())) ?? false;
  const showCredentials = selected?.key !== "moysklad" || selected.connection_mode === "credentials";
  const replacementAllowed = selected?.key !== "moysklad" || !moyskladConnection || moyskladReplaceConfirmed;
  const credentialFields = selected?.fields.map((field) => (
    <div key={field.key} className="space-y-2">
      <Label htmlFor={`integration-${field.key}`}>{field.label}</Label>
      <Input id={`integration-${field.key}`} type={field.secret ? "password" : "text"} autoComplete="off" value={values[field.key] ?? ""} onChange={(event) => setValues((current) => ({ ...current, [field.key]: event.target.value }))} placeholder={field.placeholder} className="h-11 border-border-default bg-surface" />
      {field.help && <p className="text-xs leading-5 text-fg-tertiary">{field.help}</p>}
    </div>
  ));

  const DetailTitle = embedded ? "h2" : DialogTitle;
  const DetailDescription = embedded ? "p" : DialogDescription;
  const implementationSummary = recognizeBuiltinIntegrationRequest(implementationPrompt);
  const implementationContent = (
    <>
      {embedded && <Button variant="ghost" className="w-fit" disabled={implement.isPending} onClick={() => setImplementationProvider(null)}><ArrowLeft className="size-4" />Назад к сервисам</Button>}
      <DetailTitle className="text-xl font-semibold">Добавить интеграцию в приложение</DetailTitle>
      <DetailDescription className="text-sm text-fg-secondary">Проверьте и при необходимости измените задание. ИИ начнёт доработку только после нажатия кнопки. Не вставляйте ключи и токены.</DetailDescription>
      <p className="text-sm text-fg-secondary">Оплата списывается после успешной сборки приложения. Результат появится в редакторе; затем потребуется публикация.</p>
      <OwnerChatQuotaNotice limited={quota.limited} remaining={quota.remaining} />
      {implementationSummary && <IntegrationRequestCard request={implementationSummary} text={implementationPrompt} expandable={false} />}
      <Label htmlFor="integration-implementation-prompt">Задание для ИИ</Label>
      <Textarea id="integration-implementation-prompt" value={implementationPrompt} disabled={implement.isPending} onChange={(event) => {
        if (implementationSubmitting.current) return;
        setImplementationPrompt(event.target.value); implement.reset(); setTerminalFailure(false);
      }} className="min-h-[240px] border-border-default bg-surface-base" />
      {implementationProvider && !canUseProvider(implementationProvider) && <p className="text-sm text-danger-fg">Подключение требует настройки. Проверьте доступ перед доработкой.</p>}
      {proposalHasSecret && <p role="alert" className="text-sm text-danger-fg">Удалите ключи и токены из задания. Используйте защищённую форму подключения.</p>}
      {embedded && implement.error && <p role="alert" className="text-sm text-danger-fg">{message(implement.error)}</p>}
      {embedded && terminalFailure && <p role="alert" className="text-sm text-danger-fg">Предыдущая доработка завершилась без результата. Можно запустить новую попытку. Оплата — после успешной сборки.</p>}
      <Button disabled={!canImplement || implement.isPending} onClick={startImplementation} className="min-h-11">{implement.isPending && <Loader2 className="size-4 animate-spin" />}{embedded && terminalFailure ? "Повторить доработку" : "Запустить доработку"}</Button>
    </>
  );
  const providerContent = selected && (
    <>
      <header className={cn("shrink-0 border-b border-border-default p-5 sm:p-6", !embedded && "pr-16 sm:pr-14")}>
        {embedded && <Button variant="ghost" className="mb-4 w-fit" disabled={busy} onClick={() => { closeProvider(); setValues({}); }}><ArrowLeft className="size-4" />Назад к сервисам</Button>}
        <div>
          <p className="omnia-kicker text-accent">Подключение</p>
          <DetailTitle className="mt-2 text-2xl font-semibold text-fg-primary">
            {selected.name}
          </DetailTitle>
          <DetailDescription className="mt-2 max-w-[470px] text-sm leading-6 text-fg-secondary">
            {selected.description}
          </DetailDescription>
        </div>
      </header>
      <div className="min-h-0 flex-1 space-y-5 overflow-y-auto overscroll-contain p-5 sm:p-6">
        {selected.key === "moysklad" && showCredentials && (
          <div className="space-y-3 rounded-[10px] border border-border-default p-4">
            <h3 className="text-sm font-semibold">Подключить через API-токен</h3>
            <p className="text-xs leading-5 text-fg-secondary">Используйте существующий токен пользователя вашего аккаунта МойСклад. Нужны права чтения товаров, цен, остатков, организаций и складов, а для приёма заказов — чтение и создание заказов покупателей и контрагентов.</p>
            <p className="text-xs leading-5 text-fg-secondary">После подключения выберите организацию и склад. Один аккаунт и эти настройки общие для всех ваших мини-приложений. Затем нажмите «Добавить в приложение» и опубликуйте доработку.</p>
            <p className="text-xs leading-5 text-fg-secondary">Право создавать заказы проверяется отдельным тестовым заказом. При проверке токена документы не создаются. Сейчас каталог показывает до 100 последних обновлённых товаров и использует первую цену продажи.</p>
            {moyskladConnection && (
              <label className="flex items-start gap-2 text-xs leading-5">
                <input type="checkbox" checked={moyskladReplaceConfirmed} onChange={(event) => setMoyskladReplaceConfirmed(event.target.checked)} className="mt-1" />
                <span>Подтверждаю замену общего подключения для всех моих мини-приложений. После замены потребуется снова выбрать организацию и склад.</span>
              </label>
            )}
            {credentialFields}
            {connect.isError && <p role="alert" className="text-xs text-danger-fg">{message(connect.error)}</p>}
          </div>
        )}
        {selected.key === "moysklad" && (
          <div className="space-y-3 rounded-[10px] border border-accent/30 bg-accent/[.06] p-4">
            <h3 className="text-sm font-semibold">Подключение через решение в каталоге</h3>
            {moyskladInstall.isPending && <p className="text-xs text-fg-secondary">Проверяем доступность решения…</p>}
            {moyskladInstall.isError && <p role="alert" className="text-xs text-danger-fg">Не удалось проверить доступность решения. <button type="button" className="underline" onClick={() => void moyskladInstall.refetch()}>Повторить проверку</button></p>}
            {moyskladInstall.data && !moyskladInstall.data.available && <p className="text-xs leading-5 text-fg-secondary">Публикация решения в каталоге МойСклад ещё ожидается. Если решение уже доступно в вашем аккаунте, откройте его настройки для подключения.</p>}
            {moyskladInstall.data?.available && moyskladInstall.data.install_url && <Button disabled={busy} onClick={startMoyskladInstallation} className="min-h-11 bg-accent text-fg-on-accent hover:bg-accent-hover">{installMoysklad.isPending && <Loader2 className="size-4 animate-spin" />}Установить решение в МойСклад <ExternalLink className="size-3.5" /></Button>}
            {installMoysklad.isError && <p role="alert" className="text-xs text-danger-fg">Не удалось начать установку. Попробуйте снова.</p>}
            {moyskladInstallLink?.projectId === projectId && <a href={moyskladInstallLink.url} target="_blank" rel="noopener noreferrer" className="inline-flex min-h-11 items-center text-xs text-accent underline">Открыть страницу установки, если новая вкладка не появилась</a>}
            <p className="text-xs leading-5 text-fg-secondary">Администратор склада устанавливает решение Yleum, подтверждает доступ в МойСклад и открывает решение. В решении войдите в Yleum, выберите своё мини-приложение и нажмите «Подключить выбранный миниапп». Затем выберите организацию и склад и нажмите «Сохранить настройки». Пароль и токен МойСклад вводить в Yleum не нужно.</p>
            <details id="moysklad-code-fallback" className="space-y-3 rounded-[10px] border border-border-default p-3">
              <summary className="cursor-pointer text-xs text-fg-tertiary">Одноразовый код из прежнего решения</summary>
              <p className="text-xs leading-5 text-fg-secondary">Если ранее установленное решение выдало одноразовый код, можно подтвердить его здесь для этого проекта.</p>
              <Label htmlFor="moysklad-pairing-code">Код из решения Yleum в МойСклад</Label>
              <Input id="moysklad-pairing-code" value={moyskladCode} autoComplete="off" onChange={(event) => setMoyskladCode(event.target.value.trim())} placeholder="Вставьте одноразовый код" className="h-11 border-border-default bg-surface" />
              <Button disabled={moyskladCode.length < 20 || busy} onClick={() => claimMoysklad.mutate(moyskladCode)} className="min-h-11 bg-accent text-fg-on-accent hover:bg-accent-hover">{claimMoysklad.isPending && <Loader2 className="size-4 animate-spin" />}Подключить склад</Button>
            </details>
          </div>
        )}
        {selected.key === "moysklad" && moyskladConnection?.status === "active" && (
          <div className="space-y-3 rounded-[10px] border border-border-default p-4">
            <h3 className="text-sm font-semibold">Организация и склад для заказов</h3>
            <p className="text-xs text-fg-secondary">Выберите, от имени какой организации принимать заказы и с какого склада проверять остатки.</p>
            {moyskladOptions.isPending && <p className="text-sm text-fg-secondary">Загружаем список…</p>}
            {moyskladOptions.isError && <p role="alert" className="text-sm text-danger-fg">Не удалось получить список. Проверьте подключение МойСклад.</p>}
            {moyskladOptions.data && <>
              <Label htmlFor="moysklad-organization">Организация</Label>
              <select id="moysklad-organization" value={moyskladOrganization} onChange={(event) => setMoyskladOrganization(event.target.value)} className="h-11 w-full rounded-md border border-border-default bg-surface px-3 text-sm">
                <option value="">Выберите организацию</option>
                {moyskladOptions.data.organizations.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
              </select>
              <Label htmlFor="moysklad-store">Склад</Label>
              <select id="moysklad-store" value={moyskladStore} onChange={(event) => setMoyskladStore(event.target.value)} className="h-11 w-full rounded-md border border-border-default bg-surface px-3 text-sm">
                <option value="">Выберите склад</option>
                {moyskladOptions.data.stores.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
              </select>
              <Button disabled={!moyskladOrganization || !moyskladStore || busy} onClick={() => saveMoysklad.mutate()} className="min-h-11">Сохранить для заказов</Button>
            </>}
          </div>
        )}
        {selected.key === "amocrm" && amocrmConnection?.status === "active" && (
          <div className="space-y-3 rounded-[10px] border border-border-default p-4">
            <h3 className="text-sm font-semibold">Воронка и этап для новых лидов</h3>
            <p className="text-xs text-fg-secondary">Выберите, в какую воронку и на какой этап направлять заявки. Без настройки amoCRM использует этап по умолчанию.</p>
            {amocrmOptions.isPending && <p className="text-sm text-fg-secondary">Загружаем воронки…</p>}
            {amocrmOptions.isError && <p role="alert" className="text-sm text-danger-fg">Не удалось получить воронки amoCRM. Проверьте подключение и повторите попытку.</p>}
            {amocrmOptions.data && <>
              <Label htmlFor="amocrm-pipeline">Воронка</Label>
              <select
                id="amocrm-pipeline"
                value={amocrmPipeline}
                onChange={(event) => { setAmocrmPipeline(event.target.value); setAmocrmStatus(""); }}
                className="h-11 w-full rounded-md border border-border-default bg-surface px-3 text-sm"
              >
                <option value="">Выберите воронку</option>
                {amocrmOptions.data.pipelines.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
              </select>
              <Label htmlFor="amocrm-status">Этап</Label>
              <select
                id="amocrm-status"
                value={amocrmStatus}
                disabled={!selectedAmocrmPipeline}
                onChange={(event) => setAmocrmStatus(event.target.value)}
                className="h-11 w-full rounded-md border border-border-default bg-surface px-3 text-sm disabled:cursor-not-allowed disabled:opacity-60"
              >
                <option value="">Выберите этап</option>
                {(selectedAmocrmPipeline?.statuses ?? []).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
              </select>
              <Button disabled={!selectedAmocrmPipeline || !selectedAmocrmStatus || saveAmocrm.isPending} onClick={() => saveAmocrm.mutate()} className="min-h-11">{saveAmocrm.isPending && <Loader2 className="size-4 animate-spin" />}Сохранить этап</Button>
            </>}
          </div>
        )}
        {selected.oauth_available && (
          <div className="rounded-[10px] border border-accent/30 bg-accent/[.06] p-4">
            <h3 className="text-sm font-semibold">Рекомендуется: вход через {selected.name}</h3>
            <p className="mt-1 text-xs leading-5 text-fg-secondary">Откроется официальный кабинет. Пароли и API-ключи вводить в Yleum не потребуется.</p>
            <Button onClick={() => oauth.mutate(selected.key)} disabled={oauth.isPending} className="mt-4 bg-accent text-fg-on-accent hover:bg-accent-hover">Войти и разрешить доступ <ExternalLink className="size-3.5" /></Button>
          </div>
        )}
        {selected.key !== "moysklad" && credentialFields}
        {selected.key === "moysklad" && moyskladConnection && (
          <div className="space-y-3 rounded-[10px] border border-border-default p-4">
            <h3 className="text-sm font-semibold">Управление подключением</h3>
            <p className="text-xs leading-5 text-fg-secondary">Кнопка «Отключить МойСклад» в списке сервисов отключает только это приложение. Удаление общего подключения отключит МойСклад во всех ваших мини-приложениях. Токен в самом МойСклад при этом не отзывается.</p>
            {!moyskladDeleteOpen ? <Button variant="outline" disabled={busy} onClick={() => setMoyskladDeleteOpen(true)} className="min-h-11 text-danger-fg">Удалить общее подключение</Button> : <>
              <label htmlFor="moysklad-delete-confirm" className="flex items-start gap-2 text-xs leading-5">
                <input id="moysklad-delete-confirm" type="checkbox" checked={moyskladDeleteConfirmed} onChange={(event) => setMoyskladDeleteConfirmed(event.target.checked)} className="mt-1" />
                <span>Подтверждаю удаление подключения и его привязок ко всем моим приложениям. Для восстановления понадобится подключить МойСклад заново.</span>
              </label>
              <Button variant="outline" disabled={!moyskladDeleteConfirmed || busy} onClick={() => deleteConnection.mutate()} className="min-h-11 text-danger-fg">Подтвердить удаление</Button>
              <Button variant="ghost" disabled={deleteConnection.isPending} onClick={() => { setMoyskladDeleteOpen(false); setMoyskladDeleteConfirmed(false); }}>Отмена</Button>
              {deleteConnection.isError && <p role="alert" className="text-xs text-danger-fg">{message(deleteConnection.error)}</p>}
            </>}
          </div>
        )}
        {showCredentials && selected.fields.length > 0 && <div className="rounded-[10px] bg-surface-base p-4 text-xs leading-5 text-fg-secondary"><ShieldCheck className="mb-2 size-4 text-success-fg" />Секреты сохраняются зашифрованно и не показываются повторно.</div>}
      </div>
      <footer className="flex shrink-0 flex-col-reverse items-stretch gap-3 border-t border-border-default p-5 pb-[max(1rem,env(safe-area-inset-bottom))] sm:flex-row sm:items-center sm:justify-between">
        <a href={selected.docs_url} target="_blank" rel="noreferrer" className="inline-flex min-h-11 items-center text-xs text-fg-tertiary">Документация сервиса</a>
        {showCredentials && selected.fields.length > 0 && <Button disabled={!canSubmit || !replacementAllowed || busy} onClick={() => connect.mutate({ provider: selected.key, payload: values })} className="min-h-11 bg-accent text-fg-on-accent hover:bg-accent-hover">{connect.isPending && <Loader2 className="size-4 animate-spin" />}Проверить и подключить</Button>}
      </footer>
    </>
  );
  const catalogContent = (
    <>
      {/* Абзац предупреждения был первым, что видит человек на экране выбора
          сервисов. Текст остался, но уступил место самим сервисам. */}
      <details className="mt-4 max-w-[850px] rounded-[6px] border border-border-subtle bg-surface-base px-4 py-3">
        <summary className="cursor-pointer text-sm font-medium text-accent-secondary">Что даёт подключение</summary>
        <p className="mt-2 text-sm leading-6 text-fg-secondary">Подключение сервиса не добавляет экраны автоматически. Для встроенного ИИ или после авторизации сервиса выберите «Добавить в приложение», проверьте задание для ИИ и запустите доработку. Пользователи увидят изменения после повторной публикации.</p>
      </details>
      <section className="max-integration-summary mt-6 grid gap-4 lg:grid-cols-[1fr_220px]">
        <div className="rounded-[12px] border border-border-default bg-surface p-6">
          <div className="flex items-start gap-4">
            <span className="grid size-11 shrink-0 place-items-center rounded-[8px] bg-accent text-fg-on-accent"><Sparkles className="size-5" /></span>
            <div>
              <p className="omnia-kicker text-accent">Рекомендуемый набор</p>
              <h2 className="mt-1 text-xl font-semibold">{catalog.data?.recommended_pack?.title ?? "Базовый контур приложения"}</h2>
              <p className="mt-2 text-sm leading-6 text-fg-secondary">{catalog.data?.recommended_pack?.description ?? "Оплата, CRM, учёт и аналитика для вашего сценария."}</p>
            </div>
          </div>
          <Button onClick={() => pack.mutate()} disabled={pack.isPending || !catalog.data?.recommended_pack} className="mt-4 min-h-11 bg-accent text-fg-on-accent hover:bg-accent-hover">
            {pack.isPending ? <Loader2 className="size-4 animate-spin" /> : <Sparkles className="size-4" />}
            Подключить рекомендуемые
          </Button>
        </div>
        <div className="rounded-[12px] border border-border-default bg-surface p-6">
          <p className="omnia-kicker text-fg-tertiary">Состояние</p>
          <p className="mt-3 text-3xl font-semibold">{catalog.isSuccess ? connectedCount : "—"}<span className="text-lg text-fg-tertiary"> / {catalog.isSuccess ? catalog.data.providers.length : "—"}</span></p>
          <p className="mt-2 text-xs text-fg-secondary">сервисов активно в этом проекте</p>
          <div className="mt-5 flex items-center gap-2 text-xs text-success-fg"><ShieldCheck className="size-4" /> Секреты зашифрованы</div>
        </div>
      </section>

      <section className="mt-6">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex flex-wrap gap-2">
            {(Object.keys(categories) as Array<IntegrationCategory | "all">).map((key) => {
              const item = categories[key];
              return (
                <button key={key} aria-pressed={category === key} onClick={() => setCategory(key)} className={cn("inline-flex h-11 shrink-0 items-center gap-2 rounded-[8px] border px-3 text-xs sm:h-9", category === key ? "border-accent bg-accent-subtle text-accent-secondary" : "border-border-default bg-surface text-fg-secondary")}>
                  <item.icon className="size-3.5" />{item.label}
                </button>
              );
            })}
          </div>
          <label className="relative block w-full lg:w-[280px]">
            <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-fg-tertiary" />
            <Input aria-label="Найти сервис" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Найти сервис" className="h-11 border-border-default bg-surface pl-9 sm:h-9" />
          </label>
        </div>

        <div className="mt-5 overflow-hidden rounded-[12px] border border-border-default bg-surface">
          <div className="hidden grid-cols-[1.3fr_.8fr_130px_190px] border-b border-border-default px-5 py-3 font-mono text-[11px] uppercase tracking-[.1em] text-fg-tertiary lg:grid">
            <span>Сервис</span><span>Возможности</span><span>Статус</span><span />
          </div>
          {catalog.isLoading ? (
            <div className="grid min-h-[260px] place-items-center"><Loader2 className="size-5 animate-spin text-accent" /></div>
          ) : catalog.isError ? (
            <div className="grid min-h-[260px] place-items-center px-6 py-10 text-center">
              <div className="max-w-[420px]">
                <Plug className="mx-auto size-7 text-fg-tertiary" />
                <h3 className="mt-4 text-base font-semibold">Не удалось загрузить сервисы</h3>
                <p className="mt-2 text-sm leading-6 text-fg-secondary">{message(catalog.error)}</p>
                <Button
                  variant="outline"
                  className="mt-5 border-border-default bg-surface"
                  onClick={() => void catalog.refetch()}
                >
                  <RefreshCw className="size-4" />
                  Повторить
                </Button>
              </div>
            </div>
          ) : (
            <div className="divide-y divide-border-subtle">
              {visible.length === 0 && <div className="px-6 py-12 text-center"><h3 className="font-semibold">Ничего не найдено</h3><p className="mt-2 text-sm text-fg-secondary">Попробуйте другое название или категорию.</p><Button variant="outline" className="mt-4" onClick={() => { setSearch(""); setCategory("all"); }}>Сбросить фильтры</Button></div>}
              {visible.map((provider) => {
                const connection = connections.get(provider.key);
                const platform = provider.connection_mode === "platform";
                const connected = readyForImplementation(provider, connection);
                const needsSetup = connection?.bound_to_project && !connected;
                const reusable = connection?.status === "active" && !connection.bound_to_project;
                const Icon = providerIcons[provider.key] ?? CloudCog;
                return (
                  <article key={provider.key} className="grid gap-4 p-5 lg:grid-cols-[minmax(0,1fr)_200px] lg:items-center">
                    <div className="flex items-start gap-3">
                      <span className="grid size-10 shrink-0 place-items-center rounded-[6px] border border-border-default bg-surface text-accent"><Icon className="size-4" /></span>
                      <div className="min-w-0">
                        <h3 className="flex flex-wrap items-center gap-2 text-sm font-semibold">
                          {provider.name}
                          {/* Состояние — значком у названия: колонка со словом
                              «Не подключено» повторяла кнопку в той же строке. */}
                          {connected ? <span className="inline-flex items-center gap-1 text-xs font-medium text-success-fg"><Check className="size-3.5" />{platform ? "Встроено" : "Подключено"}</span>
                            : needsSetup ? <span className="text-xs font-medium text-danger-fg">Требуется настройка</span>
                            : reusable ? <span className="text-xs font-medium text-accent-secondary">Есть у бизнеса</span>
                            : !provider.available ? <span className="text-xs font-medium text-fg-tertiary">Готовим</span>
                            : null}
                        </h3>
                        <p className="mt-1 text-xs leading-5 text-fg-tertiary">{provider.key === "llmgw" ? "Работает через LLMGW; расходы с баланса владельца" : provider.description}</p>
                        {provider.capabilities.length > 0 && (
                          <p className="mt-1 text-xs text-fg-tertiary">{provider.capabilities.slice(0, 3).join(" · ")}</p>
                        )}
                      </div>
                    </div>
                    <div className="flex flex-wrap justify-end gap-1">
                      {platform ? (
                        provider.key === "llmgw" && provider.available ? (
                          <>
                            {connected && getBuiltinIntegrationRequest(provider.key) && <Button size="sm" variant="outline" className="h-11 sm:h-8" onClick={() => openImplementation(provider.key)}>Добавить в приложение</Button>}
                            <Button size="sm" variant="outline" className="h-11 sm:h-8" disabled={platformAi.isPending} onClick={() => platformAi.mutate(!connected)}>{connected ? "Выключить ИИ" : "Включить ИИ"}</Button>
                          </>
                        ) : null
                      ) : connected ? (
                        <>
                          {getBuiltinIntegrationRequest(provider.key) && <Button size="sm" variant="outline" className="h-11 sm:h-8" onClick={() => openImplementation(provider.key)}>Добавить в приложение</Button>}
                          <button onClick={() => verify.mutate(provider.key)} className="grid size-11 place-items-center rounded-[8px] text-fg-secondary hover:bg-surface-base sm:size-8" aria-label={`Проверить ${provider.name}`}><RefreshCw className="size-3.5" /></button>
                          <button onClick={() => disconnect.mutate(provider.key)} className="grid size-11 place-items-center rounded-[8px] text-fg-tertiary hover:bg-danger/10 hover:text-danger-fg sm:size-8" aria-label={`Отключить ${provider.name}`}><Trash2 className="size-3.5" /></button>
                          <Button size="sm" variant="outline" className="h-11 sm:h-8" onClick={() => openProvider(provider)}>Настроить</Button>
                        </>
                      ) : connection?.status === "error" ? (
                        <>
                          <Button size="sm" variant="outline" className="h-11 sm:h-8" disabled={verify.isPending} onClick={() => verify.mutate(provider.key)} aria-label={`Проверить ${provider.name}`}>Проверить снова</Button>
                          <Button size="sm" variant="outline" className="h-11 sm:h-8" onClick={() => openProvider(provider)}>Переподключить</Button>
                        </>
                      ) : reusable ? (
                        <Button size="sm" variant="outline" className="h-11 sm:h-8" onClick={() => bind.mutate(provider.key)}>Использовать</Button>
                      ) : provider.available ? (
                        <Button size="sm" variant="outline" className="h-11 sm:h-8" onClick={() => openProvider(provider)}>Подключить <ChevronRight className="size-3.5" /></Button>
                      ) : (
                        <a href={provider.docs_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-xs text-fg-tertiary">Требования <ExternalLink className="size-3" /></a>
                      )}
                    </div>
                  </article>
                );
              })}
            </div>
          )}
        </div>
      </section>
    </>
  );

  if (embedded) {
    return (
      <div className="max-integration-embedded">
        {selected ? (
          <section className="flex min-h-0 flex-col">{providerContent}</section>
        ) : implementationProvider ? (
          <section className="grid gap-5">{implementationContent}</section>
        ) : catalogContent}
      </div>
    );
  }

  return (
    <MaxSectionShell
      projectId={projectId}
      projectName={projectName}
      active="integrations"
      eyebrow="Дополнительный шаг"
      title="Интеграции"
      lead="Авторизуйте сервис один раз для бизнеса. Секреты хранятся отдельно от исходного кода, а приложение получает только безопасные функции."
    >
      {catalogContent}
      <Dialog open={Boolean(implementationProvider)} onOpenChange={(open) => { if (!open) setImplementationProvider(null); }}>
        <DialogContent data-product-shell data-max-studio className="max-h-[90dvh] overflow-y-auto border-border-default bg-surface text-fg-primary sm:max-w-[600px]">
          {implementationContent}
        </DialogContent>
      </Dialog>
      <Dialog
        open={Boolean(selected)}
        onOpenChange={(open) => {
          if (!open && !connect.isPending) closeProvider();
        }}
      >
        {selected && (
          <DialogContent
            data-product-shell data-max-studio
            className="flex max-h-[calc(100dvh-1rem)] flex-col gap-0 overflow-hidden border-border-default bg-surface p-0 text-fg-primary sm:max-h-[90dvh] sm:max-w-[600px] sm:p-0"
          >
            {providerContent}
          </DialogContent>
        )}
      </Dialog>
    </MaxSectionShell>
  );
}
