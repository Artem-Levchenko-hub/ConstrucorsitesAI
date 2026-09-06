"use client";
import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { createPayment, createSubscriptionCheckout, getPaymentConfig, listPayments } from "@/lib/api/account";
import { money, paymentState } from "./account-presentation";

export type CheckoutSelection = { kind: "topup" | "plan"; code: string; title: string; price: string; credit: string };
type Attempt = { key: string; selection: CheckoutSelection; autoRenew: boolean; paymentId?: string };
function readAttempt(storageKey: string): Attempt | null {
  const value = localStorage.getItem(storageKey);
  if (!value) return null;
  const a = JSON.parse(value) as Attempt;
  if (!a.key || !a.selection || !["topup", "plan"].includes(a.selection.kind) ||
    !["start", "pro", "business"].includes(a.selection.code) ||
    typeof a.autoRenew !== "boolean" || !Number.isFinite(Number(a.selection.price)) ||
    !Number.isFinite(Number(a.selection.credit))) throw new Error("Не удалось прочитать незавершённый заказ. Проверьте операции перед новой оплатой.");
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
  const confirmed = useRef(new Set<string>());
  const config = useQuery({ queryKey: ["payment-config"], queryFn: getPaymentConfig });
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
      void client.invalidateQueries({ queryKey: ["wallet"] });
      void client.invalidateQueries({ queryKey: ["billing-subscription"] });
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
  }, [payment, payments.isError, attempt, storageKey, client]);
  function choose(next: CheckoutSelection) {
    if (!ready || storageError || attempt || !config.data?.enabled) return;
    setSelection(next); setAutoRenew(false); setError(null);
  }
  function resume() {
    if (!attempt) return;
    setSelection(attempt.selection); setAutoRenew(attempt.autoRenew); setError(null);
  }
  async function pay() {
    if (lock.current || !selection || !config.data?.enabled || storageError) return;
    lock.current = true; setPending(true); setError(null);
    try {
      // Persist BEFORE contacting the backend. A timeout, navigation or reload
      // must never silently turn a retry into a fresh charge.
      const stored = readAttempt(storageKey);
      if (stored && (stored.selection.kind !== selection.kind || stored.selection.code !== selection.code)) {
        setAttempt(stored);
        throw new Error("Есть другой незавершённый заказ. Продолжите его перед новой оплатой.");
      }
      const current = stored ?? { key: crypto.randomUUID(), selection, autoRenew };
      localStorage.setItem(storageKey, JSON.stringify(current)); setAttempt(current);
      const result = current.selection.kind === "topup"
        ? await createPayment(current.selection.code, current.key)
        : await createSubscriptionCheckout(current.selection.code as "pro" | "business", current.autoRenew, current.key);
      const updated = { ...current, paymentId: result.id };
      setLastPaymentId(result.id);
      localStorage.setItem(storageKey, JSON.stringify(updated)); setAttempt(updated);
      void client.invalidateQueries({ queryKey: ["payments"] });
      if (result.confirmation_url && !paymentState(result.status).terminal) window.location.assign(result.confirmation_url);
      else if (!paymentState(result.status).terminal) setError("Сервер не вернул ссылку для оплаты. Проверяем статус заказа; повторный запрос использует тот же заказ.");
      else setSelection(null);
    } catch (e) { setError(e instanceof Error ? e.message : "Не удалось начать оплату. Проверьте статус перед повтором."); }
    finally { lock.current = false; setPending(false); }
  }
  return {
    config, attempt, selection, setSelection, autoRenew, setAutoRenew, pending, error, storageError,
    paymentId, payment, payments, choose, resume, pay, locked: !ready || Boolean(storageError || attempt)
  };
}
export type PaymentJourney = ReturnType<typeof usePaymentJourney>;
export function PaymentCheckout({ journey: j }: { journey: PaymentJourney }) {
  const state = paymentState(j.payment?.status ?? "");
  return <>
    {j.storageError && <div role="alert" className="account-notice error">{j.storageError}</div>}
    {j.paymentId && <section className={`account-payment-result account-notice ${j.payments.isError ? "error" : state.tone}`} aria-live="polite">
      <div>
        <h2>{j.payments.isError ? "Не удалось проверить оплату" : j.payments.isPending ? "Проверяем результат оплаты…" : !j.payment ? "Платёж не найден" : state.label}</h2>
        <p>{j.payments.isError ? "Статус недоступен. Повторите проверку; новая оплата не требуется." : !j.payment && !j.payments.isPending ? "В последних 100 платежах аккаунта нет этого заказа. Его результат пока неизвестен." : j.payment?.status === "succeeded" ? `Сервер подтвердил платёж ${money(j.payment.amount_rub)}. Баланс и тариф обновляются.` : state.terminal ? "Результат получен с сервера. Подробности доступны в операциях." : "Дождитесь подтверждения сервера. Не оплачивайте заказ повторно."}</p>
        <small>Заказ: {j.paymentId}</small>
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
              <dt>Зачисление на баланс</dt>
              <dd>{money(j.selection.credit)}</dd>
            </div>
            <div>
              <dt>Период</dt>
              <dd>{j.selection.kind === "topup" ? "Разовое пополнение" : "1 месяц"}</dd>
            </div>
          </dl>
          {j.selection.kind === "plan" && <label className="account-consent">
            <input type="checkbox" checked={j.autoRenew} disabled={Boolean(j.attempt) || j.pending} onChange={e => j.setAutoRenew(e.target.checked)} />
            <span>Включить автопродление. Разрешаю ежемесячное списание стоимости тарифа с сохранённого способа оплаты. Согласие можно отозвать в разделе «Тариф».</span>
          </label>}
          <p className="account-hint">Нажимая «Перейти к оплате», вы принимаете <Link href="/legal/offer" target="_blank" rel="noopener noreferrer">публичную оферту</Link>. {j.selection.kind === "topup" ? "Это разовое пополнение, без подписки." : "Тариф активируется после подтверждения оплаты."}</p>
          {j.error && <p role="alert" className="account-notice error">{j.error}</p>}
          {!j.config.data?.enabled && <p className="account-notice pending">{j.config.data?.reason ?? "Платёжный сервис недоступен"}</p>}
          <Button disabled={j.pending || !j.config.data?.enabled || Boolean(j.storageError)} onClick={() => void j.pay()}>Перейти к оплате в ЮKassa{j.pending ? "…" : ""}</Button>
          <p className="account-hint">Возврат из ЮKassa сам по себе не подтверждает оплату. Мы проверим результат на сервере.</p>
        </>}
      </DialogContent>
    </Dialog>
  </>;
}
