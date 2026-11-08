"use client";

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight, Bot, Check, ChevronDown, Copy, ExternalLink, KeyRound, Loader2, Rocket, Smartphone } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  activateMaxIntegration, connectMaxIntegration, disconnectMaxIntegration, getMaxIntegration, verifyMaxIntegration,
} from "@/lib/api/max-integration";
import { getMaxProjectConfig, saveMaxUrlAttached } from "@/lib/api/max-studio";
import { getLastDeploy } from "@/lib/api/runtime";
import type { DeployStatus, MaxIntegration } from "@/lib/api/types";
import { canActivateMaxWebhook } from "@/lib/max-integration-flow";
import { copyMaxLaunchUrl } from "@/lib/max-launch-steps";
import { ApiError } from "@/lib/api/client";

const steps = ["Бот", "Подключение", "Публикация", "Адрес в MAX"];

type AddressBinding = { url: string; botId: string };
type AddressState = AddressBinding & { proof: "historical" | "pending" | "confirmed" };

function matchesBinding(saved: AddressBinding | null, current: AddressBinding | null) {
  return Boolean(saved && current && saved.url === current.url && saved.botId === current.botId);
}

function httpsAddress(value: string | null | undefined) {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === "https:" && !url.username && !url.password ? value : null;
  } catch {
    return null;
  }
}

