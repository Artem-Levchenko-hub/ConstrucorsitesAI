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
  connectAppIntegration,
  disconnectAppIntegration,
  getIntegrationCatalog,
  startIntegrationOAuth,
  setPlatformAiEnabled,
  verifyAppIntegration,
} from "@/lib/api/app-integrations";
import { ApiError } from "@/lib/api/client";
import { syncMaxManagedKit } from "@/lib/api/max-studio";
import { sendPrompt } from "@/lib/api/messages";
import type { AppIntegration, IntegrationCategory, IntegrationProvider } from "@/lib/api/types";
import { containsChatSecret } from "@/lib/max-chat-credentials";
import { cn } from "@/lib/utils";

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

// Static implementation briefs never interpolate account metadata or credentials.
const implementationFeatures: Record<string, string> = {
  yookassa: "Добавь оплату заказа через ЮKassa: создание платежа и проверку его статуса. Подтверждай оплату только по серверному статусу, а не по возврату пользователя со страницы оплаты. Возвраты не поддерживаются.",
  iiko: "Добавь меню iiko с категориями и блюдами. Доступно только чтение меню; создание заказов не поддерживается.",
  bitrix24: "Добавь форму заявки с созданием лида в Битрикс24 и подтверждением результата. Не повторяй отправку при неизвестном результате предыдущей попытки.",
  amocrm: "Добавь форму заявки с созданием лида в amoCRM и подтверждением результата. Не повторяй отправку при неизвестном результате предыдущей попытки.",
  moysklad: "Добавь каталог товаров и цены из МойСклад. Реальные складские остатки пока недоступны; не показывай товары как имеющиеся в наличии на основании каталога.",
  yandex_metrica: "Подключи счётчик Яндекс Метрики к приложению через управляемую интеграцию.",
  llmgw: "Добавь ИИ-помощника с отправкой сообщений через встроенный LLMGW и отображением ответа. Используй requestOmniaAI; расходы оплачиваются с баланса владельца приложения. Пользователю не нужны API-ключи или отдельное подключение ИИ.",
};
const readyForImplementation = (
  provider: IntegrationProvider | undefined,
  connection: AppIntegration | undefined,
) => Boolean(provider && (provider.connection_mode === "platform"
  ? provider.available && provider.enabled === true
  : connection?.status === "active" && connection.bound_to_project && connection.binding_status === "ready"));

