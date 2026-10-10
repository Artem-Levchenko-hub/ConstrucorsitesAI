"use client";

import { useState, useEffect, useRef } from "react";
import { Typography, Button, Input, Textarea, Spinner, CellList, CellSimple, IconButton } from "@maxhub/max-ui";
import { FileText, List, RefreshCw, AlertCircle, CheckCircle2, Info } from "lucide-react";
import {
  getYleumAppConfig,
  getYleumIntegrations,
  createYleumLead,
  getYleumLeads,
  YleumIntegrationError,
  YleumLeadStatus
} from "@/lib/omnia/integration-client";
import { saveMaxConsent } from "@/lib/omnia/client";
import type { YleumMaxConfig } from "@/lib/omnia/max-config";

function getOrCreateIdempotencyKey() {
  if (typeof window === "undefined") return "";
  let key = sessionStorage.getItem("lead_idempotency_key");
  if (!key) {
    key = crypto.randomUUID();
    sessionStorage.setItem("lead_idempotency_key", key);
  }
  return key;
}

function resetIdempotencyKey() {
  if (typeof window !== "undefined") {
    sessionStorage.removeItem("lead_idempotency_key");
  }
}

export default function App() {
  const [activeTab, setActiveTab] = useState<"submit" | "list">("submit");
  const [config, setConfig] = useState<YleumMaxConfig | null>(null);
  const [amoConnected, setAmoConnected] = useState<boolean | null>(null);
  const [loadingInit, setLoadingInit] = useState(true);
  const [initError, setInitError] = useState<string | null>(null);

  // Form state
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [email, setEmail] = useState("");
  const [comment, setComment] = useState("");
  const [consent, setConsent] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitSuccess, setSubmitSuccess] = useState(false);

  // Leads state
  const [leads, setLeads] = useState<YleumLeadStatus[]>([]);
  const [loadingLeads, setLoadingLeads] = useState(false);
  const [leadsError, setLeadsError] = useState<string | null>(null);

  const initFetched = useRef(false);

  useEffect(() => {
    if (initFetched.current) return;
    initFetched.current = true;

    async function init() {
      try {
        const [appConfig, integrations] = await Promise.all([
          getYleumAppConfig(),
          getYleumIntegrations()
        ]);
        setConfig(appConfig);
        setAmoConnected(integrations.providers.includes("amocrm"));
      } catch (e) {
        setInitError(e instanceof Error ? e.message : "Ошибка инициализации");
      } finally {
        setLoadingInit(false);
      }
    }
    init();
  }, []);

  const loadLeads = async () => {
    setLoadingLeads(true);
    setLeadsError(null);
    try {
      const res = await getYleumLeads();
      setLeads(res.items);
    } catch (e) {
      setLeadsError(e instanceof Error ? e.message : "Ошибка загрузки заявок");
    } finally {
      setLoadingLeads(false);
    }
  };

  useEffect(() => {
    if (activeTab === "list" && amoConnected) {
      loadLeads();
    }
  }, [activeTab, amoConnected]);

  const handleSubmit = async () => {
    if (!name.trim() || !phone.trim()) {
      setSubmitError("Имя и телефон обязательны");
      return;
    }
    if (config?.legal.personal_data_consent && !consent) {
      setSubmitError("Необходимо согласие с политикой обработки персональных данных");
      return;
    }

    setSubmitting(true);
    setSubmitError(null);

    try {
      if (config?.legal.personal_data_consent) {
        await saveMaxConsent("personal_data", true, "1");
      }

      const key = getOrCreateIdempotencyKey();
      await createYleumLead({
        idempotency_key: key,
        name: name.trim(),
        phone: phone.trim(),
        email: email.trim() || undefined,
        comment: comment.trim() || undefined,
        source: "MAX Mini App"
      });

      setSubmitSuccess(true);
    } catch (error) {
      if (error instanceof YleumIntegrationError) {
        if (error.status === 422 && error.code === "integration_request_rejected") {
          resetIdempotencyKey();
          setSubmitError("Заявка отклонена. Пожалуйста, проверьте данные и попробуйте снова.");
        } else {
          setSubmitError(error.message || "Ошибка при отправке заявки");
        }
      } else {
        setSubmitError("Неизвестная ошибка при отправке");
      }
    } finally {
      setSubmitting(false);
    }
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
      </div>
    );
  }

  if (amoConnected === false) {
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
        {activeTab === "submit" && (
          <div className="max-w-md mx-auto space-y-6">
            {submitSuccess ? (
              <div className="flex flex-col items-center justify-center py-12 text-center">
                <CheckCircle2 className="w-16 h-16 text-green-500 mb-4" />
                <Typography.Title variant="medium-strong" asChild><h2>Заявка отправлена</h2></Typography.Title>
                <Typography.Body className="mt-2 text-[var(--app-muted)]">
                  Ваша заявка успешно передана в amoCRM.
                </Typography.Body>
                <Button
                  className="mt-8"
                  variant="secondary"
                  onClick={() => setActiveTab("list")}
                >
                  Перейти к моим заявкам
                </Button>
              </div>
            ) : (
              <div className="space-y-4">
                <div>
                  <label className="block mb-1 text-sm font-medium">Имя *</label>
                  <Input
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="Иван Иванов"
                    disabled={submitting}
                  />
                </div>
                <div>
                  <label className="block mb-1 text-sm font-medium">Телефон *</label>
                  <Input
                    value={phone}
                    onChange={(e) => setPhone(e.target.value)}
                    placeholder="+7 (999) 000-00-00"
                    type="tel"
                    disabled={submitting}
                  />
                </div>
                <div>
                  <label className="block mb-1 text-sm font-medium">Email</label>
                  <Input
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="ivan@example.com"
                    type="email"
                    disabled={submitting}
                  />
                </div>
                <div>
                  <label className="block mb-1 text-sm font-medium">Комментарий</label>
                  <Textarea
                    value={comment}
                    onChange={(e) => setComment(e.target.value)}
                    placeholder="Дополнительная информация..."
                    disabled={submitting}
                  />
                </div>

                {config?.legal.personal_data_consent && (
                  <div className="flex items-start gap-3 mt-4">
                    <input
                      type="checkbox"
                      id="consent"
                      checked={consent}
                      onChange={(e) => setConsent(e.target.checked)}
                      disabled={submitting}
                      className="mt-1 w-5 h-5 accent-[var(--app-brand)]"
                    />
                    <label htmlFor="consent" className="text-sm text-[var(--app-muted)]">
                      Я согласен с политикой обработки персональных данных
                    </label>
                  </div>
                )}

                {submitError && (
                  <div className="p-3 bg-red-50 text-red-600 rounded-md text-sm">
                    {submitError}
                  </div>
                )}

                <Button
                  className="w-full mt-6"
                  size="large"
                  variant="primary"
                  onClick={handleSubmit}
                  loading={submitting}
                  disabled={submitting}
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
                disabled={loadingLeads}
                aria-label="Обновить"
              >
                <RefreshCw className={`w-5 h-5 ${loadingLeads ? 'animate-spin' : ''}`} />
              </IconButton>
            </div>

            {loadingLeads && leads.length === 0 ? (
              <div className="flex justify-center py-12">
                <Spinner size={24} />
              </div>
            ) : leadsError ? (
              <div className="p-4 bg-red-50 text-red-600 rounded-md text-center">
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