export function MaxConnectionWizard({ projectId, onNavigate, onBusyChange }: {
  projectId: string;
  onNavigate: (panel: "publish") => void;
  onBusyChange?: (busy: boolean) => void;
}) {
  const qc = useQueryClient();
  const [botPrepared, setBotPrepared] = useState(false);
  const [token, setToken] = useState("");
  const [addressState, setAddressState] = useState<AddressState | null>(null);
  const addressSummary = useRef<HTMLElement>(null);
  const [copyNotice, setCopyNotice] = useState("");
  const [disconnectRequested, setDisconnectRequested] = useState(false);
  const [maintenanceNotice, setMaintenanceNotice] = useState("");
  const integrationKey = ["max-integration", projectId];
  const configKey = ["max-config", projectId];
  const integration = useQuery({ queryKey: integrationKey, queryFn: () => getMaxIntegration(projectId), retry: false, refetchOnMount: "always" });
  const config = useQuery({ queryKey: configKey, queryFn: () => getMaxProjectConfig(projectId), retry: false, refetchOnMount: "always" });
  const deploy = useQuery({
    queryKey: ["deploy", projectId], queryFn: () => getLastDeploy(projectId), retry: false,
    refetchInterval: query => ["queued", "building", "pushing", "swapping", "cancelling"].includes(query.state.data?.phase ?? "") && query.state.data?.run_id ? 1_500 : false,
  });
  const dataReady = integration.isSuccess && integration.isFetchedAfterMount && config.isSuccess && config.isFetchedAfterMount && deploy.isSuccess;
  const data = integration.data;
  const connected = dataReady && data?.eligible && data.connected && (data.status === "verified" || data.status === "active");
  const productionUrl = dataReady ? httpsAddress(deploy.data.prod_url ?? data?.app_url) : null;
  const step = connected ? productionUrl ? 4 : 3 : botPrepared || data?.connected ? 2 : 1;
  const binding = productionUrl && data?.bot_id ? { url: productionUrl, botId: data.bot_id } : null;
  const currentBinding = matchesBinding(addressState, binding);
  if (binding && !currentBinding) {
    setAddressState({ ...binding, proof: addressState ? "pending" : "historical" });
  }
  const confirmed = currentBinding && addressState?.proof === "confirmed" && config.data?.config.max_url_attached;

  function refreshIntegration() {
    void qc.invalidateQueries({ queryKey: integrationKey });
  }
  function refreshBinding() {
    refreshIntegration();
    void qc.invalidateQueries({ queryKey: configKey });
    void qc.invalidateQueries({ queryKey: ["max-readiness", projectId] });
  }
  function saveIntegration(result: MaxIntegration) {
    if (data?.bot_id !== result.bot_id) void qc.invalidateQueries({ queryKey: configKey });
    qc.setQueryData(integrationKey, result);
    void qc.invalidateQueries({ queryKey: ["max-readiness", projectId] });
  }
  const connect = useMutation({
    mutationFn: () => connectMaxIntegration(projectId, token.trim()),
    onSuccess: result => { saveIntegration(result); setToken(""); setMaintenanceNotice(""); },
    onError: refreshBinding,
  });
  const verify = useMutation({
    mutationFn: () => verifyMaxIntegration(projectId),
    onSuccess: result => { saveIntegration(result); setMaintenanceNotice("Данные подключения обновлены"); },
    onError: refreshIntegration,
  });
  const activate = useMutation({
    mutationFn: () => activateMaxIntegration(projectId),
    onSuccess: result => {
      saveIntegration(result);
      void qc.invalidateQueries({ queryKey: ["deploy", projectId] });
      setMaintenanceNotice("Связь с MAX обновлена");
    },
    onError: refreshIntegration,
  });
  const disconnect = useMutation({
    mutationFn: () => disconnectMaxIntegration(projectId),
    onSuccess: () => {
      qc.setQueryData<MaxIntegration>(integrationKey, previous => previous && ({
        ...previous, connected: false, status: "disconnected", bot_id: null, bot_name: null, bot_username: null,
        app_url: null, webhook_url: null, deep_link: null, last_error: null, verified_at: null, published_at: null,
      }));
      refreshBinding();
      setToken(""); setBotPrepared(false);
      setAddressState(current => current && { ...current, proof: "pending" });
      setDisconnectRequested(false); setMaintenanceNotice("");
    },
  });
  const confirmUrl = useMutation({
    mutationFn: async ({ url, botId }: AddressBinding) => {
      await fetch(url, { method: "GET", mode: "no-cors", cache: "no-store" });
      const latestDeploy = qc.getQueryState<DeployStatus>(["deploy", projectId]);
      const latestIntegration = qc.getQueryState<MaxIntegration>(integrationKey);
      const currentUrl = httpsAddress(latestDeploy?.data?.prod_url ?? latestIntegration?.data?.app_url);
      if (latestDeploy?.status !== "success" || latestIntegration?.status !== "success" || currentUrl !== url || latestIntegration.data?.bot_id !== botId) {
        throw new Error("Адрес или бот изменился. Проверьте подключение и подтвердите адрес заново.");
      }
      return saveMaxUrlAttached(projectId, true, botId);
    },
    onSuccess: (result, savedBinding) => {
      qc.setQueryData(configKey, result);
      void qc.invalidateQueries({ queryKey: ["max-readiness", projectId] });
      if (result.config.max_url_attached) {
        setAddressState(current => matchesBinding(current, savedBinding)
          ? { ...savedBinding, proof: "confirmed" }
          : current);
      }
    },
    onError: error => {
      if (error instanceof ApiError && error.details?.reason === "max_bot_binding_changed") {
        refreshBinding();
      }
    },
  });
  const busy = connect.isPending || verify.isPending || activate.isPending || disconnect.isPending || confirmUrl.isPending;
  useEffect(() => { onBusyChange?.(busy); }, [busy, onBusyChange]);
  useEffect(() => () => { onBusyChange?.(false); }, [onBusyChange]);
  useEffect(() => {
    const summary = addressSummary.current;
    if (confirmed && summary && !summary.closest("details")?.open) summary.focus();
  }, [confirmed]);
  const activationReady = connected && Boolean(httpsAddress(data?.app_url)) && canActivateMaxWebhook(data);

  function resetMaintenance() {
    setMaintenanceNotice(""); verify.reset(); activate.reset(); disconnect.reset(); connect.reset();
  }
  const tokenField = (
    <div className="max-connect-token">
      <Label htmlFor="max-connect-bot-token">Токен бота</Label>
      <Input id="max-connect-bot-token" type="password" autoComplete="off" value={token} disabled={busy}
        onChange={event => { if (busy) return; setToken(event.target.value); connect.reset(); }} placeholder="Вставьте токен из кабинета MAX" />
      <p className="max-connect-hint">Токен — секретный ключ бота. Он хранится зашифрованно и не показывается повторно.</p>
      <p className="max-connect-hint">Можно подключить одного бота к нескольким приложениям. Данные и подключения каждого приложения сохраняются отдельно.</p>
      <p className="max-connect-hint">У этих приложений будет общий чат с ботом. Чтобы у каждого был отдельный чат, используйте разных ботов.</p>
      {connect.isError && <p role="alert" className="max-connect-error">{
        connect.error instanceof ApiError && connect.error.code === "max_bot_binding_busy"
          ? "Подключение MAX для этого приложения уже изменяется. Повторите попытку позже."
          : "Не удалось подключить бота. Проверьте токен и повторите попытку."
      }</p>}
    </div>
  );
  const cabinetLink = <a className="max-connect-cabinet" href="https://business.max.ru/" target="_blank" rel="noreferrer">Открыть кабинет MAX <ExternalLink aria-hidden="true" /></a>;

  if (integration.isError || config.isError || deploy.isError) {
    return <div className="max-connect-state" role="alert"><h2>Не удалось загрузить подключение</h2><p>Повторите проверку, чтобы получить актуальные данные.</p><Button onClick={() => { void integration.refetch(); void config.refetch(); void deploy.refetch(); }}>Повторить</Button></div>;
  }
  if (!dataReady || !data) return <div className="max-connect-state" role="status"><Loader2 className="animate-spin" aria-hidden="true" />Загружаем настройки MAX…</div>;
  if (!data.eligible) return <div className="max-connect-state"><h2>Подключение MAX недоступно для этого проекта</h2><p>Откройте проект мини-приложения MAX.</p></div>;

  const configured = step === 4 && config.data.config.max_url_attached && currentBinding && addressState?.proof !== "pending";
  const addressForm = productionUrl && <>
    {configured && !confirmed && <p className="max-connect-hint">Адрес подтверждали ранее. Если он изменился, сохраните новый адрес в MAX и подтвердите здесь.</p>}
    <div className="max-connect-address"><code>{productionUrl}</code><Button variant="outline" onClick={async () => {
      const copied = await copyMaxLaunchUrl(productionUrl);
      setCopyNotice(copied ? "Адрес скопирован" : "Не удалось скопировать. Выделите адрес и скопируйте его вручную.");
    }}><Copy aria-hidden="true" />Скопировать адрес</Button></div>
    {copyNotice && <p className="max-connect-hint" role="status">{copyNotice}</p>}
    <p className="max-connect-instructions">Сохраните адрес в настройках нужного бота.</p>
    <p className="max-connect-path">Чат-боты → ⋮ у нужного бота → Настройки → Мини-приложение → Введите ссылку → Сохранить</p>
    {cabinetLink}
    {confirmUrl.isError && <p role="alert" className="max-connect-error">Не удалось сохранить подтверждение. Проверьте адрес и соединение, затем повторите попытку.</p>}
    <div className="max-connect-primary">
      <Button disabled={busy || !binding} onClick={() => { if (connected && binding) confirmUrl.mutate(binding); }}>
        {confirmUrl.isPending && <Loader2 className="animate-spin" aria-hidden="true" />}Я сохранил адрес в MAX
      </Button>
    </div>
  </>;
  const botSettings = data.connected && <details className="max-connect-advanced max-connect-disclosure">
    <summary>
      <span className="max-connect-bot-name">{data.bot_name || data.bot_username || "Бот MAX"}</span>
      <span className="max-connect-bot-status" data-tone={connected ? "success" : "warning"}>
        <span className="max-connect-bot-dot" aria-hidden="true" />
        {connected ? "Бот подключён" : "Нужна проверка"}
      </span>
      <ChevronDown className="max-connect-chevron" aria-hidden="true" />
    </summary>
    <div className="max-connect-advanced-body">
      <div className="max-connect-actions">
        <Button variant="outline" aria-disabled={busy || undefined} onClick={() => {
          if (busy) return;
          resetMaintenance(); verify.mutate();
        }}>Проверить подключение</Button>
        <Button variant="outline" disabled={busy || !activationReady} onClick={() => { resetMaintenance(); activate.mutate(); }}>Обновить связь</Button>
      </div>
      {!activationReady && <p className="max-connect-hint">Для обновления связи сначала нужен опубликованный HTTPS-адрес и действующее подключение.</p>}
      {(verify.isError || activate.isError || disconnect.isError) && <p role="alert" className="max-connect-error">Не удалось выполнить действие. Повторите попытку.</p>}
      {maintenanceNotice && <p role="status" className="max-connect-hint">{maintenanceNotice}</p>}
      {step !== 2 && <details className="max-connect-technical"><summary>Заменить токен бота</summary>{tokenField}<Button variant="outline" disabled={busy || token.trim().length < 10} onClick={() => { resetMaintenance(); connect.mutate(); }}>Заменить токен</Button></details>}
      {disconnectRequested ? <div className="max-connect-disconnect"><p>Отключить бота? Безопасный вход пользователей MAX перестанет работать до повторного подключения.</p><div className="max-connect-actions"><Button variant="destructive" disabled={busy} onClick={() => disconnect.mutate()}>Да, отключить</Button><Button variant="outline" disabled={busy} onClick={() => setDisconnectRequested(false)}>Отмена</Button></div></div>
        : <Button variant="ghost" className="max-connect-disconnect-button" disabled={busy} onClick={() => { resetMaintenance(); setDisconnectRequested(true); }}>Отключить MAX</Button>}
    </div>
  </details>;

  return (
    <div className="max-connect-wizard" data-configured={configured || undefined}>
      {botSettings}
      {!configured && <ol className="max-connect-progress" aria-label="Шаги подключения MAX">
        {steps.map((label, index) => <li key={label} aria-current={step === index + 1 ? "step" : undefined} data-complete={step > index + 1}>
          <span>{step > index + 1 ? <Check aria-hidden="true" /> : index + 1}</span><small>{label}</small>
        </li>)}
      </ol>}
      {configured ? <div className="max-connect-step">
        <details className="max-connect-address-settings max-connect-disclosure">
          <summary ref={addressSummary}><span>Адрес приложения в MAX</span><ChevronDown className="max-connect-chevron" aria-hidden="true" /></summary>
          {addressForm}
        </details>
        {confirmed && <p role="status" className="max-connect-success">Подтверждение сохранено</p>}
      </div> : <section className="max-connect-step" aria-labelledby="max-connect-step-title">
        {step === 1 && <>
          <h2 id="max-connect-step-title">Создайте бота в MAX</h2>
          <p className="max-connect-lead">Бот — страница вашего бизнеса в MAX. Через неё пользователи смогут открыть приложение.</p>
          {cabinetLink}
          <figure className="max-connect-illustration" aria-label="Схема: кабинет MAX, бот и приложение">
            <div className="max-connect-diagram"><span><Bot aria-hidden="true" /><small>Ваш бот</small></span><ArrowRight aria-hidden="true" /><span><Smartphone aria-hidden="true" /><small>Приложение</small></span></div>
            <figcaption>Бот открывает ваше приложение внутри MAX</figcaption>
          </figure>
          <p className="max-connect-instructions">В кабинете MAX создайте чат-бота и дождитесь его модерации. Если бот уже готов, переходите дальше.</p>
          <div className="max-connect-primary"><Button onClick={() => setBotPrepared(true)}>Бот готов — далее <ArrowRight aria-hidden="true" /></Button></div>
        </>}
        {step === 2 && <>
          <h2 id="max-connect-step-title">Подключите бота</h2>
          <p className="max-connect-lead">Скопируйте токен вашего бота в кабинете MAX и вставьте сюда. Так Yleum сможет безопасно узнавать пользователей приложения.</p>
          {data.status === "error" && <p role="alert" className="max-connect-error">{data.last_error || "Подключение требует проверки. Обновите токен или проверьте бота."}</p>}
          {tokenField}
          {cabinetLink}
          <div className="max-connect-primary" role="group" aria-label="Действия шага">
            {!data.connected && <Button variant="outline" className="max-connect-back" aria-label="Назад к созданию бота" disabled={busy} onClick={() => { setBotPrepared(false); setToken(""); connect.reset(); }}><ArrowLeft aria-hidden="true" />Назад</Button>}
            <Button disabled={busy || token.trim().length < 10} onClick={() => connect.mutate()}>{connect.isPending ? <Loader2 className="animate-spin" aria-hidden="true" /> : <KeyRound aria-hidden="true" />}Подключить бота</Button>
          </div>
        </>}
        {step === 3 && <>
          <h2 id="max-connect-step-title">Опубликуйте приложение</h2>
          <p className="max-connect-lead">Бот подключён. Теперь получите постоянный HTTPS-адрес приложения.</p>
          <p className="max-connect-instructions">На шаге публикации Yleum покажет, что ещё нужно заполнить, и подготовит приложение к запуску. После публикации вернитесь сюда, чтобы добавить адрес в MAX.</p>
          <div className="max-connect-primary"><Button disabled={busy} onClick={() => onNavigate("publish")}><Rocket aria-hidden="true" />Перейти к публикации</Button></div>
        </>}
        {step === 4 && productionUrl && <>
          <h2 id="max-connect-step-title">Добавьте адрес в MAX</h2>
          <p className="max-connect-lead">{addressState?.proof === "pending"
            ? "Проверьте адрес в настройках бота и подтвердите его заново."
            : "Осталось указать в настройках бота, какое приложение открывать."}</p>
          {addressForm}
        </>}
      </section>}
    </div>
  );
}
