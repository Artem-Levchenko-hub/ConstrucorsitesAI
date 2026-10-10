"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { Typography, Button, Input, Textarea, Spinner, CellList, CellSimple, IconButton } from "@maxhub/max-ui";
import { FileText, List, RefreshCw, AlertCircle, CheckCircle2, Info } from "lucide-react";
import {
  getYleumAppConfig, getYleumIntegrations, createYleumLead, getYleumLeads,
  getYleumLeadStatus, getYleumLeadReceipt, YleumIntegrationError, type YleumLeadStatus, type YleumLeadReceipt,
} from "@/lib/omnia/integration-client";
import { saveMaxConsent } from "@/lib/omnia/client";
import type { YleumMaxConfig } from "@/lib/omnia/max-config";
import { useMaxApp } from "@/components/MaxAppProvider";

const UUID_KEY = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const DIGEST = /^[0-9a-f]{64}$/;
const LEAD_ID = /^[1-9][0-9]{0,19}$/;
type LeadReceipt = { version: 1; key: string } & (
  { state: "pending"; attempted: boolean; fingerprint: string } |
  { state: "succeeded"; id: string; fingerprint?: string; requestDigest?: string }
);
type LegacyAdoption = { version: 1; key: string; id: string; requestDigest: string };

function readReceipt(storageKey: string): LeadReceipt | null {
  const raw = localStorage.getItem(storageKey) ?? sessionStorage.getItem(storageKey);
  if (raw === null || raw === "null") return null;
  const value: unknown = JSON.parse(raw);
  if (!value || typeof value !== "object") throw new Error("Подтверждение заявки повреждено");
  const record = value as Partial<LeadReceipt>;
  if (record.version !== 1 || typeof record.key !== "string" || !UUID_KEY.test(record.key) ||
      !((record.state === "pending" && typeof record.attempted === "boolean" &&
          typeof record.fingerprint === "string" && DIGEST.test(record.fingerprint)) ||
        (record.state === "succeeded" && typeof record.id === "string" && LEAD_ID.test(record.id) &&
          (record.fingerprint === undefined || (typeof record.fingerprint === "string" && DIGEST.test(record.fingerprint))) &&
          (record.requestDigest === undefined || (typeof record.requestDigest === "string" && DIGEST.test(record.requestDigest))) &&
          ((typeof record.fingerprint === "string" && DIGEST.test(record.fingerprint)) ||
           (typeof record.requestDigest === "string" && DIGEST.test(record.requestDigest)))))) {
    throw new Error("Подтверждение заявки повреждено");
  }
  const valid = record as LeadReceipt;
  return valid.state === "pending"
    ? { version: 1, key: valid.key, state: "pending", attempted: valid.attempted, fingerprint: valid.fingerprint }
    : { version: 1, key: valid.key, state: "succeeded", id: valid.id,
        ...(valid.fingerprint ? { fingerprint: valid.fingerprint } : {}),
        ...(valid.requestDigest ? { requestDigest: valid.requestDigest } : {}) };
}

function samePending(a: LeadReceipt | null, b: LeadReceipt | null): boolean {
  return a?.state === "pending" && b?.state === "pending" && a.key === b.key &&
    a.fingerprint === b.fingerprint && a.attempted === b.attempted;
}

function sameSuccess(a: LeadReceipt | null, b: LeadReceipt | null): boolean {
  return a?.state === "succeeded" && b?.state === "succeeded" &&
    a.key === b.key && a.id === b.id && a.fingerprint === b.fingerprint && a.requestDigest === b.requestDigest;
}

function readLegacyAdoption(storageKey: string): LegacyAdoption | null {
  const raw = localStorage.getItem(`${storageKey}:legacy-adoption`) ?? sessionStorage.getItem(`${storageKey}:legacy-adoption`);
  if (raw === null) return null;
  const value: unknown = JSON.parse(raw);
  if (!value || typeof value !== "object") throw new Error("Подтверждение старой заявки повреждено");
  const record = value as Partial<LegacyAdoption>;
  if (record.version !== 1 || typeof record.key !== "string" || !UUID_KEY.test(record.key) ||
      typeof record.id !== "string" || !LEAD_ID.test(record.id) ||
      typeof record.requestDigest !== "string" || !DIGEST.test(record.requestDigest)) {
    throw new Error("Подтверждение старой заявки повреждено");
  }
  return { version: 1, key: record.key, id: record.id, requestDigest: record.requestDigest } as LegacyAdoption;
}

