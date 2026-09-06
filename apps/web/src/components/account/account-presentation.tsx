import { Button } from "@/components/ui/button";
export const money = (value: string | number) => `${Number(value).toLocaleString("ru-RU")} ₽`;
export const date = (value: string | null) => value ? new Date(value).toLocaleString("ru-RU", { dateStyle: "medium", timeStyle: "short" }) : "—";
export function QueryError({ error, retry }: { error: Error | null; retry?: () => void }) {
  if (!error) return null;
  return <div className="account-notice error" role="alert">
    <p>{error.message}</p>
    {retry && <Button variant="outline" onClick={retry}>Повторить</Button>}</div>;
}
export function paymentState(status: string) {
  switch (status) {
    case "succeeded": return { label: "Оплата подтверждена", tone: "success", terminal: true };
    case "pending": return { label: "Ожидает оплаты", tone: "pending", terminal: false };
    case "waiting_for_capture": return { label: "Проверяем оплату", tone: "pending", terminal: false };
    case "cancelled": case "canceled": return { label: "Оплата отменена", tone: "neutral", terminal: true };
    case "failed": return { label: "Оплата не прошла", tone: "error", terminal: true };
    case "refunded": return { label: "Возврат проведён", tone: "neutral", terminal: true };
    default: return { label: "Статус неизвестен", tone: "pending", terminal: false };
  }
}
