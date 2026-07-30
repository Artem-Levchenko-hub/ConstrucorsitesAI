"use client";

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { LucideIcon } from "lucide-react";
import {
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
  X,
} from "lucide-react";
import { toast } from "sonner";

import { MaxSectionShell } from "@/components/max/MaxSectionShell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  applyIntegrationPack,
  bindAppIntegration,
  connectAppIntegration,
  disconnectAppIntegration,
  getIntegrationCatalog,
  startIntegrationOAuth,
  verifyAppIntegration,
} from "@/lib/api/app-integrations";
import { ApiError } from "@/lib/api/client";
import { syncMaxManagedKit } from "@/lib/api/max-studio";
import type { AppIntegration, IntegrationCategory, IntegrationProvider } from "@/lib/api/types";
import { cn } from "@/lib/utils";

const categories: Record<IntegrationCategory | "all", { label: string; icon: LucideIcon }> = {
  all: { label: "Все сервисы", icon: Plug },
  payments: { label: "Оплата", icon: CircleDollarSign },
  restaurant: { label: "Рестораны", icon: Store },
  crm: { label: "CRM", icon: UsersRound },
  inventory: { label: "Товары", icon: PackageSearch },
  analytics: { label: "Аналитика", icon: BarChart3 },
  booking: { label: "Запись", icon: CalendarDays },
  delivery: { label: "Доставка", icon: Truck },
};

