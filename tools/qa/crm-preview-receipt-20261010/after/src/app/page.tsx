"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { Typography, Button, Input, Textarea, Spinner, CellList, CellSimple, IconButton } from "@maxhub/max-ui";
import { FileText, List, RefreshCw, AlertCircle, CheckCircle2, Info } from "lucide-react";
import {
  getYleumAppConfig, getYleumIntegrations, createYleumLead, getYleumLeads,
  getYleumLeadStatus, YleumIntegrationError, type YleumLeadStatus,
} from "@/lib/omnia/integration-client";
import { saveMaxConsent } from "@/lib/omnia/client";
import type { YleumMaxConfig } from "@/lib/omnia/max-config";
import { useMaxApp } from "@/components/MaxAppProvider";

type LeadReceipt = {
  version: 1; key: string; fingerprint: string;
} & ({ state: "pending"; attempted: boolean } | { state: "succeeded"; id: string });

function readReceipt(storageKey: string): LeadReceipt | null {
  const raw = sessionStorage.getItem(storageKey);
  if (raw === null) {
    // This old unscoped identity cannot safely be attributed or discarded.
    if (sessionStorage.getItem("lead_idempotency_key")) {
      throw new Error("Исход старой заявки не подтверждён. Не отправляйте её повторно до проверки.");
    }
    return null;
  }
  const value: unknown = JSON.parse(raw);
  if (!value || typeof value !== "object") throw new Error("Подтверждение заявки повреждено");
  const record = value as Partial<LeadReceipt>;
  if (record.version !== 1 || typeof record.key !== "string" ||
      !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(record.key) ||
      typeof record.fingerprint !== "string" || !/^[0-9a-f]{64}$/.test(record.fingerprint) ||
      !((record.state === "pending" && typeof record.attempted === "boolean") ||
        (record.state === "succeeded" && typeof record.id === "string" && /^[1-9][0-9]{0,19}$/.test(record.id)))) {
    throw new Error("Подтверждение заявки повреждено");
  }
  return record as LeadReceipt;
}

export default function App() {
  const { mode, user, error } = useMaxApp();
  if (mode === "loading") return <div className="flex justify-center p-8"><Spinner size={48} /></div>;
  const preview = mode === "preview" && user?.id === "preview";
  if (!preview && (mode !== "max" || !user || !/^[1-9][0-9]{0,19}$/.test(user.id))) {
    return <div role="alert" className="p-4">{error || "Откройте приложение внутри MAX"}</div>;
  }
  // Each product has its own origin. A new actor/mode unmounts private state
  // synchronously; old asynchronous replies cannot populate the new instance.
  const scope = `${typeof window === "undefined" ? "" : window.location.origin}:${user!.id}:${mode}`;
  return <CRMApp key={scope} scope={scope} preview={preview} />;
}

