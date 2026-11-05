import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";

import { LandingPricing } from "@/components/marketing/landing/LandingPricing";
import type { BillingPlan } from "@/lib/api/account";

const plans: BillingPlan[] = [
  { id: "free-v4", code: "free", version: 4, name: "Free", price_rub: "0.00", included_credit_rub: "0.0000", billing_interval: "month", entitlements: { max_projects: 1, static_publish_slots: 1, initial_generations_lifetime: 1, edits_lifetime: 1, team_seats: 1, always_on_slots: 0, integrations: true } },
  { id: "pro-v5", code: "pro", version: 5, name: "Pro", price_rub: "1.00", included_credit_rub: "0.0000", billing_interval: "month", entitlements: { max_projects: 3, static_publish_slots: 3, initial_generations_monthly: 2, edits_monthly: 25, team_seats: 1, always_on_slots: 1, integrations: true } },
  { id: "business-v3", code: "business", version: 3, name: "Business", price_rub: "6990.00", included_credit_rub: "0.0000", billing_interval: "month", entitlements: { max_projects: 10, static_publish_slots: 10, initial_generations_monthly: 5, edits_monthly: 50, team_seats: 5, always_on_slots: 1, integrations: true } },
];

function render(catalog: BillingPlan[] | null) {
  const host = document.createElement("div");
  host.innerHTML = renderToStaticMarkup(<LandingPricing plans={catalog} />);
  return host;
}

const text = (element: Element) => element.textContent?.replace(/\s+/g, " ") ?? "";

function expectUnavailable(catalog: BillingPlan[] | null) {
  const host = render(catalog);
  const status = host.querySelector('[role="status"]');
  expect(status).not.toBeNull();
  expect(status?.textContent ?? "").toContain("Не удалось загрузить тарифы");
  expect(host.querySelectorAll("article")).toHaveLength(0);
  expect(text(host)).not.toMatch(/₽|0,00|1,00|6 990/);
  expect(host.querySelector("a")?.getAttribute("href")).toBe("/login?next=/billing/plan");
}

it("shows all three supplied plans and their lifetime or monthly allowances without mutating their order", () => {
  const reversed = [...plans].reverse();
  const originalOrder = reversed.map(plan => plan.code);
  const host = render(reversed);
  const cards = Array.from(host.querySelectorAll("article"));
  expect(cards).toHaveLength(3);
  expect(cards.map(card => card.querySelector("h3")?.textContent)).toEqual(["Бесплатный", "Pro", "Business"]);
  const expected = [
    ["0,00 ₽", "Проектов: 1", "Публикаций: 1", "Генераций за всё время: 1", "Правок за всё время: 1"],
    ["1,00 ₽", "Проектов: 3", "Публикаций: 3", "Генераций в месяц: 2", "Правок в месяц: 25"],
    ["6 990,00 ₽", "Проектов: 10", "Публикаций: 10", "Генераций в месяц: 5", "Правок в месяц: 50"],
  ];
  cards.forEach((card, index) => expected[index].forEach(value => expect(text(card)).toContain(value)));
  expect(text(cards[0])).toContain("один раз на аккаунт");
  expect(text(cards[1])).toContain("после каждой успешной ежемесячной оплаты");
  expect(reversed.map(plan => plan.code)).toEqual(originalOrder);
});

it("keeps changed prices and allowances associated with the plan that supplied them", () => {
  const changed = plans.map(plan => plan.code === "pro" ? {
    ...plan, price_rub: "4275.50", entitlements: {
      ...plan.entitlements, max_projects: 7, static_publish_slots: 6,
      initial_generations_monthly: 4, edits_monthly: 33,
    },
  } : plan);
  const cards = Array.from(render(changed).querySelectorAll("article"));
  expect(cards).toHaveLength(3);
  expect(text(cards[1])).toContain("4 275,50 ₽");
  for (const value of ["Проектов: 7", "Публикаций: 6", "Генераций в месяц: 4", "Правок в месяц: 33"]) {
    expect(text(cards[1])).toContain(value);
    expect(text(cards[0])).not.toContain(value);
    expect(text(cards[2])).not.toContain(value);
  }
  expect(text(cards[2])).toContain("6 990,00 ₽");
});

it("shows all plans with one clear route to choosing a tariff in the account", () => {
  const host = render(plans);
  expect(text(host)).toContain("Бесплатный");
  expect(text(host)).not.toMatch(/\bFree\b|без карты|когда пойдут клиенты/i);
  expect(host.querySelector('a[href="/pricing"]')).toBeNull();
  const links = Array.from(host.querySelectorAll("a"));
  expect(links).toHaveLength(1);
  expect(links[0].getAttribute("href")).toBe("/login?next=/billing/plan");
  expect(links[0].textContent).toContain("Открыть тарифы в аккаунте");
});

it("shows no invented prices when the complete unique catalog is unavailable", () => {
  for (const catalog of [null, [], plans.slice(0, 2), [plans[0], plans[0], plans[2]]]) {
    expectUnavailable(catalog);
  }
});

it("rejects malformed price or entitlement data instead of publishing partial terms", () => {
  const malformed = [
    { ...plans[1], price_rub: "NaN" },
    { ...plans[1], price_rub: "-1.00" },
    { ...plans[1], entitlements: { ...plans[1].entitlements, max_projects: "three" } },
    { ...plans[1], entitlements: { ...plans[1].entitlements, edits_monthly: -1 } },
    { ...plans[1], entitlements: null },
    null,
  ];
  for (const invalid of malformed) {
    expectUnavailable([plans[0], invalid, plans[2]] as BillingPlan[]);
  }
  const sparse = [...plans];
  delete sparse[1];
  expectUnavailable(sparse);
});
