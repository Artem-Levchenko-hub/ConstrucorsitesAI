import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it } from "vitest";

import { CatalogDemo } from "./CatalogDemo";

let root: Root;
let host: HTMLDivElement;

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
});

function button(label: string) {
  const control = [...host.querySelectorAll("button")].find(element => (
    (element.getAttribute("aria-label") ?? element.textContent?.trim()) === label
  ));
  expect(control, label).toBeDefined();
  return control!;
}

async function click(label: string) {
  await act(async () => button(label).click());
}

async function choose(label: string, value: string) {
  const control = host.querySelector<HTMLSelectElement>(`select[aria-label="${label}"]`);
  expect(control, label).not.toBeNull();
  await act(async () => {
    control!.value = value;
    control!.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

it("adds distinct drink variants with their own quantity and price, then keeps the selected pickup in the receipt", async () => {
  await act(async () => root.render(<CatalogDemo kind="cafe" />));
  expect(host.textContent).not.toContain("9:56");

  const details = button("Состав: Капучино");
  details.focus();
  await click("Состав: Капучино");
  expect(host.textContent).toContain("1 шот (30 мл)");
  expect(host.textContent).toContain("170 мл");
  await click("Вернуться в каталог");
  expect(document.activeElement).toBe(button("Состав: Капучино"));

  await click("Добавить Капучино");
  await choose("Молоко", "oat");
  await choose("Сироп", "vanilla");
  await choose("Маршмеллоу", "yes");
  await click("Увеличить количество");
  expect(host.textContent).toContain("680 ₽");
  await click("Добавить в корзину");
  expect(host.textContent).toContain("9:56");

  await click("Добавить Капучино");
  await click("Добавить в корзину");
  await click("Чай");
  await click("Добавить Эрл Грей");
  expect(host.querySelector('select[aria-label="Маршмеллоу"]')).toBeNull();
  await choose("Лимон", "yes");
  await click("Добавить в корзину");
  await click("Открыть корзину");

  const lines = [...host.querySelectorAll(".ys-row")].map(row => row.textContent);
  expect(lines).toHaveLength(3);
  expect(lines[0]).toContain("Овсяное молоко · Ванильный сироп · Маршмеллоу");
  expect(lines[0]).toContain("680 ₽");
  expect(lines[1]).toContain("240 ₽");
  expect(lines[2]).toContain("Лимон");
  expect(host.textContent).toMatch(/1\s105 ₽/);
  await choose("Время готовности", "10:30");
  await click("Здесь");
  await click("Подтвердить заказ");
  expect(host.textContent).toContain("Здесь");
  expect(host.textContent).toContain("Готово к 10:30");
});

it("accrues exact plant bonuses on quantity and leaves pots out of the calculation", async () => {
  await act(async () => root.render(<CatalogDemo kind="shop" />));
  expect(button("Карта покупателя").textContent).toContain("0 ₽");
  await click("Кашпо");
  expect(host.textContent).not.toContain("Высота без кашпо");
  await click("Добавить Кашпо «Песок»");
  expect(button("Карта покупателя").textContent).toContain("0 ₽");

  await click("Новое");
  expect(host.textContent).toContain("Высота без кашпо · 40 см");
  await click("Добавить Калатея");
  await click("Добавить Калатея");
  expect(button("Карта покупателя").textContent).toContain("89 ₽");
  await click("Убрать Калатея");
  await click("Карта покупателя");
  expect(host.textContent).toContain("44,50 ₽");
  expect(host.textContent).toContain("За кашпо бонусы не начисляются");
});
