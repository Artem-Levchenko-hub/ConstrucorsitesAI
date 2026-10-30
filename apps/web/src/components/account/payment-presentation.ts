import type { Payment } from "@/lib/api/account";
import { allowanceCount } from "@/lib/billing-allowances";
import { paymentState } from "./account-presentation";

const planNames = new Map([["free", "Free"], ["pro", "Pro"], ["business", "Business"]]);

export function paymentTitle(payment: Payment): string {
  if (payment.purpose === "payment_test") return "Проверочная оплата";
  if (payment.purpose === "wallet_topup") return payment.edits_only ? "Пакет правок" : "Пакет использования";
  const code = /^subscription:([^:]+):v\d+$/.exec(payment.package_code)?.[1];
  const name = code ? planNames.get(code) : undefined;
  if (payment.purpose === "subscription_renewal") return name ? `Продление ${name}` : "Продление тарифа";
  return name ? `Тариф ${name}` : "Покупка тарифа";
}

/** Describe only the outcome recorded on this payment, never today's plan or balance. */
export function paymentOutcome(payment: Payment): string {
  if (payment.status === "refunded") return "Оплата возвращена";
  if (payment.purpose === "payment_test") return "Подписка и пакеты без изменений";
  if (payment.status !== "succeeded") {
    return ["pending", "waiting_for_capture", "cancelled", "canceled", "failed"].includes(payment.status)
      ? "Не зачислено" : "Результат уточняется";
  }
  if (payment.purpose === "subscription_renewal") return "Тариф продлён";
  if (payment.purpose === "subscription_initial") return "Тариф активирован";
  if (typeof payment.edit_units === "number" && payment.edit_units > 0) {
    return `Начислено: ${allowanceCount(payment.edit_units, "edits")}`;
  }
  return "Зачислено по условиям покупки";
}

export function paymentConfirmationDescription(
  payment: Payment | undefined, request: { isError: boolean; isPending: boolean },
): string {
  if (request.isError) return "Статус недоступен. Повторите проверку; новая оплата не требуется.";
  if (request.isPending) return "Дождитесь подтверждения сервера. Не оплачивайте заказ повторно.";
  if (!payment) return "В последних 100 платежах аккаунта нет этого заказа. Его результат пока неизвестен.";
  if (payment.status === "succeeded") {
    return payment.purpose === "payment_test" ? "Подписка и пакеты не изменены." : paymentOutcome(payment);
  }
  if (payment.status === "refunded") return "Оплата возвращена. Подробности доступны в истории операций.";
  if (paymentState(payment.status).terminal) return "Результат получен с сервера. Подробности доступны в операциях.";
  return "Дождитесь подтверждения сервера. Не оплачивайте заказ повторно.";
}