function checkReceipt(receipt: YleumLeadReceipt, key: string, id?: string, digest?: string) {
  if (receipt.provider !== "amocrm" || receipt.status !== "succeeded" || receipt.idempotency_key !== key ||
      typeof receipt.id !== "string" || !LEAD_ID.test(receipt.id) ||
      typeof receipt.request_digest !== "string" || !DIGEST.test(receipt.request_digest) ||
      (id !== undefined && receipt.id !== id) || (digest !== undefined && receipt.request_digest !== digest)) {
    throw new Error("Не удалось сопоставить сохранённую заявку");
  }
}

async function withReceiptLock(key: string, signal: AbortSignal, action: () => Promise<void>) {
  if (!navigator.locks?.request) throw new Error("Безопасное хранение заявки недоступно. Отправка заблокирована.");
  await navigator.locks.request(key, { mode: "exclusive", signal }, async () => {
    if (signal.aborted) return;
    let stopped!: () => void;
    const cancelled = new Promise<void>(resolve => { stopped = resolve; });
    signal.addEventListener("abort", stopped, { once: true });
    try { await Promise.race([action(), cancelled]); }
    finally { signal.removeEventListener("abort", stopped); }
  });
}

function legacyIdentity(storageKey: string): string | null {
  return sessionStorage.getItem("lead_idempotency_key") ?? readLegacyAdoption(storageKey)?.key ?? null;
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
  const storageLifetime = useRef(new AbortController());
  const inFlight = useRef(false);
  const listSequence = useRef(0);
  const checkedLegacy = useRef<string | null | undefined>(undefined);
  const checkedAdoption = useRef<LegacyAdoption | null>(null);
  const checkedSuccess = useRef<LeadReceipt | null>(null);
  const assertLegacyUnchanged = () => {
    if (checkedLegacy.current === undefined ||
        legacyIdentity(storageKey) !== checkedLegacy.current) {
      throw new Error("Старый ключ заявки изменился. Отправка заблокирована до повторной проверки.");
    }
    const marker = readLegacyAdoption(storageKey);
    const verified = checkedAdoption.current;
    if ((verified && (!marker || marker.key !== verified.key || marker.id !== verified.id ||
          marker.requestDigest !== verified.requestDigest)) || (!verified && marker)) {
      throw new Error("Подтверждение старой заявки изменилось. Отправка заблокирована.");
    }
  };

  useEffect(() => {
    active.current = true;
    storageLifetime.current = new AbortController();
    return () => { active.current = false; storageLifetime.current.abort(); listSequence.current++; };
  }, []);

  useEffect(() => {
    let alive = true;
    setLoadingInit(true); setInitError(null); setRecoveryBlocked(true);
    checkedLegacy.current = undefined; checkedAdoption.current = null; checkedSuccess.current = null;
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
          let saved = readReceipt(storageKey);
          let currentKeyVerified = false;
          const oldKey = legacyIdentity(storageKey);
          const marker = readLegacyAdoption(storageKey);
          if (localStorage.getItem(storageKey) === null && saved) {
            localStorage.setItem(storageKey, JSON.stringify(saved));
          }
          if (localStorage.getItem(`${storageKey}:legacy-adoption`) === null && marker) {
            localStorage.setItem(`${storageKey}:legacy-adoption`, JSON.stringify(marker));
          }
          if (oldKey !== null) {
            if (!UUID_KEY.test(oldKey) || (marker && marker.key !== oldKey)) throw new Error("Старый ключ требует проверки");
            const receipt = await getYleumLeadReceipt(oldKey, marker
              ? { requestDigest: marker.requestDigest, leadId: marker.id } : undefined);
            if (!alive) return;
            if (receipt.provider !== "amocrm" || receipt.status !== "succeeded" ||
                receipt.idempotency_key !== oldKey || typeof receipt.id !== "string" || !LEAD_ID.test(receipt.id) ||
                typeof receipt.request_digest !== "string" || !DIGEST.test(receipt.request_digest) ||
                (marker && (marker.id !== receipt.id || marker.requestDigest !== receipt.request_digest))) {
              throw new Error("Не удалось сопоставить старую заявку");
            }
            if (legacyIdentity(storageKey) !== oldKey) throw new Error("Старый ключ изменился");
            const currentMarker = readLegacyAdoption(storageKey);
            if ((marker && (!currentMarker || currentMarker.key !== marker.key || currentMarker.id !== marker.id ||
                currentMarker.requestDigest !== marker.requestDigest)) || (!marker && currentMarker)) {
              throw new Error("Подтверждение старой заявки изменилось");
            }
            saved = readReceipt(storageKey);
            if (saved?.key === oldKey && saved.state === "succeeded" &&
                (saved.id !== receipt.id || (saved.requestDigest !== undefined && saved.requestDigest !== receipt.request_digest))) {
              throw new Error("Сохранённая операция не совпадает с квитанцией");
            }
            if (!marker) {
              if (saved && (saved.state !== "succeeded" || saved.key !== oldKey || saved.id !== receipt.id ||
                  (saved.requestDigest !== undefined && saved.requestDigest !== receipt.request_digest))) {
                throw new Error("Сохранённая операция требует отдельной проверки");
              }
              // Server digest confirms historical receipt, not a new form payload.
              saved = { version: 1, key: oldKey, state: "succeeded", id: receipt.id,
                requestDigest: receipt.request_digest };
              localStorage.setItem(storageKey, JSON.stringify(saved));
              localStorage.setItem(`${storageKey}:legacy-adoption`, JSON.stringify({
                version: 1, key: oldKey, id: receipt.id, requestDigest: receipt.request_digest,
              }));
            }
            checkedAdoption.current = { version: 1, key: oldKey, id: receipt.id, requestDigest: receipt.request_digest };
          } else if (marker) throw new Error("Старый ключ отсутствует");
          checkedLegacy.current = oldKey;
          if (saved?.state === "pending" && saved.attempted) {
            let recovered: YleumLeadReceipt | null = null;
            try { recovered = await getYleumLeadReceipt(saved.key); } catch { /* Unknown stays pending. */ }
            if (!alive) return;
            assertLegacyUnchanged();
            if (!samePending(readReceipt(storageKey), saved)) throw new Error("Сохранённая операция изменилась");
            if (recovered) {
              checkReceipt(recovered, saved.key);
              currentKeyVerified = true;
              saved = { version: 1, key: saved.key, state: "succeeded", id: recovered.id,
                fingerprint: saved.fingerprint, requestDigest: recovered.request_digest };
              localStorage.setItem(storageKey, JSON.stringify(saved));
            }
          }
          if (saved?.state === "succeeded") {
            if (saved.key !== oldKey && !currentKeyVerified) {
              const exact = await getYleumLeadReceipt(saved.key, {
                leadId: saved.id, ...(saved.requestDigest ? { requestDigest: saved.requestDigest } : {}),
              });
              if (!alive) return;
              assertLegacyUnchanged();
              if (!sameSuccess(readReceipt(storageKey), saved)) throw new Error("Сохранённая операция изменилась");
              checkReceipt(exact, saved.key, saved.id, saved.requestDigest);
              saved = { ...saved, requestDigest: exact.request_digest };
              localStorage.setItem(storageKey, JSON.stringify(saved));
            }
            const confirmed = await getYleumLeadStatus(saved.id);
            if (!alive) return;
            assertLegacyUnchanged();
            if (!sameSuccess(readReceipt(storageKey), saved)) throw new Error("Сохранённая операция изменилась");
            if (confirmed.id !== saved.id || confirmed.provider !== "amocrm") throw new Error("Не удалось подтвердить заявку");
            checkedSuccess.current = saved;
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
    void (preview ? init() : withReceiptLock(storageKey, storageLifetime.current.signal, init)).catch(() => {
      if (alive) {
        setSubmitError("Безопасное хранение заявки недоступно. Отправка заблокирована.");
        setLoadingInit(false);
      }
    });
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
    try {
      await withReceiptLock(storageKey, storageLifetime.current.signal, async () => {
        if (!active.current) return;
        let priorAttempted = false;
        let dispatched = false;
        let confirmedId: string | null = null;
        let dispatchedReceipt: LeadReceipt | null = null;
        try {
          const payload = { name: name.trim(), phone: phone.trim(), email: email.trim() || undefined,
            comment: comment.trim() || undefined, source: "MAX Mini App" };
          const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(JSON.stringify(payload)));
          if (!active.current) return;
          const fingerprint = Array.from(new Uint8Array(bytes), n => n.toString(16).padStart(2, "0")).join("");
          assertLegacyUnchanged();
          const saved = readReceipt(storageKey);
          if (saved?.state === "succeeded") throw new Error("Сначала проверьте подтверждение предыдущей заявки");
          priorAttempted = saved?.state === "pending" && saved.attempted;
          if (priorAttempted && saved!.fingerprint !== fingerprint) {
            throw new Error("Исход предыдущей отправки неизвестен. Изменять данные и создавать новый ключ нельзя.");
          }
          const key = saved?.fingerprint === fingerprint ? saved.key : crypto.randomUUID();
          const pending: LeadReceipt = { version: 1, state: "pending", key, fingerprint, attempted: priorAttempted };
          localStorage.setItem(storageKey, JSON.stringify(pending));
          if (config.legal.personal_data_consent) await saveMaxConsent("personal_data", true, "1");
          if (!active.current) return;
          assertLegacyUnchanged();
          if (!samePending(readReceipt(storageKey), pending)) throw new Error("Сохранённая операция изменилась; отправка заблокирована.");
          dispatchedReceipt = { ...pending, attempted: true };
          localStorage.setItem(storageKey, JSON.stringify(dispatchedReceipt));
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
          assertLegacyUnchanged();
          const successful: LeadReceipt = { version: 1, state: "succeeded", key, fingerprint, id: result.id };
          localStorage.setItem(storageKey, JSON.stringify(successful));
          checkedSuccess.current = successful;
          if (!active.current) return;
          setReceiptId(result.id); setSubmitSuccess(true);
          setReceiptWarning(result.details_status === "unknown"
            ? result.warning || "Передача комментария и источника не подтверждена." : null);
        } catch (error) {
          if (!active.current) return;
          if (error instanceof YleumIntegrationError && error.status === 422 &&
              error.code === "integration_request_rejected" && dispatched && !priorAttempted) {
            try {
              assertLegacyUnchanged();
              if (!samePending(readReceipt(storageKey), dispatchedReceipt)) {
                setRecoveryBlocked(true);
                setSubmitError("Сохранённая операция изменилась. Новый ключ не создан; повторите проверку.");
                return;
              }
              localStorage.setItem(storageKey, "null");
            } catch { setRecoveryBlocked(true); }
            setSubmitError("Заявка отклонена. Проверьте данные перед новой отправкой.");
          } else {
            setSubmitError(confirmedId
              ? `Заявка ${confirmedId} создана, но подтверждение не сохранено. Повторите только те же данные с прежним ключом.`
              : error instanceof Error ? error.message : "Ошибка при отправке заявки");
          }
        }
      });
    } catch {
      if (active.current) {
        setRecoveryBlocked(true);
        setSubmitError("Безопасное хранение заявки недоступно. Отправка заблокирована.");
      }
    } finally {
      inFlight.current = false;
      if (active.current) setSubmitting(false);
    }
  };

  const newSubmission = async () => {
    if (!submitSuccess || !receiptId || inFlight.current || preview) return;
    inFlight.current = true;
    try {
      await withReceiptLock(storageKey, storageLifetime.current.signal, async () => {
        if (!active.current) return;
        assertLegacyUnchanged();
        if (!sameSuccess(readReceipt(storageKey), checkedSuccess.current)) throw new Error("Сохранённая операция изменилась");
        localStorage.setItem(storageKey, "null");
        checkedSuccess.current = null;
        setReceiptId(null); setReceiptWarning(null); setSubmitSuccess(false); setSubmitError(null);
        setName(""); setPhone(""); setEmail(""); setComment(""); setConsent(false);
      });
    } catch { if (active.current) setSubmitError("Не удалось завершить прежнюю заявку. Новый ключ не создан."); }
    finally { inFlight.current = false; }
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
