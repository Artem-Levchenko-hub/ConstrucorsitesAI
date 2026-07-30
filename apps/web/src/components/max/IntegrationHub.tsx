"use client";

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
  Store,
  Trash2,
  Truck,
  UsersRound,
} from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  connectAppIntegration,
  disconnectAppIntegration,
  getIntegrationCatalog,
  verifyAppIntegration,
} from "@/lib/api/app-integrations";
import { ApiError } from "@/lib/api/client";
import type {
  AppIntegration,
  IntegrationCategory,
  IntegrationProvider,
} from "@/lib/api/types";
import { cn } from "@/lib/utils";

const CATEGORY_COPY: Record<
  IntegrationCategory | "all",
  { label: string; icon: LucideIcon }
> = {
  all: { label: "Все", icon: Plug },
  payments: { label: "Оплата", icon: CircleDollarSign },
  restaurant: { label: "Рестораны", icon: Store },
  crm: { label: "CRM", icon: UsersRound },
  inventory: { label: "Товары", icon: PackageSearch },
  analytics: { label: "Аналитика", icon: BarChart3 },
  booking: { label: "Запись", icon: CalendarDays },
  delivery: { label: "Доставка", icon: Truck },
};

const PROVIDER_ICONS: Record<string, LucideIcon> = {
  yookassa: CircleDollarSign,
  iiko: Store,
  bitrix24: UsersRound,
  moysklad: PackageSearch,
  yandex_metrica: BarChart3,
  rkeeper: Store,
  yclients: CalendarDays,
  amocrm: UsersRound,
  one_c: PackageSearch,
  cdek: Truck,
};

function errorMessage(error: unknown): string {
  return error instanceof ApiError
    ? error.message
    : "Не удалось выполнить действие";
}

function ProviderMark({ provider }: { provider: IntegrationProvider }) {
  const Icon = PROVIDER_ICONS[provider.key] ?? CloudCog;
  return (
    <span className="flex h-11 w-11 items-center justify-center border border-white/[0.1] bg-white/[0.04] text-[#7897f4]">
      <Icon className="h-5 w-5" />
    </span>
  );
}