const providerIcons: Record<string, LucideIcon> = {
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

const message = (error: unknown) => {
  if (error instanceof ApiError) return error.message;
  if (error instanceof TypeError && /fetch/i.test(error.message)) {
    return "Сервис временно недоступен. Проверьте соединение и повторите попытку.";
  }
  return error instanceof Error ? error.message : "Не удалось выполнить действие";
};

export function FigmaIntegrationHub({ projectId, projectName }: { projectId: string; projectName: string }) {
  const qc = useQueryClient();
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
      toast.warning("Подключение сохранено, SDK обновится позже", { description: message(error) });
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
      toast.success("Интеграция подключена");
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
  const oauth = useMutation({
    mutationFn: (provider: string) => startIntegrationOAuth(projectId, provider),
    onSuccess: ({ authorization_url }) => window.location.assign(authorization_url),
    onError: (error) => toast.error("Не удалось начать авторизацию", { description: message(error) }),
  });
  const verify = useMutation({
    mutationFn: (provider: string) => verifyAppIntegration(projectId, provider),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey });
      toast.success("Подключение работает");
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

  const openProvider = (provider: IntegrationProvider) => {
    const connection = connections.get(provider.key);
    setSelected(provider);
    setValues(
      Object.fromEntries(
        provider.fields.map((field) => [field.key, field.secret ? "" : connection?.public_config[field.key] ?? ""]),
      ),
    );
  };

  const connectedCount = (catalog.data?.connections ?? []).filter((item) => item.status === "active" && item.bound_to_project).length;
  const canSubmit = selected?.fields.every((field) => !field.required || Boolean(values[field.key]?.trim())) ?? false;

  return (
    <MaxSectionShell
      projectId={projectId}
      projectName={projectName}
      active="integrations"
      eyebrow="06 / Integrations"
      title="Интеграции"
      lead="Авторизуйте сервис один раз для бизнеса. Секреты хранятся отдельно от исходного кода, а приложение получает только безопасные функции."
    >
      <section className="mt-8 grid gap-4 lg:grid-cols-[1fr_300px]">
        <div className="rounded-[12px] border border-[#d8d4cb] bg-[#fcfbf7] p-6">
          <div className="flex items-start gap-4">
            <span className="grid size-11 shrink-0 place-items-center rounded-[8px] bg-[#f15a38] text-white"><Sparkles className="size-5" /></span>
            <div>
              <p className="omnia-kicker text-[#f15a38]">Рекомендуемый набор</p>
              <h2 className="mt-1 text-xl font-semibold">{catalog.data?.recommended_pack?.title ?? "Базовый контур приложения"}</h2>
              <p className="mt-2 text-sm leading-6 text-[#6d6962]">{catalog.data?.recommended_pack?.description ?? "Оплата, CRM, учёт и аналитика для вашего сценария."}</p>
            </div>
          </div>
          <Button onClick={() => pack.mutate()} disabled={pack.isPending || !catalog.data?.recommended_pack} className="mt-6 bg-[#f15a38] text-white hover:bg-[#d94929]">
            {pack.isPending ? <Loader2 className="size-4 animate-spin" /> : <Sparkles className="size-4" />}
            Подключить рекомендуемые
          </Button>
        </div>
        <div className="rounded-[12px] border border-[#d8d4cb] bg-[#fcfbf7] p-6">
          <p className="omnia-kicker text-[#8d887f]">Состояние</p>
          <p className="mt-3 text-3xl font-semibold">{connectedCount}<span className="text-lg text-[#aaa59b]"> / {catalog.data?.providers.length ?? 0}</span></p>
          <p className="mt-2 text-xs text-[#6d6962]">сервисов активно в этом проекте</p>
          <div className="mt-5 flex items-center gap-2 text-xs text-[#248a4b]"><ShieldCheck className="size-4" /> Секреты зашифрованы</div>
        </div>
      </section>

      <section className="mt-8">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex gap-2 overflow-x-auto pb-1">
            {(Object.keys(categories) as Array<IntegrationCategory | "all">).map((key) => {
              const item = categories[key];
              return (
                <button key={key} onClick={() => setCategory(key)} className={cn("inline-flex h-9 shrink-0 items-center gap-2 rounded-[8px] border px-3 text-xs", category === key ? "border-[#171716] bg-[#171716] text-white" : "border-[#d8d4cb] bg-[#fcfbf7] text-[#6d6962]")}>
                  <item.icon className="size-3.5" />{item.label}
                </button>
              );
            })}
          </div>
          <label className="relative block w-full lg:w-[280px]">
            <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-[#aaa59b]" />
            <Input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Найти сервис" className="h-9 border-[#d8d4cb] bg-[#fcfbf7] pl-9" />
          </label>
        </div>

        <div className="mt-5 overflow-hidden rounded-[12px] border border-[#d8d4cb] bg-[#fcfbf7]">
          <div className="hidden grid-cols-[1.3fr_1fr_150px_130px] border-b border-[#d8d4cb] px-5 py-3 font-mono text-[9px] uppercase tracking-[.1em] text-[#8d887f] md:grid">
            <span>Сервис</span><span>Возможности</span><span>Статус</span><span />
          </div>
          {catalog.isLoading ? (
            <div className="grid min-h-[260px] place-items-center"><Loader2 className="size-5 animate-spin text-[#f15a38]" /></div>
          ) : catalog.isError ? (
            <div className="grid min-h-[260px] place-items-center px-6 py-10 text-center">
              <div className="max-w-[420px]">
                <Plug className="mx-auto size-7 text-[#aaa59b]" />
                <h3 className="mt-4 text-base font-semibold">Не удалось загрузить сервисы</h3>
                <p className="mt-2 text-sm leading-6 text-[#6d6962]">{message(catalog.error)}</p>
                <Button
                  variant="outline"
                  className="mt-5 border-[#d8d4cb] bg-[#fcfbf7]"
                  onClick={() => void catalog.refetch()}
                >
                  <RefreshCw className="size-4" />
                  Повторить
                </Button>
              </div>
            </div>
          ) : (
            <div className="divide-y divide-[#e7e3da]">
              {visible.map((provider) => {
                const connection = connections.get(provider.key);
                const connected = connection?.status === "active" && connection.bound_to_project;
                const reusable = connection?.status === "active" && !connection.bound_to_project;
                const Icon = providerIcons[provider.key] ?? CloudCog;
                return (
                  <article key={provider.key} className="grid gap-4 p-5 md:grid-cols-[1.3fr_1fr_150px_130px] md:items-center">
                    <div className="flex items-center gap-3">
                      <span className="grid size-10 shrink-0 place-items-center rounded-[8px] border border-[#d8d4cb] bg-white text-[#f15a38]"><Icon className="size-4" /></span>
                      <div><h3 className="text-sm font-semibold">{provider.name}</h3><p className="mt-1 line-clamp-1 text-xs text-[#8d887f]">{provider.description}</p></div>
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {provider.capabilities.slice(0, 3).map((item) => <span key={item} className="rounded-full border border-[#d8d4cb] px-2 py-1 text-[9px] text-[#6d6962]">{item}</span>)}
                    </div>
                    <div>
                      {connected ? <span className="inline-flex items-center gap-1.5 text-xs font-medium text-[#248a4b]"><Check className="size-3.5" />Подключено</span>
                        : reusable ? <span className="text-xs text-[#c84528]">Есть у бизнеса</span>
                        : provider.available ? <span className="text-xs text-[#8d887f]">Не подключено</span>
                        : <span className="text-xs text-[#aaa59b]">Готовим</span>}
                    </div>
                    <div className="flex justify-end gap-1">
                      {connected ? (
                        <>
                          <button onClick={() => verify.mutate(provider.key)} className="grid size-8 place-items-center rounded-[8px] text-[#6d6962] hover:bg-[#f5f3ee]" aria-label={`Проверить ${provider.name}`}><RefreshCw className="size-3.5" /></button>
                          <button onClick={() => disconnect.mutate(provider.key)} className="grid size-8 place-items-center rounded-[8px] text-[#8d887f] hover:bg-[#c63d35]/10 hover:text-[#c63d35]" aria-label={`Отключить ${provider.name}`}><Trash2 className="size-3.5" /></button>
                          <Button size="sm" variant="outline" onClick={() => openProvider(provider)}>Настроить</Button>
                        </>
                      ) : reusable ? (
                        <Button size="sm" onClick={() => bind.mutate(provider.key)}>Использовать</Button>
                      ) : provider.available ? (
                        <Button size="sm" onClick={() => openProvider(provider)}>Подключить <ChevronRight className="size-3.5" /></Button>
                      ) : (
                        <a href={provider.docs_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-xs text-[#8d887f]">Требования <ExternalLink className="size-3" /></a>
                      )}
                    </div>
                  </article>
                );
              })}
            </div>
          )}
        </div>
      </section>

      {selected && (
        <div className="fixed inset-0 z-50 grid place-items-center bg-[#171716]/50 p-4 backdrop-blur-[2px]">
          <button className="absolute inset-0" onClick={() => !connect.isPending && setSelected(null)} aria-label="Закрыть" />
          <section className="relative max-h-[90vh] w-full max-w-[600px] overflow-y-auto rounded-[12px] border border-[#d8d4cb] bg-[#fcfbf7] shadow-[0_30px_100px_rgba(0,0,0,.2)]">
            <header className="flex items-start justify-between border-b border-[#d8d4cb] p-6">
              <div>
                <p className="omnia-kicker text-[#f15a38]">Подключение</p>
                <h2 className="mt-2 text-2xl font-semibold">{selected.name}</h2>
                <p className="mt-2 max-w-[470px] text-sm leading-6 text-[#6d6962]">{selected.description}</p>
              </div>
              <button onClick={() => setSelected(null)} className="grid size-9 place-items-center rounded-[8px] text-[#8d887f] hover:bg-[#f5f3ee]"><X className="size-4" /></button>
            </header>
            <div className="space-y-5 p-6">
              {selected.oauth_available && (
                <div className="rounded-[10px] border border-[#f15a38]/30 bg-[#f15a38]/[.06] p-4">
                  <h3 className="text-sm font-semibold">Рекомендуется: вход через {selected.name}</h3>
                  <p className="mt-1 text-xs leading-5 text-[#6d6962]">Откроется официальный кабинет. Пароли и API-ключи вводить в Omnia не потребуется.</p>
                  <Button onClick={() => oauth.mutate(selected.key)} disabled={oauth.isPending} className="mt-4 bg-[#f15a38] text-white hover:bg-[#d94929]">Войти и разрешить доступ <ExternalLink className="size-3.5" /></Button>
                </div>
              )}
              {selected.fields.map((field) => (
                <div key={field.key} className="space-y-2">
                  <Label htmlFor={`integration-${field.key}`}>{field.label}</Label>
                  <Input id={`integration-${field.key}`} type={field.secret ? "password" : "text"} autoComplete="off" value={values[field.key] ?? ""} onChange={(event) => setValues((current) => ({ ...current, [field.key]: event.target.value }))} placeholder={field.placeholder} className="h-11 border-[#d8d4cb] bg-white" />
                  {field.help && <p className="text-xs leading-5 text-[#8d887f]">{field.help}</p>}
                </div>
              ))}
              <div className="rounded-[10px] bg-[#f5f3ee] p-4 text-xs leading-5 text-[#6d6962]"><ShieldCheck className="mb-2 size-4 text-[#248a4b]" />Секреты сохраняются зашифрованно и не показываются повторно.</div>
            </div>
            <footer className="flex items-center justify-between gap-3 border-t border-[#d8d4cb] p-5">
              <a href={selected.docs_url} target="_blank" rel="noreferrer" className="text-xs text-[#8d887f]">Документация сервиса</a>
              {selected.fields.length > 0 && <Button disabled={!canSubmit || connect.isPending} onClick={() => connect.mutate({ provider: selected.key, payload: values })} className="bg-[#f15a38] text-white hover:bg-[#d94929]">{connect.isPending && <Loader2 className="size-4 animate-spin" />}Проверить и подключить</Button>}
            </footer>
          </section>
        </div>
      )}
    </MaxSectionShell>
  );
}
