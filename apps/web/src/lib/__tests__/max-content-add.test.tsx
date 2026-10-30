import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MaxProjectSetupSections } from "@/components/max/MaxProjectSetupSections";
import type { MaxContentItem, MaxProjectConfigPayload } from "@/lib/api/types";

const first: MaxContentItem = {
  id: "retained-first", title: "Первая позиция", category: "Одежда", description: "Сохранённое описание",
  price: "от 2 000 ₽", availability: "on_request", options: ["S", "M"],
  image_url: "https://example.test/first.webp", action_label: "Заказать", active: false,
};
const second: MaxContentItem = {
  id: "retained-second", title: "Вторая позиция", category: "Услуги", description: "Другое описание",
  price: "По запросу", availability: "in_stock", options: ["30 минут"],
  image_url: "", action_label: "Записаться", active: true,
};
const config: MaxProjectConfigPayload = {
  app_name: "Каталог", app_type: "catalog", summary: "Описание", audience: "Гости", primary_action: "Заказать",
  features: [], style: "clean", brand_colors: "", content: [], operator: { legal_name: "Владелец" },
  support: { email: null, response_time: "Один рабочий день" },
  legal: { age_rating: "0+", has_sales: true, has_user_content: false, marketing_notifications: false,
    personal_data_consent: true, terms_accepted: false, policy_url: "" },
  max_url_attached: false,
};

let root: Root;
let host: HTMLDivElement;
let current: MaxProjectConfigPayload;
let scrolled: Element[];
let scrollDescriptor: PropertyDescriptor | undefined;

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  scrolled = [];
  scrollDescriptor = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "scrollIntoView");
  Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
    configurable: true,
    value(this: HTMLElement) { scrolled.push(this); },
  });
  vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new Error("Adding a draft item must stay local"))));
});

afterEach(async () => {
  await act(async () => root.unmount()); host.remove(); vi.unstubAllGlobals();
  if (scrollDescriptor) Object.defineProperty(HTMLElement.prototype, "scrollIntoView", scrollDescriptor);
  else delete (HTMLElement.prototype as Partial<HTMLElement>).scrollIntoView;
});

async function mount(content: MaxContentItem[]) {
  current = { ...config, content };
  function Editor() {
    const [draft, setDraft] = useState(current);
    return <MaxProjectSetupSections section="content" projectId="owned" current={draft}
      onChange={next => { current = next; setDraft(next); }} />;
  }
  await act(async () => root.render(<Editor />));
}

function addButton() {
  return [...host.querySelectorAll<HTMLButtonElement>("button")].find(button => button.textContent?.trim() === "Добавить позицию")!;
}

async function fill(input: HTMLInputElement, value: string) {
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

it("prepends a local item and focuses its name without losing existing values or editors", async () => {
  await mount([structuredClone(first), structuredClone(second)]);
  const previousName = host.querySelector<HTMLInputElement>('[aria-label="Название элемента 1"]')!;
  const previousOptions = host.querySelector<HTMLInputElement>('[aria-label="Варианты элемента 1"]')!;
  await fill(previousOptions, "S, M, ");
  const previousContent = structuredClone(current.content);
  expect(scrolled).toHaveLength(0);

  await act(async () => addButton().click());

  expect(current.content.map(item => item.id)).toEqual([expect.any(String), "retained-first", "retained-second"]);
  expect(current.content[0].id).not.toBe("retained-first");
  expect(current.content[0].id).not.toBe("retained-second");
  expect(current.content.slice(1)).toEqual(previousContent);
  const name = host.querySelector<HTMLInputElement>('[aria-label="Название элемента 1"]')!;
  expect(name.value).toBe("");
  expect(document.activeElement).toBe(name);
  expect(scrolled).toEqual([name.closest("fieldset")]);
  expect(host.querySelector('[aria-label="Название элемента 2"]')).toBe(previousName);
  expect(host.querySelector('[aria-label="Варианты элемента 2"]')).toBe(previousOptions);
  expect(previousOptions.value).toBe("S, M, ");

  previousName.focus();
  await fill(previousName, "Изменённая первая позиция");
  expect(document.activeElement).toBe(previousName);
  expect(scrolled).toHaveLength(1);
  expect(current.content[1]).toEqual({ ...previousContent[0], title: "Изменённая первая позиция" });
  expect(current.content[2]).toEqual(second);
  expect(fetch).not.toHaveBeenCalled();
});

it("allows the hundredth item and blocks further additions without dropping or reordering existing items", async () => {
  const original = Array.from({ length: 99 }, (_, index) => ({ ...first, id: `existing-${index}`, title: `Позиция ${index + 1}` }));
  await mount(original);
  expect(addButton().disabled).toBe(false);

  await act(async () => addButton().click());

  expect(current.content).toHaveLength(100);
  expect(current.content.slice(1)).toEqual(original);
  expect(addButton().disabled).toBe(true);
  const atLimit = structuredClone(current.content);
  await act(async () => addButton().click());
  expect(current.content).toEqual(atLimit);
  expect(fetch).not.toHaveBeenCalled();
});
