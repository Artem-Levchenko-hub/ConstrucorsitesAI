import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import { MaxInitialCatalog } from "@/components/max/MaxInitialCatalog";
import type { MaxContentItem } from "@/lib/api/types";

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
const mounted: { root: Root; container: HTMLDivElement }[] = [];

function item(id = "latte"): MaxContentItem {
  return {
    id, title: "Латте", price: "300 ₽", category: "Кофе", description: "С молоком",
    availability: "on_request", options: ["250 мл"], image_url: "https://example.test/latte.jpg",
    action_label: "Заказать", active: false,
  };
}

async function mount({
  items = [item()], pending = false, tooManyItems = false, descriptionChanged = false,
  error = null as string | null,
} = {}) {
  const change = vi.fn();
  const reset = vi.fn();
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  mounted.push({ root, container });
  function Harness() {
    const [draft, setDraft] = useState(items);
    return <MaxInitialCatalog items={draft} pending={pending} tooManyItems={tooManyItems}
      descriptionChanged={descriptionChanged} error={error} onReset={reset}
      onChange={next => { change(next); setDraft(next); }} />;
  }
  await act(async () => root.render(<Harness />));
  const input = (label: string) => {
    const element = [...container.querySelectorAll("label")].find(row => row.textContent === label)!;
    return document.getElementById(element.htmlFor) as HTMLInputElement;
  };
  const button = (label: string) => [...container.querySelectorAll("button")]
    .find(row => row.getAttribute("aria-label") === label || row.textContent === label)!;
  const click = async (label: string) => act(async () => button(label).click());
  const type = async (label: string, value: string) => act(async () => {
    const target = input(label);
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(target, value);
    target.dispatchEvent(new Event("input", { bubbles: true }));
  });
  return { container, input, button, click, type, change, reset };
}

afterEach(async () => {
  for (const { root, container } of mounted.splice(0)) {
    await act(async () => root.unmount());
    container.remove();
  }
});

it("edits only title and price while retaining stable IDs and all other catalog fields", async () => {
  const original = item();
  const untouched = item("cappuccino");
  const ui = await mount({ items: [original, untouched] });
  expect(ui.input("Название позиции 1").maxLength).toBe(120);
  expect(ui.input("Цена позиции 1").maxLength).toBe(80);
  await ui.type("Название позиции 1", "Латте без кофеина");
  await ui.type("Цена позиции 1", "По запросу");
  const saved = ui.change.mock.lastCall![0];
  expect(saved[0]).toEqual({ ...original, title: "Латте без кофеина", price: "По запросу" });
  expect(saved[1]).toBe(untouched);
  expect(original).toEqual(item());
});

it("removes exactly the selected row without remounting the surviving ID", async () => {
  const second = item("second");
  const ui = await mount({ items: [item(), second] });
  const survivingInput = ui.input("Название позиции 2");
  await ui.click("Убрать позицию 1");
  expect(ui.change).toHaveBeenLastCalledWith([second]);
  expect(ui.input("Название позиции 1")).toBe(survivingInput);
  expect(ui.container.textContent).toContain("Позиций: 1 из 100");
});

it("adds a fresh empty item using canonical defaults instead of inventing a product", async () => {
  const ui = await mount({ items: [] });
  expect(ui.change).not.toHaveBeenCalled();
  await ui.click("Добавить позицию");
  const created = ui.change.mock.lastCall![0][0];
  expect(created).toEqual({
    id: expect.stringMatching(/^item-[a-f0-9-]+$/), title: "", category: "", description: "",
    price: "", availability: "in_stock", options: [], image_url: "", action_label: "Открыть", active: true,
  });
  await ui.click("Добавить позицию");
  expect(ui.change.mock.lastCall![0][1].id).not.toBe(created.id);
  expect(ui.input("Название позиции 1").value).toBe("");
});

it("clears only by explicit action and leaves recognition to the parent", async () => {
  const ui = await mount({ descriptionChanged: true });
  expect(ui.container.querySelector('[role="status"]')?.textContent).toContain("Описание изменилось");
  expect(ui.change).not.toHaveBeenCalled();
  expect(ui.reset).not.toHaveBeenCalled();
  await ui.click("Начать с пустого каталога");
  expect(ui.change).toHaveBeenLastCalledWith([]);
  expect(ui.container.textContent).toContain("Каталог начнёт работу без позиций");
  await ui.click("Распознать заново");
  expect(ui.reset).toHaveBeenCalledTimes(1);
  expect(ui.container.querySelectorAll("input")).toHaveLength(0);
});

it("disables all controls while pending, including synthetic input changes", async () => {
  const ui = await mount({ pending: true });
  expect(ui.container.querySelector("section")?.getAttribute("aria-busy")).toBe("true");
  for (const control of ui.container.querySelectorAll<HTMLInputElement | HTMLButtonElement>("input, button")) {
    expect(control.disabled).toBe(true);
  }
  await ui.type("Название позиции 1", "Не сохранять");
  for (const label of ["Убрать позицию 1", "Добавить позицию", "Распознать заново", "Начать с пустого каталога"]) {
    await ui.click(label);
  }
  expect(ui.change).not.toHaveBeenCalled();
  expect(ui.reset).not.toHaveBeenCalled();
});

it("blocks the 101st addition without hiding or truncating an oversized supplied draft", async () => {
  const ui = await mount({ items: Array.from({ length: 101 }, (_, index) => item(`row-${index}`)) });
  expect(ui.button("Добавить позицию").disabled).toBe(true);
  expect(ui.container.querySelectorAll("li")).toHaveLength(101);
  expect(ui.container.querySelector('[role="alert"]')?.textContent).toContain("не более 100");
  expect(ui.container.textContent).toContain("Позиций: 101 из 100");
  await ui.click("Добавить позицию");
  expect(ui.change).not.toHaveBeenCalled();
  await ui.click("Убрать позицию 101");
  expect(ui.button("Добавить позицию").disabled).toBe(true);
  expect(ui.container.querySelector('[role="alert"]')).toBeNull();
  await ui.click("Убрать позицию 100");
  expect(ui.button("Добавить позицию").disabled).toBe(false);
});

it("keeps a parser overflow visible even with no rows and exposes the parent error", async () => {
  const ui = await mount({ items: [], tooManyItems: true, error: "Проверьте название позиции" });
  expect(ui.button("Добавить позицию").disabled).toBe(true);
  expect(ui.container.querySelector('[role="alert"]')?.textContent).toBe("Проверьте название позиции");
  expect(ui.button("Распознать заново").disabled).toBe(false);
  expect(ui.button("Начать с пустого каталога").disabled).toBe(false);
  expect([...ui.container.querySelectorAll("button")].every(button => button.type === "button")).toBe(true);
});