function CRMApp({ scope, preview }: { scope: string; preview: boolean }) {
  const storageKey = `yleum:amocrm:lead:v1:${scope}`;
  const [activeTab, setActiveTab] = useState<"submit" | "list">("submit");
  const [config, setConfig] = useState<YleumMaxConfig | null>(null);
  const [amoConnected, setAmoConnected] = useState<boolean | null>(null);
  const [loadingInit, setLoadingInit] = useState(true);
  const [initError, setInitError] = useState<string | null>(null);
  const [initAttempt, setInitAttempt] = useState(0);
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [email, setEmail] = useState("");
  const [comment, setComment] = useState("");
  const [consent, setConsent] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitSuccess, setSubmitSuccess] = useState(false);
  const [receiptId, setReceiptId] = useState<string | null>(null);
  const [receiptWarning, setReceiptWarning] = useState<string | null>(null);
  const [recoveryBlocked, setRecoveryBlocked] = useState(true);
  const [leads, setLeads] = useState<YleumLeadStatus[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [loadingLeads, setLoadingLeads] = useState(false);
  const [leadsError, setLeadsError] = useState<string | null>(null);
  const active = useRef(false);
  const inFlight = useRef(false);
  const listSequence = useRef(0);

  useEffect(() => {
    active.current = true;
    return () => { active.current = false; listSequence.current++; };
  }, []);

  useEffect(() => {
    let alive = true;
    setLoadingInit(true); setInitError(null); setRecoveryBlocked(true);
    async function init() {
      try {
        const appConfig = await getYleumAppConfig();
        if (!alive) return;
        setConfig(appConfig);
        if (preview) return; // No CRM status, history, consent or writes in preview.
        const integrations = await getYleumIntegrations();
        if (!alive) return;
        setAmoConnected(integrations.providers.includes("amocrm"));
        try {
          const saved = readReceipt(storageKey);
          if (saved?.state === "succeeded") {
            const confirmed = await getYleumLeadStatus(saved.id);
            if (!alive) return;
            if (confirmed.id !== saved.id || confirmed.provider !== "amocrm") throw new Error("Не удалось подтвердить заявку");
            setReceiptId(saved.id); setSubmitSuccess(true);
            setReceiptWarning(confirmed.details_status === "unknown"
              ? confirmed.warning || "Передача комментария и источника не подтверждена." : null);
          } else if (saved?.state === "pending" && saved.attempted) {
            setSubmitError("Исход предыдущей отправки неизвестен. Повторите только те же данные: сохранён прежний ключ заявки.");
          }
          setRecoveryBlocked(false);
        } catch {
          if (alive) setSubmitError("Не удалось проверить сохранённое подтверждение. Отправка заблокирована до повторной проверки.");
        }
      } catch (e) {
        if (alive) setInitError(e instanceof Error ? e.message : "Ошибка инициализации");
      } finally {
        if (alive) setLoadingInit(false);
      }
    }
    void init();
    return () => { alive = false; };
  }, [preview, storageKey, initAttempt]);

  const loadLeads = useCallback(async () => {
    if (preview || !amoConnected || !active.current) return;
    const sequence = ++listSequence.current;
    setLoadingLeads(true); setLeadsError(null);
    try {
      const res = await getYleumLeads();
      if (!active.current || sequence !== listSequence.current) return;
      setLeads(res.items); setHasMore(res.has_more);
    } catch (e) {
      if (active.current && sequence === listSequence.current) {
        setLeadsError(e instanceof Error ? e.message : "Ошибка загрузки заявок");
      }
    } finally {
      if (active.current && sequence === listSequence.current) setLoadingLeads(false);
    }
  }, [preview, amoConnected]);

  useEffect(() => {
    if (activeTab !== "list" || preview || !amoConnected) return;
    void loadLeads();
    const refresh = () => { if (document.visibilityState !== "hidden") void loadLeads(); };
    window.addEventListener("focus", refresh);
    const visible = () => { if (document.visibilityState === "visible") void loadLeads(); };
    document.addEventListener("visibilitychange", visible);
    return () => {
      listSequence.current++;
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", visible);
    };
  }, [activeTab, preview, amoConnected, loadLeads]);

  const handleSubmit = async () => {
    if (preview || !active.current || !amoConnected || !config || recoveryBlocked ||
        loadingInit || initError || submitSuccess || inFlight.current) return;
    if (!name.trim() || !phone.trim()) { setSubmitError("Имя и телефон обязательны"); return; }
    if (config.legal.personal_data_consent && !consent) {
      setSubmitError("Необходимо согласие с политикой обработки персональных данных"); return;
    }
    inFlight.current = true; // Set before any await; a second click cannot dispatch.
    setSubmitting(true); setSubmitError(null);
    let priorAttempted = false;
    let dispatched = false;
    let confirmedId: string | null = null;
    try {
      const payload = { name: name.trim(), phone: phone.trim(), email: email.trim() || undefined,
        comment: comment.trim() || undefined, source: "MAX Mini App" };
      const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(JSON.stringify(payload)));
      if (!active.current) return;
      const fingerprint = Array.from(new Uint8Array(bytes), n => n.toString(16).padStart(2, "0")).join("");
      const saved = readReceipt(storageKey);
      if (saved?.state === "succeeded") throw new Error("Сначала проверьте подтверждение предыдущей заявки");
      priorAttempted = saved?.state === "pending" && saved.attempted;
      if (priorAttempted && saved!.fingerprint !== fingerprint) {
        throw new Error("Исход предыдущей отправки неизвестен. Изменять данные и создавать новый ключ нельзя.");
      }
      const key = saved?.fingerprint === fingerprint ? saved.key : crypto.randomUUID();
      const pending: LeadReceipt = { version: 1, state: "pending", key, fingerprint, attempted: priorAttempted };
      sessionStorage.setItem(storageKey, JSON.stringify(pending));
      if (config.legal.personal_data_consent) await saveMaxConsent("personal_data", true, "1");
      if (!active.current) return;
      sessionStorage.setItem(storageKey, JSON.stringify({ ...pending, attempted: true }));
      dispatched = true;
      const result = await createYleumLead({ idempotency_key: key, ...payload });
      if (!active.current) return;
      if (result.provider !== "amocrm" || typeof result.id !== "string" || !/^[1-9][0-9]{0,19}$/.test(result.id)) {
        throw new Error("Не удалось подтвердить ID заявки; сохранён прежний ключ");
      }
      confirmedId = result.id;
      const current = readReceipt(storageKey);
      if (current?.state !== "pending" || current.key !== key || current.fingerprint !== fingerprint) {
        throw new Error("Сохранённая операция изменилась. Новый ключ не создан; проверьте предыдущую заявку.");
      }
      // Store only receipt identifiers/digest, never name/phone/email/comment.
      sessionStorage.setItem(storageKey, JSON.stringify({ version: 1, state: "succeeded", key, fingerprint, id: result.id }));
      if (!active.current) return;
      setReceiptId(result.id); setSubmitSuccess(true);
      setReceiptWarning(result.details_status === "unknown"
        ? result.warning || "Передача комментария и источника не подтверждена." : null);
    } catch (error) {
      if (!active.current) return;
      if (error instanceof YleumIntegrationError && error.status === 422 &&
          error.code === "integration_request_rejected" && dispatched && !priorAttempted) {
        try { sessionStorage.removeItem(storageKey); }
        catch { setRecoveryBlocked(true); }
        setSubmitError("Заявка отклонена. Проверьте данные перед новой отправкой.");
      } else {
        setSubmitError(confirmedId
          ? `Заявка ${confirmedId} создана, но подтверждение не сохранено. Повторите только те же данные с прежним ключом.`
          : error instanceof Error ? error.message : "Ошибка при отправке заявки");
      }
    } finally {
      inFlight.current = false;
      if (active.current) setSubmitting(false);
    }
  };

  const newSubmission = () => {
    if (!submitSuccess || !receiptId || inFlight.current || preview) return;
    try {
      sessionStorage.removeItem(storageKey);
      setReceiptId(null); setReceiptWarning(null); setSubmitSuccess(false); setSubmitError(null);
      setName(""); setPhone(""); setEmail(""); setComment(""); setConsent(false);
    } catch { setSubmitError("Не удалось завершить прежнюю заявку. Новый ключ не создан."); }
  };

  if (loadingInit) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <Spinner size={48} />
      </div>
    );
  }

  if (initError) {
    return (
      <div className="flex flex-col items-center justify-center min-h-screen p-4 text-center">
        <AlertCircle className="w-12 h-12 text-red-500 mb-4" />
        <Typography.Title variant="medium-strong" asChild><h2>Ошибка загрузки</h2></Typography.Title>
        <Typography.Body className="mt-2 text-[var(--app-muted)]">{initError}</Typography.Body>
        <Button className="mt-4" variant="secondary" onClick={() => setInitAttempt(n => n + 1)}>Повторить</Button>
      </div>
    );
  }

  if (!preview && amoConnected === false) {
    return (
      <div className="flex flex-col items-center justify-center min-h-screen p-4 text-center">
        <Info className="w-12 h-12 text-[var(--app-brand)] mb-4" />
        <Typography.Title variant="medium-strong" asChild><h2>amoCRM не подключена</h2></Typography.Title>
        <Typography.Body className="mt-2 text-[var(--app-muted)]">
          Для работы приложения необходимо подключить интеграцию с amoCRM в настройках Yleum.
        </Typography.Body>
      </div>
    );
  }

  return (
    <div className="flex flex-col min-h-screen pb-20">
      <header className="p-4 border-b border-[var(--app-border)] bg-[var(--app-surface)] sticky top-0 z-10">
        <Typography.Title variant="medium-strong" asChild>
          <h1>{activeTab === "submit" ? "Оставить заявку" : "Мои заявки"}</h1>
        </Typography.Title>
      </header>

      <main className="flex-1 p-4">
        {preview && <div role="status" className="mb-4 p-3 border rounded-md">
          Режим просмотра: форма доступна только для ознакомления. Заявки и CRM-история доступны внутри MAX.
        </div>}
        {activeTab === "submit" && (
          <div className="max-w-md mx-auto space-y-6">
            {submitSuccess ? (
              <div className="flex flex-col items-center justify-center py-12 text-center">
                <CheckCircle2 className="w-16 h-16 text-green-500 mb-4" />
                <Typography.Title variant="medium-strong" asChild><h2>Заявка отправлена</h2></Typography.Title>
                <Typography.Body className="mt-2 text-[var(--app-muted)]">
                  Заявка создана в amoCRM. ID: {receiptId}
                </Typography.Body>
                {receiptWarning && <div role="status" className="mt-3">{receiptWarning}</div>}
                <Button
                  className="mt-8"
                  variant="secondary"
                  onClick={() => setActiveTab("list")}
                >
                  Перейти к моим заявкам
                </Button>
                <Button className="mt-3" variant="secondary" onClick={newSubmission}>Новая заявка</Button>
                {submitError && <div role="alert" className="mt-3 text-red-600">{submitError}</div>}
              </div>
            ) : (
              <div className="space-y-4">
                <div>
                  <label className="block mb-1 text-sm font-medium">Имя *</label>
                  <Input
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="Иван Иванов"
                    disabled={submitting || preview || recoveryBlocked}
                  />
                </div>
                <div>
                  <label className="block mb-1 text-sm font-medium">Телефон *</label>
                  <Input
                    value={phone}
                    onChange={(e) => setPhone(e.target.value)}
                    placeholder="+7 (999) 000-00-00"
                    type="tel"
                    disabled={submitting || preview || recoveryBlocked}
                  />
                </div>
                <div>
                  <label className="block mb-1 text-sm font-medium">Email</label>
                  <Input
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="ivan@example.com"
                    type="email"
                    disabled={submitting || preview || recoveryBlocked}
                  />
                </div>
                <div>
                  <label className="block mb-1 text-sm font-medium">Комментарий</label>
                  <Textarea
                    value={comment}
                    onChange={(e) => setComment(e.target.value)}
                    placeholder="Дополнительная информация..."
                    disabled={submitting || preview || recoveryBlocked}
                  />
                </div>

                {config?.legal.personal_data_consent && (
                  <div className="flex items-start gap-3 mt-4">
                    <input
                      type="checkbox"
                      id="consent"
                      checked={consent}
                      onChange={(e) => setConsent(e.target.checked)}
                      disabled={submitting || preview || recoveryBlocked}
                      className="mt-1 w-5 h-5 accent-[var(--app-brand)]"
                    />
                    <label htmlFor="consent" className="text-sm text-[var(--app-muted)]">
                      Я согласен с политикой обработки персональных данных
                    </label>
                  </div>
                )}

                {submitError && (
                  <div role="alert" className="p-3 bg-red-50 text-red-600 rounded-md text-sm">
                    {submitError}
                  </div>
                )}

                {recoveryBlocked && !preview && <Button variant="secondary" onClick={() => setInitAttempt(n => n + 1)}>
                  Повторить проверку
                </Button>}
                <Button
                  className="w-full mt-6"
                  size="large"
                  variant="primary"
                  onClick={handleSubmit}
                  loading={submitting}
                  disabled={submitting || preview || recoveryBlocked}
                >
                  Отправить заявку
                </Button>
              </div>
            )}
          </div>
        )}

        {activeTab === "list" && (
          <div className="max-w-md mx-auto">
            <div className="flex justify-between items-center mb-4">
              <Typography.Body className="text-[var(--app-muted)]">
                История ваших заявок
              </Typography.Body>
              <IconButton
                variant="ghost"
                onClick={loadLeads}
                disabled={loadingLeads || preview}
                aria-label="Обновить"
              >
                <RefreshCw className={`w-5 h-5 ${loadingLeads ? 'animate-spin' : ''}`} />
              </IconButton>
            </div>

            {hasMore && !preview && <p className="mb-3 text-sm">Показаны последние 20 заявок.</p>}
            {preview ? <div role="status">История доступна только внутри MAX. CRM-данные в превью не загружаются.</div> : loadingLeads && leads.length === 0 ? (
              <div className="flex justify-center py-12">
                <Spinner size={24} />
              </div>
            ) : leadsError ? (
              <div role="alert" className="p-4 bg-red-50 text-red-600 rounded-md text-center">
                {leadsError}
                <Button variant="secondary" size="small" className="mt-3" onClick={loadLeads}>
                  Повторить
                </Button>
              </div>
            ) : leads.length === 0 ? (
              <div className="text-center py-12 text-[var(--app-muted)]">
                <List className="w-12 h-12 mx-auto mb-3 opacity-20" />
                <p>У вас пока нет заявок</p>
              </div>
            ) : (
              <CellList mode="island">
                {leads.map((lead) => (
                  <CellSimple
                    key={lead.id}
                    title={lead.name || "Без имени"}
                    subtitle={`Этап: ${lead.status_name}`}
                    overline={new Date(lead.updated_at * 1000).toLocaleDateString("ru-RU")}
                    after={
                      <div className="text-xs text-right text-[var(--app-muted)]">
                        <div>Проверено:</div>
                        <div>{new Date(lead.checked_at).toLocaleTimeString("ru-RU", {hour: '2-digit', minute:'2-digit'})}</div>
                        {lead.details_status === "unknown" && <div role="status">{lead.warning || "Комментарий и источник не подтверждены."}</div>}
                      </div>
                    }
                  />
                ))}
              </CellList>
            )}
          </div>
        )}
      </main>

      <nav className="fixed bottom-0 left-0 right-0 bg-[var(--app-surface)] border-t border-[var(--app-border)] pb-safe">
        <div className="flex justify-around p-2 max-w-md mx-auto">
          <button
            className={`flex flex-col items-center p-2 min-w-[64px] rounded-lg transition-colors ${activeTab === "submit" ? "text-[var(--app-brand)]" : "text-[var(--app-muted)]"}`}
            onClick={() => setActiveTab("submit")}
          >
            <FileText className="w-6 h-6 mb-1" />
            <span className="text-[10px] font-medium">Заявка</span>
          </button>
          <button
            className={`flex flex-col items-center p-2 min-w-[64px] rounded-lg transition-colors ${activeTab === "list" ? "text-[var(--app-brand)]" : "text-[var(--app-muted)]"}`}
            onClick={() => setActiveTab("list")}
          >
            <List className="w-6 h-6 mb-1" />
            <span className="text-[10px] font-medium">Мои заявки</span>
          </button>
        </div>
      </nav>
    </div>
  );
}