function ProviderCard({
  provider,
  connection,
  busy,
  onOpen,
  onVerify,
  onDisconnect,
}: {
  provider: IntegrationProvider;
  connection?: AppIntegration;
  busy: boolean;
  onOpen: () => void;
  onVerify: () => void;
  onDisconnect: () => void;
}) {
  const connected = connection?.status === "active";
  return (
    <article
      className={cn(
        "group flex min-h-[280px] flex-col border p-5 transition-colors",
        connected
          ? "border-[#315bd7]/50 bg-[#315bd7]/[0.06]"
          : "border-white/[0.1] bg-[#171815] hover:border-white/[0.2]",
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <ProviderMark provider={provider} />
        {connected ? (
          <span className="inline-flex items-center gap-1.5 border border-emerald-400/25 bg-emerald-400/[0.07] px-2 py-1 text-[10px] font-medium uppercase tracking-[0.12em] text-emerald-300">
            <Check className="h-3 w-3" />
            Подключено
          </span>
        ) : provider.available ? (
          <span className="border border-white/[0.1] px-2 py-1 text-[10px] uppercase tracking-[0.12em] text-white/45">
            Доступно
          </span>
        ) : (
          <span className="border border-white/[0.08] px-2 py-1 text-[10px] uppercase tracking-[0.12em] text-white/25">
            Готовим
          </span>
        )}
      </div>

      <div className="mt-5">
        <div className="flex items-center gap-2">
          <h3 className="text-lg font-semibold text-white">{provider.name}</h3>
          {provider.recommended && (
            <span className="text-[9px] font-medium uppercase tracking-[0.13em] text-[#7897f4]">
              Приоритет
            </span>
          )}
        </div>
        <p className="mt-2 min-h-10 text-sm leading-5 text-white/45">
          {provider.description}
        </p>
      </div>

      <div className="mt-4 flex flex-wrap gap-1.5">
        {provider.capabilities.slice(0, 4).map((capability) => (
          <span
            key={capability}
            className="border border-white/[0.08] bg-black/10 px-2 py-1 text-[10px] text-white/45"
          >
            {capability}
          </span>
        ))}
      </div>

      <div className="mt-auto pt-5">
        {connection?.account_label && (
          <p className="mb-3 truncate text-xs text-white/35">
            {connection.account_label}
          </p>
        )}
        {connection?.last_error && (
          <p className="mb-3 line-clamp-2 text-xs leading-4 text-red-300/80">
            {connection.last_error}
          </p>
        )}
        {connected ? (
          <div className="grid grid-cols-[1fr_auto_auto] gap-2">
            <Button
              variant="secondary"
              size="sm"
              disabled={busy}
              onClick={onOpen}
              className="border-white/[0.1] bg-white/[0.04] text-white hover:bg-white/[0.08]"
            >
              Настроить
            </Button>
            <Button
              variant="ghost"
              size="icon"
              disabled={busy}
              onClick={onVerify}
              aria-label={`Проверить ${provider.name}`}
              className="text-white/45 hover:bg-white/[0.06] hover:text-white"
            >
              <RefreshCw className={cn("h-4 w-4", busy && "animate-spin")} />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              disabled={busy}
              onClick={onDisconnect}
              aria-label={`Отключить ${provider.name}`}
              className="text-white/35 hover:bg-red-500/10 hover:text-red-300"
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          </div>
        ) : provider.available ? (
          <Button className="w-full" size="sm" onClick={onOpen}>
            Подключить
            <ChevronRight className="h-3.5 w-3.5" />
          </Button>
        ) : (
          <a
            href={provider.docs_url}
            target="_blank"
            rel="noreferrer"
            className="flex items-center justify-between border-t border-white/[0.08] pt-3 text-xs text-white/35 transition-colors hover:text-white/65"
          >
            {provider.requirement ?? "Посмотреть требования"}
            <ExternalLink className="ml-3 h-3.5 w-3.5 shrink-0" />
          </a>
        )}
      </div>
    </article>
  );
}

export function IntegrationHub({
  projectId,
  projectName,
}: {
  projectId: string;
  projectName: string;
}) {
  const queryClient = useQueryClient();
  const [category, setCategory] = useState<IntegrationCategory | "all">("all");
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<IntegrationProvider | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const queryKey = ["app-integrations", projectId];
  const catalog = useQuery({
    queryKey,
    queryFn: () => getIntegrationCatalog(projectId),
  });

  const connect = useMutation({
    mutationFn: ({
      provider,
      payload,
    }: {
      provider: string;
      payload: Record<string, string>;
    }) => connectAppIntegration(projectId, provider, payload),
    onSuccess: (connection) => {
      queryClient.setQueryData(queryKey, (current: typeof catalog.data) =>
        current
          ? {
              ...current,
              connections: [
                ...current.connections.filter(
                  (item) => item.provider !== connection.provider,
                ),
                connection,
              ],
            }
          : current,
      );
      setSelected(null);
      setValues({});
      toast.success("Интеграция подключена", {
        description: connection.account_label ?? undefined,
      });
    },
    onError: (error) =>
      toast.error("Проверка не пройдена", { description: errorMessage(error) }),
  });
  const verify = useMutation({
    mutationFn: (provider: string) =>
      verifyAppIntegration(projectId, provider),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey });
      toast.success("Подключение работает");
    },
    onError: (error) => {
      queryClient.invalidateQueries({ queryKey });
      toast.error("Интеграция не отвечает", {
        description: errorMessage(error),
      });
    },
  });
  const disconnect = useMutation({
    mutationFn: (provider: string) =>
      disconnectAppIntegration(projectId, provider),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey });
      toast.success("Интеграция отключена");
    },
    onError: (error) =>
      toast.error("Не удалось отключить", {
        description: errorMessage(error),
      }),
  });

  const connections = useMemo(
    () =>
      new Map(
        (catalog.data?.connections ?? []).map((connection) => [
          connection.provider,
          connection,
        ]),
      ),
    [catalog.data?.connections],
  );
  const visibleProviders = useMemo(() => {
    const needle = search.trim().toLocaleLowerCase("ru-RU");
    return (catalog.data?.providers ?? []).filter(
      (provider) =>
        (category === "all" || provider.category === category) &&
        (!needle ||
          provider.name.toLocaleLowerCase("ru-RU").includes(needle) ||
          provider.description.toLocaleLowerCase("ru-RU").includes(needle) ||
          provider.capabilities.some((item) =>
            item.toLocaleLowerCase("ru-RU").includes(needle),
          )),
    );
  }, [catalog.data?.providers, category, search]);

  function openProvider(provider: IntegrationProvider) {
    const connection = connections.get(provider.key);
    setSelected(provider);
    setValues(
      Object.fromEntries(
        provider.fields.map((field) => [
          field.key,
          field.secret ? "" : connection?.public_config[field.key] ?? "",
        ]),
      ),
    );
  }

  const canSubmit =
    selected?.fields.every(
      (field) => !field.required || Boolean(values[field.key]?.trim()),
    ) ?? false;
  const connectedCount = catalog.data?.connections.filter(
    (item) => item.status === "active",
  ).length;

  return (
    <div className="min-h-0 flex-1 overflow-y-auto bg-[#10110f] text-white">
      <header className="sticky top-0 z-20 border-b border-white/[0.1] bg-[#10110f]/95 px-5 py-4 backdrop-blur-xl sm:px-8">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-4">
          <div className="flex min-w-0 items-center gap-4">
            <Button
              asChild
              variant="ghost"
              size="icon"
              className="shrink-0 text-white/55 hover:bg-white/[0.06] hover:text-white"
            >
              <Link href={`/max/${projectId}`} aria-label="Назад в редактор">
                <ArrowLeft className="h-4 w-4" />
              </Link>
            </Button>
            <div className="min-w-0">
              <p className="text-[10px] font-medium uppercase tracking-[0.16em] text-[#7897f4]">
                {projectName}
              </p>
              <h1 className="mt-0.5 truncate text-lg font-semibold">
                Интеграции
              </h1>
            </div>
          </div>
          <div className="hidden text-right sm:block">
            <p className="text-xs text-white/35">Активные подключения</p>
            <p className="mt-0.5 text-sm font-medium text-white/80">
              {connectedCount ?? 0} из {catalog.data?.providers.length ?? 0}
            </p>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-5 py-8 sm:px-8 sm:py-10">
        <section className="grid gap-6 border-b border-white/[0.1] pb-8 lg:grid-cols-[1fr_360px]">
          <div>
            <p className="text-[10px] font-medium uppercase tracking-[0.18em] text-white/35">
              Integration Hub
            </p>
            <h2 className="mt-3 max-w-3xl text-3xl font-semibold tracking-[-0.035em] sm:text-4xl">
              Подключите бизнес-сервисы один раз.
            </h2>
            <p className="mt-4 max-w-2xl text-sm leading-6 text-white/45 sm:text-base">
              Студия проверит доступ и сохранит секреты зашифрованно. Ключи не
              попадают в промпт, браузер или исходный код приложения.
            </p>
          </div>
          <div className="border-l-2 border-[#315bd7] bg-white/[0.025] p-5">
            <p className="text-xs font-medium text-white/75">Как подключаем</p>
            <ol className="mt-4 space-y-3 text-xs leading-5 text-white/40">
              <li>1. Вы вводите реквизиты из кабинета сервиса.</li>
              <li>2. Integration Hub делает безопасную проверку доступа.</li>
              <li>3. Подключение закрепляется за этим приложением.</li>
            </ol>
          </div>
        </section>

        <section className="pt-7">
          <div className="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
            <div className="flex gap-2 overflow-x-auto pb-1">
              {(Object.keys(CATEGORY_COPY) as Array<
                IntegrationCategory | "all"
              >).map((key) => {
                const item = CATEGORY_COPY[key];
                const Icon = item.icon;
                return (
                  <button
                    key={key}
                    type="button"
                    onClick={() => setCategory(key)}
                    className={cn(
                      "inline-flex h-9 shrink-0 items-center gap-2 border px-3 text-xs transition-colors",
                      category === key
                        ? "border-[#315bd7] bg-[#315bd7] text-white"
                        : "border-white/[0.1] text-white/45 hover:border-white/[0.2] hover:text-white/75",
                    )}
                  >
                    <Icon className="h-3.5 w-3.5" />
                    {item.label}
                  </button>
                );
              })}
            </div>
            <label className="relative block w-full xl:w-72">
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-white/25" />
              <Input
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Найти сервис или функцию"
                className="h-9 border-white/[0.1] bg-white/[0.03] pl-9 text-white placeholder:text-white/25"
              />
            </label>
          </div>

          {catalog.isLoading ? (
            <div className="flex min-h-80 items-center justify-center">
              <Loader2 className="h-6 w-6 animate-spin text-[#7897f4]" />
            </div>
          ) : catalog.isError ? (
            <div className="mt-8 border border-red-400/20 bg-red-400/[0.06] p-5 text-sm text-red-200">
              {errorMessage(catalog.error)}
            </div>
          ) : (
            <div className="mt-6 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              {visibleProviders.map((provider) => (
                <ProviderCard
                  key={provider.key}
                  provider={provider}
                  connection={connections.get(provider.key)}
                  busy={
                    (verify.isPending &&
                      verify.variables === provider.key) ||
                    (disconnect.isPending &&
                      disconnect.variables === provider.key)
                  }
                  onOpen={() => openProvider(provider)}
                  onVerify={() => verify.mutate(provider.key)}
                  onDisconnect={() => {
                    if (
                      window.confirm(
                        `Отключить ${provider.name} от этого приложения?`,
                      )
                    ) {
                      disconnect.mutate(provider.key);
                    }
                  }}
                />
              ))}
            </div>
          )}
        </section>
      </main>

      <Dialog
        open={Boolean(selected)}
        onOpenChange={(open) => {
          if (!open && !connect.isPending) {
            setSelected(null);
            setValues({});
          }
        }}
      >
        <DialogContent className="max-h-[90vh] overflow-y-auto border-white/[0.12] bg-[#171815] sm:max-w-[620px]">
          {selected && (
            <>
              <DialogHeader className="pr-8">
                <div className="mb-3">
                  <ProviderMark provider={selected} />
                </div>
                <DialogTitle className="text-xl text-white">
                  {connections.has(selected.key) ? "Обновить" : "Подключить"}{" "}
                  {selected.name}
                </DialogTitle>
                <DialogDescription className="leading-5 text-white/45">
                  {selected.description} Перед сохранением мы выполним
                  read-only проверку доступа.
                </DialogDescription>
              </DialogHeader>

              {connections.has(selected.key) && (
                <div className="border-l-2 border-[#7897f4] bg-white/[0.025] px-4 py-3 text-xs leading-5 text-white/45">
                  Секреты не показываются повторно. Чтобы изменить подключение,
                  введите новые реквизиты целиком.
                </div>
              )}

              <div className="space-y-5 py-2">
                {selected.fields.map((field) => (
                  <div key={field.key} className="space-y-2">
                    <Label htmlFor={`integration-${field.key}`} className="text-white/75">
                      {field.label}
                    </Label>
                    <Input
                      id={`integration-${field.key}`}
                      type={field.secret ? "password" : "text"}
                      autoComplete="off"
                      value={values[field.key] ?? ""}
                      onChange={(event) =>
                        setValues((current) => ({
                          ...current,
                          [field.key]: event.target.value,
                        }))
                      }
                      placeholder={field.placeholder}
                      className="border-white/[0.12] bg-[#10110f] text-white placeholder:text-white/20"
                    />
                    {field.help && (
                      <p className="text-xs leading-5 text-white/35">
                        {field.help}
                      </p>
                    )}
                  </div>
                ))}
              </div>

              <div className="flex flex-wrap gap-1.5">
                {selected.capabilities.map((capability) => (
                  <span
                    key={capability}
                    className="border border-white/[0.08] px-2 py-1 text-[10px] text-white/40"
                  >
                    {capability}
                  </span>
                ))}
              </div>

              <DialogFooter className="items-center border-t border-white/[0.08] pt-4 sm:justify-between">
                <a
                  href={selected.docs_url}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1.5 text-xs text-white/35 hover:text-white/65"
                >
                  Документация сервиса
                  <ExternalLink className="h-3.5 w-3.5" />
                </a>
                <Button
                  disabled={!canSubmit || connect.isPending}
                  onClick={() =>
                    connect.mutate({
                      provider: selected.key,
                      payload: values,
                    })
                  }
                >
                  {connect.isPending && (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  )}
                  Проверить и подключить
                </Button>
              </DialogFooter>
            </>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