const message = (error: unknown) => {
  if (error instanceof ApiError) return error.message;
  if (error instanceof TypeError && /fetch/i.test(error.message)) {
    return "Сервис временно недоступен. Проверьте соединение и повторите попытку.";
  }
  return error instanceof Error ? error.message : "Не удалось выполнить действие";
};

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
  const [implementationProvider, setImplementationProvider] = useState<string | null>(null);
  const [implementationPrompt, setImplementationPrompt] = useState("");
  const implementationSubmitting = useRef(false);
  const implementationAttempt = useRef<ImplementationAttempt | null>(null);
  const [terminalFailure, setTerminalFailure] = useState(false);
  const [category, setCategory] = useState<IntegrationCategory | "all">("all");
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<IntegrationProvider | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const queryKey = ["app-integrations", projectId];
  const catalog = useQuery({
    queryKey,
    queryFn: () => getIntegrationCatalog(projectId),
    retry: false,
  });

  useEffect(() => {
    void syncMaxManagedKit(projectId).catch(() => undefined);
  }, [projectId]);

  const connections = useMemo(
    () => new Map((catalog.data?.connections ?? []).map((item) => [item.provider, item])),
    [catalog.data?.connections],
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
      void qc.invalidateQueries({ queryKey });
      void sync();
      setSelected(null);
      setValues({});
      toast.success("Доступ к сервису подтверждён");
    },
    onError: (error) => toast.error("Проверка не пройдена", { description: message(error) }),
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
      toast.success("Интеграция отключена");
    },
    onError: (error) => toast.error("Не удалось отключить", { description: message(error) }),
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
      const attempt = previous?.provider === provider && previous.prompt === prompt && !previous.terminal
        ? previous : { provider, prompt, key: `max-integration-${projectId}-${crypto.randomUUID()}`, terminal: false };
      // Persist the logical request before dispatch so a lost response or modal
      // remount replays it instead of charging for another generation.
      try { sessionStorage.setItem(attemptStorageKey(provider), JSON.stringify(attempt)); }
      catch { throw new Error("Не удалось сохранить попытку. Разрешите хранилище браузера и повторите попытку."); }
      implementationAttempt.current = attempt;
      setTerminalFailure(false);
      return sendPrompt(projectId, prompt, "topmix-v1", [], { skipClarify: true, idempotencyKey: attempt.key });
    },
    onSuccess: result => {
      for (const key of ["messages", "generation", "project-versions"]) {
        void qc.invalidateQueries({ queryKey: [key, projectId] });
      }
      if (result.run_status === "failed" || result.run_status === "cancelled") {
        const attempt = implementationAttempt.current;
        if (attempt) {
          attempt.terminal = true;
          try { sessionStorage.setItem(attemptStorageKey(attempt.provider), JSON.stringify(attempt)); } catch { /* The saved key still safely replays the terminal result. */ }
        }
        setTerminalFailure(true);
        return;
      }
      toast.success("Задание передано в чат приложения");
      onExit?.();
    },
  });
  const busy = connect.isPending || bind.isPending || pack.isPending || platformAi.isPending || oauth.isPending || verify.isPending || disconnect.isPending || implement.isPending;
  useEffect(() => { onBusyChange?.(busy); }, [busy, onBusyChange]);
  useEffect(() => () => { onBusyChange?.(false); }, [onBusyChange]);

  const openProvider = (provider: IntegrationProvider) => {
    if (provider.connection_mode === "platform") return;
    const connection = connections.get(provider.key);
    setSelected(provider);
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
    const feature = implementationFeatures[providerKey];
    if (!feature || !canUseProvider(providerKey)) return;
    const saved = embedded ? readAttempt(providerKey) : null;
    implementationAttempt.current = saved;
    implement.reset();
    setTerminalFailure(saved?.terminal ?? false);
    setImplementationPrompt(saved?.prompt ?? `${feature}\nИспользуй только доступные управляемые методы интеграции. Не запрашивай и не вставляй секреты в код или сообщения. Добавь состояния загрузки, пустого результата и ошибки. Проверь сценарий и сообщи, что проверено, а что требует проверки с реальным аккаунтом.`);
    setImplementationProvider(providerKey);
  };
  const proposalHasSecret = embedded && containsChatSecret(implementationPrompt);
  const canImplement = Boolean(implementationProvider && canUseProvider(implementationProvider) && implementationPrompt.trim() && !proposalHasSecret);
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

  const DetailTitle = embedded ? "h2" : DialogTitle;
  const DetailDescription = embedded ? "p" : DialogDescription;
  const implementationContent = (
    <>
      {embedded && <Button variant="ghost" className="w-fit" disabled={implement.isPending} onClick={() => setImplementationProvider(null)}><ArrowLeft className="size-4" />Назад к сервисам</Button>}
      <DetailTitle className="text-xl font-semibold">Добавить интеграцию в приложение</DetailTitle>
      <DetailDescription className="text-sm text-fg-secondary">Проверьте и при необходимости измените задание. ИИ начнёт доработку только после нажатия кнопки. Не вставляйте ключи и токены.</DetailDescription>
      <p className="text-sm text-fg-secondary">Доработка расходует баланс владельца. Результат появится в новой версии приложения; затем потребуется публикация.</p>
      <Label htmlFor="integration-implementation-prompt">Задание для ИИ</Label>
      <Textarea id="integration-implementation-prompt" value={implementationPrompt} disabled={implement.isPending} onChange={(event) => {
        if (implementationSubmitting.current) return;
        setImplementationPrompt(event.target.value); implement.reset(); setTerminalFailure(false);
      }} className="min-h-[240px] border-border-default bg-surface-base" />
      {implementationProvider && !canUseProvider(implementationProvider) && <p className="text-sm text-danger-fg">Подключение требует настройки. Проверьте доступ перед доработкой.</p>}
      {proposalHasSecret && <p role="alert" className="text-sm text-danger-fg">Удалите ключи и токены из задания. Используйте защищённую форму подключения.</p>}
      {embedded && implement.error && <p role="alert" className="text-sm text-danger-fg">{message(implement.error)}</p>}
      {embedded && terminalFailure && <p role="alert" className="text-sm text-danger-fg">Предыдущая доработка завершилась без результата. Новая попытка расходует баланс владельца.</p>}
      <Button disabled={!canImplement || implement.isPending} onClick={startImplementation} className="min-h-11">{implement.isPending && <Loader2 className="size-4 animate-spin" />}{embedded && terminalFailure ? "Повторить доработку" : "Запустить доработку"}</Button>
    </>
  );
  const providerContent = selected && (
    <>
      <header className={cn("shrink-0 border-b border-border-default p-5 sm:p-6", !embedded && "pr-16 sm:pr-14")}>
        {embedded && <Button variant="ghost" className="mb-4 w-fit" disabled={connect.isPending} onClick={() => { setSelected(null); setValues({}); }}><ArrowLeft className="size-4" />Назад к сервисам</Button>}
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
        {selected.oauth_available && (
          <div className="rounded-[10px] border border-[#4f81f7]/30 bg-accent/[.06] p-4">
            <h3 className="text-sm font-semibold">Рекомендуется: вход через {selected.name}</h3>
            <p className="mt-1 text-xs leading-5 text-fg-secondary">Откроется официальный кабинет. Пароли и API-ключи вводить в Omnia не потребуется.</p>
            <Button onClick={() => oauth.mutate(selected.key)} disabled={oauth.isPending} className="mt-4 bg-accent text-fg-on-accent hover:bg-accent-hover">Войти и разрешить доступ <ExternalLink className="size-3.5" /></Button>
          </div>
        )}
        {selected.fields.map((field) => (
          <div key={field.key} className="space-y-2">
            <Label htmlFor={`integration-${field.key}`}>{field.label}</Label>
            <Input id={`integration-${field.key}`} type={field.secret ? "password" : "text"} autoComplete="off" value={values[field.key] ?? ""} onChange={(event) => setValues((current) => ({ ...current, [field.key]: event.target.value }))} placeholder={field.placeholder} className="h-11 border-border-default bg-surface" />
            {field.help && <p className="text-xs leading-5 text-fg-tertiary">{field.help}</p>}
          </div>
        ))}
        <div className="rounded-[10px] bg-surface-base p-4 text-xs leading-5 text-fg-secondary"><ShieldCheck className="mb-2 size-4 text-success-fg" />Секреты сохраняются зашифрованно и не показываются повторно.</div>
      </div>
      <footer className="flex shrink-0 flex-col-reverse items-stretch gap-3 border-t border-border-default p-5 pb-[max(1rem,env(safe-area-inset-bottom))] sm:flex-row sm:items-center sm:justify-between">
        <a href={selected.docs_url} target="_blank" rel="noreferrer" className="inline-flex min-h-11 items-center text-xs text-fg-tertiary">Документация сервиса</a>
        {selected.fields.length > 0 && <Button disabled={!canSubmit || connect.isPending} onClick={() => connect.mutate({ provider: selected.key, payload: values })} className="min-h-11 bg-accent text-fg-on-accent hover:bg-accent-hover">{connect.isPending && <Loader2 className="size-4 animate-spin" />}Проверить и подключить</Button>}
      </footer>
    </>
  );
  const catalogContent = (
    <>
      <p className="mt-4 max-w-[850px] text-sm leading-6 text-fg-secondary">Подключение сервиса не добавляет экраны автоматически. Для встроенного ИИ или после авторизации сервиса выберите «Добавить в приложение», проверьте задание для ИИ и запустите доработку. Изменения попадут в опубликованную версию после повторной публикации.</p>
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
          <div className="hidden grid-cols-[1.3fr_.8fr_130px_190px] border-b border-border-default px-5 py-3 font-mono text-[9px] uppercase tracking-[.1em] text-fg-tertiary lg:grid">
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
                  <article key={provider.key} className="grid gap-4 p-5 lg:grid-cols-[1.3fr_.8fr_130px_190px] lg:items-center">
                    <div className="flex items-center gap-3">
                      <span className="grid size-10 shrink-0 place-items-center rounded-[8px] border border-border-default bg-surface text-accent"><Icon className="size-4" /></span>
                      <div><h3 className="text-sm font-semibold">{provider.name}</h3><p className={cn("mt-1 text-xs text-fg-tertiary", !platform && "line-clamp-1")}>{provider.key === "llmgw" ? "Работает через LLMGW; расходы с баланса владельца" : provider.description}</p></div>
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {provider.capabilities.slice(0, 3).map((item) => <span key={item} className="rounded-full border border-border-default px-2 py-1 text-[9px] text-fg-secondary">{item}</span>)}
                    </div>
                    <div>
                      {connected ? <span className="inline-flex items-center gap-1.5 text-xs font-medium text-success-fg"><Check className="size-3.5" />{platform ? "Встроено" : "Подключено"}</span>
                        : platform && provider.available ? <span className="text-xs text-fg-tertiary">Не включено</span>
                        : needsSetup ? <span className="text-xs text-danger-fg">Требуется настройка</span>
                        : reusable ? <span className="text-xs text-accent-secondary">Есть у бизнеса</span>
                        : provider.available ? <span className="text-xs text-fg-tertiary">Не подключено</span>
                        : <span className="text-xs text-fg-tertiary">Готовим</span>}
                    </div>
                    <div className="flex flex-wrap justify-end gap-1">
                      {platform ? (
                        provider.key === "llmgw" && provider.available ? (
                          <>
                            {connected && implementationFeatures[provider.key] && <Button size="sm" className="h-11 sm:h-8" onClick={() => openImplementation(provider.key)}>Добавить в приложение</Button>}
                            <Button size="sm" variant="outline" className="h-11 sm:h-8" disabled={platformAi.isPending} onClick={() => platformAi.mutate(!connected)}>{connected ? "Выключить ИИ" : "Включить ИИ"}</Button>
                          </>
                        ) : null
                      ) : connected ? (
                        <>
                          {implementationFeatures[provider.key] && <Button size="sm" className="h-11 sm:h-8" onClick={() => openImplementation(provider.key)}>Добавить в приложение</Button>}
                          <button onClick={() => verify.mutate(provider.key)} className="grid size-11 place-items-center rounded-[8px] text-fg-secondary hover:bg-surface-base sm:size-8" aria-label={`Проверить ${provider.name}`}><RefreshCw className="size-3.5" /></button>
                          <button onClick={() => disconnect.mutate(provider.key)} className="grid size-11 place-items-center rounded-[8px] text-fg-tertiary hover:bg-[#c63d35]/10 hover:text-danger-fg sm:size-8" aria-label={`Отключить ${provider.name}`}><Trash2 className="size-3.5" /></button>
                          <Button size="sm" variant="outline" className="h-11 sm:h-8" onClick={() => openProvider(provider)}>Настроить</Button>
                        </>
                      ) : reusable ? (
                        <Button size="sm" className="h-11 sm:h-8" onClick={() => bind.mutate(provider.key)}>Использовать</Button>
                      ) : provider.available ? (
                        <Button size="sm" className="h-11 sm:h-8" onClick={() => openProvider(provider)}>Подключить <ChevronRight className="size-3.5" /></Button>
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
          if (!open && !connect.isPending) setSelected(null);
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
