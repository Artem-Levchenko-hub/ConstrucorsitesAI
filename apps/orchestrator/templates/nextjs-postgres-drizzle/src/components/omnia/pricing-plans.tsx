import * as React from "react";
import { Check } from "lucide-react";

/** Tiny class joiner — the drizzle kit ships no `cn`/`clsx`, so each
 *  self-contained piece joins its own conditional classes. */
function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

export interface PricingPlan {
  /** Tier name, e.g. «Базовый» / «Стандарт» / «Премиум». */
  name: React.ReactNode;
  /** The figure itself, e.g. «2 900 ₽» / «Бесплатно» / «от 990 ₽». Set large + tabular. */
  price: React.ReactNode;
  /** Quiet unit beside the figure, e.g. «/ мес» / «разово» / «за визит». */
  period?: React.ReactNode;
  /** One supporting line under the name — who the tier is for. */
  description?: React.ReactNode;
  /** What the tier includes — each becomes a check-marked row. */
  features: React.ReactNode[];
  /** The tier's action. Rendered filled on the highlighted tier, outline otherwise. */
  cta?: { label: React.ReactNode; href: string };
  /** Marks the recommended tier: a brand gradient border, a lift, and a badge. */
  highlighted?: boolean;
  /** Pill shown on the highlighted tier — defaults to «Популярный». */
  badge?: React.ReactNode;
}

export interface PricingPlansProps {
  /** The tiers, in display order (put the recommended one in the middle). */
  plans: PricingPlan[];
  className?: string;
}

const GRID_COLS: Record<number, string> = {
  1: "max-w-md mx-auto",
  2: "sm:grid-cols-2 max-w-4xl mx-auto",
  3: "md:grid-cols-3",
  4: "sm:grid-cols-2 lg:grid-cols-4",
};

function PricingTier({ plan }: { plan: PricingPlan }) {
  const { highlighted } = plan;

  const card = (
    <div
      className={cx(
        "flex h-full flex-col rounded-3xl border p-7 backdrop-blur-sm",
        highlighted
          ? "border-white/15 bg-white/[0.06] shadow-2xl shadow-[color-mix(in_oklab,var(--brand),transparent_75%)]"
          : "border-white/10 bg-white/[0.03] hover-lift hover:border-white/20",
      )}
    >
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-base font-semibold tracking-tight text-white">
          {plan.name}
        </h3>
        {highlighted ? (
          <span className="inline-flex items-center rounded-full bg-[var(--brand)] px-2.5 py-1 text-xs font-medium text-[var(--brand-fg)]">
            {plan.badge ?? "Популярный"}
          </span>
        ) : null}
      </div>

      <div className="mt-5 flex items-baseline gap-1.5">
        <span className="omnia-display text-4xl font-semibold tracking-tight tabular-nums text-white">
          {plan.price}
        </span>
        {plan.period ? (
          <span className="text-sm font-medium text-zinc-400">
            {plan.period}
          </span>
        ) : null}
      </div>
      {plan.description ? (
        <p className="mt-3 text-sm leading-relaxed text-zinc-400">
          {plan.description}
        </p>
      ) : null}

      {plan.features.length ? (
        <ul className="mt-7 flex flex-1 flex-col gap-3 border-t border-white/10 pt-7">
          {plan.features.map((feature, i) => (
            <li key={i} className="flex items-start gap-3 text-sm text-zinc-200">
              <span
                aria-hidden
                className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full bg-[color-mix(in_oklab,var(--brand),transparent_80%)] text-[var(--brand)] [&_svg]:size-3.5"
              >
                <Check strokeWidth={3} />
              </span>
              <span className="leading-relaxed">{feature}</span>
            </li>
          ))}
        </ul>
      ) : (
        <div className="flex-1" />
      )}

      {plan.cta ? (
        <a
          href={plan.cta.href}
          className={cx(
            "mt-8 inline-flex h-12 w-full items-center justify-center rounded-xl px-7 text-base font-semibold transition hover:brightness-110",
            highlighted
              ? "bg-[var(--brand)] text-[var(--brand-fg)] shadow-lg shadow-[color-mix(in_oklab,var(--brand),transparent_70%)]"
              : "border border-white/15 bg-white/5 text-zinc-100 hover:bg-white/10",
          )}
        >
          {plan.cta.label}
        </a>
      ) : null}
    </div>
  );

  // The highlighted tier carries a brand gradient border (a 1.5px gradient frame
  // around the card) and lifts above its neighbours on desktop — the way Stripe /
  // Linear / Framer pricing tables draw the eye to the recommended plan.
  return highlighted ? (
    <div className="rounded-3xl bg-gradient-to-b from-[var(--brand)] to-[color-mix(in_oklab,var(--brand),transparent_55%)] p-[1.5px] lg:-translate-y-3">
      {card}
    </div>
  ) : (
    card
  );
}

/**
 * `<PricingPlans>` — the public storefront's pricing table for the fullstack
 * (drizzle) template: a brand-aware row of tiers with one recommended plan drawn
 * out by a gradient border and a lift, the way enterprise marketing pages
 * (Stripe / Linear / Framer / Vercel) present plans. Drop it inside a
 * <StorefrontSection> (omit that section's `columns` — this component owns its own
 * responsive grid), and it reads as a different brand per niche straight from the
 * `--brand` token with zero per-app styling. Self-contained — no shadcn.
 *
 *   <StorefrontSection id="цены" eyebrow="Тарифы" title="Прозрачные цены"
 *     lead="Без скрытых платежей — выберите формат под свою задачу." align="center">
 *     <PricingPlans plans={[
 *       { name: "Разовый визит", price: "2 900 ₽", period: "за визит",
 *         description: "Для знакомства с клиникой.",
 *         features: ["Осмотр и консультация", "План лечения", "Снимок в подарок"],
 *         cta: { label: "Записаться", href: "/signin" } },
 *       { name: "Годовое наблюдение", price: "19 900 ₽", period: "/ год",
 *         description: "Самый выбираемый формат.", highlighted: true,
 *         features: ["Всё из разового", "2 чистки в год", "Скидка 15% на лечение", "Приоритетная запись"],
 *         cta: { label: "Оформить", href: "/signin" } },
 *       { name: "Семейный", price: "34 900 ₽", period: "/ год",
 *         description: "Наблюдение для всей семьи.",
 *         features: ["До 4 человек", "Детский стоматолог", "Выездная диагностика"],
 *         cta: { label: "Обсудить", href: "/signin" } },
 *     ]} />
 *   </StorefrontSection>
 */
export function PricingPlans({ plans, className }: PricingPlansProps) {
  const cols = GRID_COLS[Math.min(plans.length, 4)] ?? GRID_COLS[3];

  return (
    <div
      className={cx(
        "grid grid-cols-1 items-stretch gap-6",
        cols,
        className,
      )}
    >
      {plans.map((plan, i) => (
        <PricingTier key={i} plan={plan} />
      ))}
    </div>
  );
}
