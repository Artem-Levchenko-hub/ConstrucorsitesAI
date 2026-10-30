"use client";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { CircleCheck, CircleHelp, Clock3, LoaderCircle, RotateCcw, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { createPayment, createSubscriptionCheckout, createTestPayment, getPaymentConfig, listPayments } from "@/lib/api/account";
import { ApiError } from "@/lib/api/client";
import { date, money, paymentState } from "./account-presentation";
import { paymentConfirmationDescription, paymentTitle } from "./payment-presentation";
import { refreshOwnerAllowances } from "@/lib/owner-chat-quota";
import "./account-billing.css";

export type CheckoutSelection = { kind: "topup" | "plan" | "test"; code: string; title: string; price: string; credit: string; allowance?: string; offerId?: string };
type Attempt = { key: string; selection: CheckoutSelection; autoRenew: boolean; paymentId?: string };
function readAttempt(storageKey: string): Attempt | null {
  const value = localStorage.getItem(storageKey);
  if (!value) return null;
  const a = JSON.parse(value) as Attempt | null;
  const selection = a?.selection;
  const validProduct = selection?.kind === "test"
    ? selection.code === "payment_test" && typeof selection.offerId === "string" && Boolean(selection.offerId)
    : selection && ["topup", "plan"].includes(selection.kind) &&
      ["start", "pro", "business"].includes(selection.code) && selection.offerId === undefined;
  if (!a || !a.key || !selection || !validProduct ||
    typeof a.autoRenew !== "boolean" || !Number.isFinite(Number(selection.price)) ||
    !Number.isFinite(Number(selection.credit)) || (selection.allowance !== undefined && (typeof selection.allowance !== "string" || selection.allowance.length > 500))) throw new Error("Не удалось прочитать незавершённый заказ. Проверьте операции перед новой оплатой.");
  return a;
}
export function usePaymentJourney(email: string) {
  const client = useQueryClient();
  const params = useSearchParams();
  const returnedId = params.get("payment");
  const storageKey = `max-checkout:${email}`;
  const [attempt, setAttempt] = useState<Attempt | null>(null);
  const [lastPaymentId, setLastPaymentId] = useState<string | null>(null);
  const [selection, setSelection] = useState<CheckoutSelection | null>(null);
  const [autoRenew, setAutoRenew] = useState(false);
  const [ready, setReady] = useState(false);
  const [storageError, setStorageError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const lock = useRef(false);
  const activePrincipal = useRef<string | null>(email);
  const confirmed = useRef(new Set<string>());
  const config = useQuery({ queryKey: ["payment-config"], queryFn: getPaymentConfig });
  useLayoutEffect(() => {
    activePrincipal.current = email;
    return () => { activePrincipal.current = null; };
  }, [email]);
  useEffect(() => {
    const load = () => {
      try { setAttempt(readAttempt(storageKey)); setStorageError(null); }
      catch (e) { setStorageError(e instanceof Error ? e.message : "Хранилище заказа недоступно"); }
      setReady(true);
    };
    load(); window.addEventListener("storage", load);
    return () => window.removeEventListener("storage", load);
  }, [storageKey]);
  const paymentId = lastPaymentId || returnedId || attempt?.paymentId;
  const payments = useQuery({
    queryKey: ["payments"], queryFn: listPayments, enabled: Boolean(paymentId),
    refetchInterval: query => {
      const payment = query.state.data?.find(p => p.id === paymentId);
      return paymentId && !paymentState(payment?.status ?? "").terminal ? 5000 : false;
    },
  });
  const payment = payments.data?.find(p => p.id === paymentId);
  useEffect(() => {
    if (!payment || payments.isError) return;
    if (payment.status === "succeeded" && !confirmed.current.has(payment.id)) {
      confirmed.current.add(payment.id);
      if (payment.purpose === "payment_test") {
        void client.invalidateQueries({ queryKey: ["billing-purchase-offer", email] });
      } else {
        void client.invalidateQueries({ queryKey: ["wallet"] });
        void client.invalidateQueries({ queryKey: ["billing-subscription"] });
        refreshOwnerAllowances(client);
      }
    }
    if (paymentState(payment.status).terminal && attempt?.paymentId === payment.id) {
      try {
        // Only clear this attempt, never another tab's newer checkout.
        if (readAttempt(storageKey)?.key === attempt.key) localStorage.removeItem(storageKey);
        // Synchronize durable browser retry state with an external server result.
        // eslint-disable-next-line react-hooks/set-state-in-effect
        setAttempt(null);
        setSelection(null);
      } catch { setStorageError("Не удалось обновить сохранённый заказ. Проверьте операции перед новой оплатой."); }
    }
  }, [payment, payments.isError, attempt, storageKey, client, email]);
  function choose(next: CheckoutSelection) {
    if (!ready || storageError || attempt || !config.data?.enabled) return;
    setSelection(next); setAutoRenew(false); setError(null);
  }
  function resume() {
    if (!attempt) return;
    setSelection(attempt.selection); setAutoRenew(attempt.selection.offerId ? false : attempt.autoRenew); setError(null);
  }
  async function pay() {
    if (lock.current || !selection || !config.data?.enabled || storageError) return;
    lock.current = true; setPending(true); setError(null);
    let submitted: Attempt | null = null;
    let retrying = false;
    try {
      // Persist BEFORE contacting the backend. A timeout, navigation or reload
      // must never silently turn a retry into a fresh charge.
      const stored = readAttempt(storageKey);
      retrying = Boolean(stored);
      if (stored && (stored.selection.kind !== selection.kind || stored.selection.code !== selection.code || stored.selection.offerId !== selection.offerId)) {
        setAttempt(stored);
        throw new Error("Есть другой незавершённый заказ. Продолжите его перед новой оплатой.");
      }
      const current = stored ?? { key: crypto.randomUUID(), selection, autoRenew: selection.offerId ? false : autoRenew };
      localStorage.setItem(storageKey, JSON.stringify(current)); setAttempt(current);
      submitted = current;
      const result = current.selection.kind === "topup"
        ? await createPayment(current.selection.code, current.key)
        : current.selection.kind === "test"
          ? await createTestPayment(current.selection.offerId!, current.key)
          : await createSubscriptionCheckout(current.selection.code as "pro" | "business", current.autoRenew, current.key);
      // The server order survives navigation. Only its original mounted owner
      // may update the browser or follow its confirmation link; keep the saved
      // idempotency key so that owner can recover it on their next visit.
      if (activePrincipal.current !== email) return;
      const updated = { ...current, paymentId: result.id };
      setLastPaymentId(result.id);
      localStorage.setItem(storageKey, JSON.stringify(updated)); setAttempt(updated);
      void client.invalidateQueries({ queryKey: ["payments"] });
      if (result.confirmation_url && !paymentState(result.status).terminal) window.location.assign(result.confirmation_url);
      else if (!paymentState(result.status).terminal) setError("Сервер не вернул ссылку для оплаты. Проверяем статус заказа; повторный запрос использует тот же заказ.");
      else setSelection(null);
    } catch (e) {
      if (activePrincipal.current !== email) return;
      // Plan conflicts and the explicit test marker follow the existing-key
      // lookup, so even a retry proves no payment exists for this intent.
      // Consent is checked BEFORE that lookup: a rejected retry could still
      // have an earlier payment, so its durable intent must remain frozen.
      const rejectedBeforeCreation = submitted && !submitted.paymentId &&
        e instanceof ApiError && e.status === 409 && (
          (submitted.selection.kind === "plan" && (
            e.code === "subscription_already_active" ||
            e.code === "subscription_plan_not_purchasable" ||
            (e.code === "subscription_consent_required" && !retrying)
          )) ||
          (submitted.selection.kind === "test" && e.code === "conflict" && e.details?.payment_test_not_created === true)
        );
      if (submitted && rejectedBeforeCreation) {
        try {
          const saved = readAttempt(storageKey);
          if (saved?.key === submitted.key) {
            localStorage.removeItem(storageKey);
            setAttempt(null);
          } else setAttempt(saved);
        } catch {
          setStorageError("Не удалось обновить сохранённый заказ. Проверьте операции перед новой оплатой.");
        }
      }
      setError(e instanceof Error ? e.message : "Не удалось начать оплату. Проверьте статус перед повтором.");
    }
    finally { lock.current = false; if (activePrincipal.current === email) setPending(false); }
  }
  return {
    accountKey: email, config, attempt, selection, setSelection, autoRenew, setAutoRenew, pending, error, storageError,
    paymentId, payment, payments, choose, resume, pay, locked: !ready || Boolean(storageError || attempt)
  };
}
export type PaymentJourney = ReturnType<typeof usePaymentJourney>;
export function PaymentCheckout({ journey: j }: { journey: PaymentJourney }) {
  const state = paymentState(j.payment?.status ?? "");
  const ResultIcon = j.payments.isError ? CircleHelp : j.payments.isPending ? LoaderCircle
    : j.payment?.status === "succeeded" ? CircleCheck : j.payment?.status === "refunded" ? RotateCcw
      : state.terminal ? X : j.payment && ["pending", "waiting_for_capture"].includes(j.payment.status) ? Clock3 : CircleHelp;
  const resultTitle = j.payments.isError ? "Не удалось проверить оплату" : j.payments.isPending ? "Проверяем результат оплаты…"
    : !j.payment ? "Платёж не найден" : state.label;
  const resultDescription = paymentConfirmationDescription(j.payment, j.payments);
  return <>
    {j.storageError && <div role="alert" className="account-notice error">{j.storageError}</div>}
    {j.paymentId && <section className={`account-payment-result account-payment-confirmation ${j.payments.isError ? "error" : state.tone}`} aria-live="polite">
      <div className="account-payment-result-icon"><ResultIcon size={24} aria-hidden="true" /></div>
      <div className="account-payment-result-content">
        <h2>{resultTitle}</h2>
        <p>{resultDescription}</p>
        {j.payment && !j.payments.isError && <div className="account-payment-result-purchase"><span>{paymentTitle(j.payment)}</span><strong>{money(j.payment.amount_rub)}</strong></div>}
        <details className="account-payment-result-details"><summary>Детали платежа</summary><small>Заказ: {j.paymentId}</small>{j.payment && <time dateTime={j.payment.created_at}>{date(j.payment.created_at)}</time>}</details>
      </div>
      <div className="account-actions">
        <Button variant="outline" disabled={j.payments.isFetching} onClick={() => void j.payments.refetch()}>Проверить статус</Button>
        {j.payment?.confirmation_url && !state.terminal && !j.payments.isError && <a className="account-link-button" href={j.payment.confirmation_url}>Продолжить в ЮKassa</a>}
        <Link href="/billing/transactions">Операции →</Link>
      </div>
    </section>}
    {j.attempt && <div className="account-attempt">
      <p>Есть незавершённый заказ: {j.attempt.selection.title}. Сначала проверьте или продолжите его.</p>
      <Button variant="outline" onClick={j.resume}>Продолжить незавершённую оплату</Button>
    </div>}
    <Dialog open={Boolean(j.selection)} onOpenChange={open => { if (!open) j.setSelection(null); }}>
      <DialogContent data-max-studio className="account-dialog">
        <DialogTitle>Проверка заказа</DialogTitle>
        <DialogDescription>Проверьте сумму перед переходом на защищённую страницу ЮKassa.</DialogDescription>
        {j.selection && <>
          <h3>{j.selection.title}</h3>
          <dl className="account-payment-summary">
            <div>
              <dt>К оплате</dt>
              <dd>{money(j.selection.price)}</dd>
            </div>
            <div>
              <dt>Включено</dt>
              <dd>{j.selection.kind === "test" ? "Подписка и пакеты без изменений" : j.selection.allowance ?? `По прежним условиям: ${money(j.selection.credit)}`}</dd>
            </div>
            <div>
              <dt>Период</dt>
              <dd>{j.selection.kind === "test" ? "Разовая проверочная оплата" : j.selection.kind === "topup" ? "Разовое пополнение" : "1 месяц"}</dd>
            </div>
          </dl>
          {j.selection.kind === "test" && <p className="account-notice">Действующая подписка, её срок, автопродление и пакеты не изменятся.</p>}
          {j.selection.kind === "plan" && <label className="account-consent">
            <input type="checkbox" checked={j.autoRenew} disabled={Boolean(j.attempt) || j.pending} onChange={e => j.setAutoRenew(e.target.checked)} />
            <span>Включить автопродление. Разрешаю ежемесячное списание стоимости тарифа с сохранённого способа оплаты. Согласие можно отозвать в разделе «Тариф».</span>
          </label>}
          <p className="account-hint">Нажимая «Перейти к оплате», вы принимаете <Link href="/legal/offer" target="_blank" rel="noopener noreferrer">публичную оферту</Link>. {j.selection.kind === "test" ? "Это разовая проверочная оплата." : j.selection.kind === "topup" ? "Это разовое пополнение, без подписки." : "Тариф активируется после подтверждения оплаты."}</p>
          {j.error && <p role="alert" className="account-notice error">{j.error}</p>}
          {!j.config.data?.enabled && <p className="account-notice pending">{j.config.data?.reason ?? "Платёжный сервис недоступен"}</p>}
          <Button disabled={j.pending || !j.config.data?.enabled || Boolean(j.storageError)} onClick={() => void j.pay()}>Перейти к оплате в ЮKassa{j.pending ? "…" : ""}</Button>
          <p className="account-hint">Возврат из ЮKassa сам по себе не подтверждает оплату. Мы проверим результат на сервере.</p>
        </>}
      </DialogContent>
    </Dialog>
  </>;
}
