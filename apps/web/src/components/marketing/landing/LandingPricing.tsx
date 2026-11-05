import { ArrowRight, Check } from "lucide-react";
import Link from "next/link";

import type { BillingPlan } from "@/lib/api/account";
import { planAllowanceRows, planPeriodDescription } from "@/lib/billing-allowances";

const PLAN_CODES = ["free", "pro", "business"] as const;
const ACCOUNT_HREF = "/login?next=/billing/plan";
const money = new Intl.NumberFormat("ru-RU", {
  style: "currency", currency: "RUB", maximumFractionDigits: 2,
});
const LIMIT_ROWS = [
  ["max_projects", "Проектов"],
  ["static_publish_slots", "Публикаций"],
  ["team_seats", "Мест в команде"],
  ["always_on_slots", "Постоянно работающих приложений"],
] as const;

function isCount(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}

function isAmount(value: unknown): value is string {
  return typeof value === "string" && /^\d+(\.\d+)?$/.test(value)
    && Number.isFinite(Number(value));
}

function isPlan(value: unknown): value is BillingPlan {
  if (!value || typeof value !== "object") return false;
  const plan = value as Partial<BillingPlan>;
  const entitlements = plan.entitlements;
  if (
    typeof plan.id !== "string" || !plan.id.trim()
    || typeof plan.name !== "string" || !plan.name.trim()
    || !PLAN_CODES.some(code => code === plan.code)
    || !Number.isSafeInteger(plan.version) || Number(plan.version) < 1
    || plan.billing_interval !== "month"
    || !isAmount(plan.price_rub) || !isAmount(plan.included_credit_rub)
    || !entitlements || typeof entitlements !== "object" || Array.isArray(entitlements)
  ) return false;
  const limit = (value: unknown) => value === null || isCount(value);
  return ["max_projects", "static_publish_slots"].every(key => limit(entitlements[key]))
    && ["team_seats", "always_on_slots"].every(key => entitlements[key] === undefined || limit(entitlements[key]))
    && ["initial_generations_lifetime", "edits_lifetime", "initial_generations_monthly", "edits_monthly", "generation_messages_lifetime"]
      .every(key => entitlements[key] === undefined || isCount(entitlements[key]))
    && (entitlements.integrations === undefined || typeof entitlements.integrations === "boolean");
}

function orderedCatalog(plans: BillingPlan[] | null): BillingPlan[] | null {
  if (!Array.isArray(plans) || plans.length !== PLAN_CODES.length) return null;
  const catalog = Array.from(plans);
  if (
    !catalog.every(isPlan)
    || new Set(catalog.map(plan => plan.code)).size !== PLAN_CODES.length
  ) return null;
  return catalog.sort((left, right) => PLAN_CODES.indexOf(left.code) - PLAN_CODES.indexOf(right.code));
}

export function LandingPricing({ plans }: { plans: BillingPlan[] | null }) {
  const catalog = orderedCatalog(plans);
  return (
    <section id="pricing" className="yl-section yl-section--tint" data-marketing-section="pricing" aria-labelledby="landing-pricing-heading">
      <div className="yl-wrap yl-head yl-head--center">
        <h2 id="landing-pricing-heading">Тарифы для вашего приложения</h2>
        <p className="yl-lead">Сравните стоимость, количество проектов и лимиты генераций и правок.</p>
      </div>
      <div className="yl-wrap yl-plans">
        {catalog ? catalog.map(plan => {
          const isFree = plan.code === "free";
          const name = isFree ? "Бесплатный" : plan.name;
          const rows = [
            ...LIMIT_ROWS.flatMap(([key, label]) => plan.entitlements[key] === undefined ? [] : [{
              key, label, value: plan.entitlements[key] === null ? "Без ограничений" : String(plan.entitlements[key]),
            }]),
            ...planAllowanceRows(plan),
            ...(typeof plan.entitlements.integrations === "boolean" ? [{
              key: "integrations", label: "Подключение ЮKassa", value: plan.entitlements.integrations ? "Да" : "Нет",
            }] : []),
          ];
          return (
            <article key={plan.id} className="yl-plan">
              <h3 className="yl-plan-top">{name}</h3>
              <p className="yl-price">{money.format(Number(plan.price_rub))}<span>/ месяц</span></p>
              <ul>
                {rows.map(row => <li key={row.key}><Check aria-hidden="true" /><span>{row.label}: <strong>{row.value}</strong></span></li>)}
              </ul>
              <p className="yl-note">{planPeriodDescription(plan)}</p>
            </article>
          );
        }) : <div role="status" className="yl-plan" style={{ gridColumn: "1 / -1" }}>
          <h3>Не удалось загрузить тарифы</h3>
          <p>Актуальные цены временно недоступны. Обновите страницу или откройте раздел тарифов в аккаунте.</p>
        </div>}
      </div>
      <div className="yl-wrap yl-plan-actions">
        <Link href={ACCOUNT_HREF} className="yl-cta yl-cta--lg yl-cta--outline">Открыть тарифы в аккаунте<ArrowRight size={18} aria-hidden="true" /></Link>
      </div>
    </section>
  );
}
