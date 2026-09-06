"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { getSubscription, listBillingPlans, manageSubscription } from "@/lib/api/account";
import type { PaymentJourney } from "./PaymentCheckout";
import { date, money, QueryError } from "./account-presentation";
export function AccountPlan({ journey: j }: { journey: PaymentJourney }) {
  const client = useQueryClient();
  const plans = useQuery({ queryKey: ["billing-plans"], queryFn: listBillingPlans });
  const subscription = useQuery({ queryKey: ["billing-subscription"], queryFn: getSubscription });
  const manage = useMutation({ mutationFn: manageSubscription, onSuccess: updated => client.setQueryData(["billing-subscription"], updated) });
  const current = subscription.data;
  return <>
    <QueryError error={subscription.error} retry={() => void subscription.refetch()} />
    {subscription.isPending && <p role="status">Загружаем тариф…</p>}
    {current && <section className="account-plan-current">
      <div>
        <p>Текущий тариф</p>
        <h2>{current.plan.name}</h2>
        <strong>{money(current.plan.price_rub)} / месяц</strong>
        <p>{current.current_period_end ? `Оплачен до ${date(current.current_period_end)}` : "Нет оплаченного периода"}</p>
      </div>
      <div className="account-renewal">
        <span className="account-status pending">{current.cancel_at_period_end ? "Отменится в конце периода" : current.auto_renew ? "Автопродление включено" : "Без автопродления"}</span>
        {current.next_charge_at && <p>Следующее списание: {date(current.next_charge_at)}</p>}
        {current.auto_renew && <Button variant="outline" disabled={manage.isPending} onClick={() => manage.mutate("cancel")}>Отменить автопродление</Button>}
        {current.can_restore && <>
          <p>Восстановление разрешает ежемесячное списание стоимости тарифа.</p>
          <Button variant="outline" disabled={manage.isPending} onClick={() => manage.mutate("restore")}>Согласен и восстановить автопродление</Button>
        </>}
        <QueryError error={manage.error} />{manage.isSuccess && <p role="status">Настройки подписки обновлены.</p>}
      </div>
    </section>}
    {current?.status === "past_due" && <p className="account-notice pending">Продление не прошло. Льготный период до {date(current.grace_period_ends_at)}.</p>}
    {current?.status === "paused" && <p className="account-notice pending">Подписка приостановлена.</p>}
    <section className="account-panel">
      <h2>Выберите подходящий тариф</h2>
      <QueryError error={plans.error} retry={() => void plans.refetch()} />
      <QueryError error={j.config.error} retry={() => void j.config.refetch()} />
      <div className="account-plans">{plans.data?.map(plan => {
        const active = current?.plan.id === plan.id;
        return <article key={plan.id} className={active ? "current" : ""}>
          <h3>{plan.name}</h3>
          {active && <span className="account-status success">Текущий</span>}
          <p className="account-plan-price">{money(plan.price_rub)}
            <small>в месяц</small>
          </p>
          <dl>
            <div>
              <dt>Проектов</dt>
              <dd>{String(plan.entitlements.max_projects ?? "—")}</dd>
            </div>
            <div>
              <dt>Мест в команде</dt>
              <dd>{String(plan.entitlements.team_seats ?? "—")}</dd>
            </div>
            {([["static_publish_slots", "Публикаций"], ["always_on_slots", "Постоянно работающих приложений"], ["custom_domains", "Своих доменов"]] as const).map(([key, label]) => plan.entitlements[key] !== undefined && <div key={key}>
              <dt>{label}</dt>
              <dd>{String(plan.entitlements[key])}</dd>
            </div>)}
            {typeof plan.entitlements.integrations === "boolean" && <div>
              <dt>Интеграции</dt>
              <dd>{plan.entitlements.integrations ? "Да" : "Нет"}</dd>
            </div>}
            <div>
              <dt>Кредит на баланс</dt>
              <dd>{money(plan.included_credit_rub)}</dd>
            </div>
          </dl>
          {plan.code !== "free" && <Button variant={active ? "outline" : "primary"} disabled={active || !current || !j.config.data?.enabled || j.locked} onClick={() => j.choose({ kind: "plan", code: plan.code, title: plan.name, price: plan.price_rub, credit: plan.included_credit_rub })}>{active ? "Текущий тариф" : `Выбрать ${plan.name}`}</Button>}</article>;
      })}</div>
      {j.config.data && !j.config.data.enabled && <p className="account-notice pending">{j.config.data.reason ?? "Платёжный сервис недоступен"}</p>}
      <p className="account-hint">Тариф и включённый кредит активируются после подтверждения оплаты. Автопродление подключается только с отдельного согласия при проверке заказа.</p>
    </section>
  </>;
}
