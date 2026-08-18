import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MaxProductAdvisor } from "@/components/max/MaxProductAdvisor";
import {
  getProductAdviceSnapshotId,
  submitProductAdvice,
  type ProductAdviceItem,
} from "@/lib/api/product-advice";

const ITEMS: ProductAdviceItem[] = [
  {
    id: "saved-favorites",
    kind: "feature",
    title: "Избранное",
    benefit: "Быстрее возвращаться к любимым товарам",
    prompt: "Добавь настоящее избранное с сохранением.",
  },
  {
    id: "repeat-order",
    kind: "improvement",
    title: "Повтор заказа",
    benefit: "Сократить частый сценарий до одного действия",
    prompt: "Добавь безопасный повтор прошлого заказа.",
  },
  {
    id: "order-status",
    kind: "feature",
    title: "Статус заказа",
    benefit: "Показывать следующий этап после покупки",
    prompt: "Добавь прозрачный статус заказа.",
  },
  {
    id: "fourth-must-not-render",
    kind: "feature",
    title: "Лишняя карточка",
    benefit: "Эта карточка не должна появиться",
    prompt: "Не отправлять.",
  },
];

describe("MAX product advisor", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    (
      globalThis as typeof globalThis & {
        IS_REACT_ACT_ENVIRONMENT: boolean;
      }
    ).IS_REACT_ACT_ENVIRONMENT = true;
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it("renders no more than three contextual actions", () => {
    act(() => {
      root.render(
        createElement(MaxProductAdvisor, {
          items: ITEMS,
          applyingId: null,
          onApply: vi.fn(),
        }),
      );
    });

    expect(container.querySelectorAll("[data-advice-id]")).toHaveLength(3);
    expect(container.textContent).toContain("Что улучшить дальше");
    expect(container.textContent).toContain("Избранное");
    expect(container.textContent).toContain("Улучшить");
    expect(container.textContent).not.toContain("Лишняя карточка");
  });

  it("passes the server-owned item through the one-click action", () => {
    const onApply = vi.fn();
    act(() => {
      root.render(
        createElement(MaxProductAdvisor, {
          items: ITEMS,
          applyingId: null,
          onApply,
        }),
      );
    });

    const row = container.querySelector<HTMLElement>(
      "[data-advice-id='saved-favorites']",
    );
    const button = row?.querySelector<HTMLButtonElement>("button");
    expect(button).not.toBeNull();
    act(() => button?.click());

    expect(onApply).toHaveBeenCalledTimes(1);
    expect(onApply).toHaveBeenCalledWith(ITEMS[0]);
  });

  it("disables only the action currently being applied", () => {
    act(() => {
      root.render(
        createElement(MaxProductAdvisor, {
          items: ITEMS,
          applyingId: "saved-favorites",
          onApply: vi.fn(),
        }),
      );
    });

    const first = container.querySelector<HTMLButtonElement>(
      "[data-advice-id='saved-favorites'] button",
    );
    const second = container.querySelector<HTMLButtonElement>(
      "[data-advice-id='repeat-order'] button",
    );
    expect(first?.disabled).toBe(true);
    expect(first?.getAttribute("aria-busy")).toBe("true");
    expect(second?.disabled).toBe(false);
    expect(second?.className).toContain("min-h-11");
  });

  it("requests advice only after the latest assistant build completes", () => {
    const completed = {
      role: "assistant" as const,
      snapshot_id: "snapshot-new",
      tokens_out: 420,
      generation_status: "completed" as const,
    };

    expect(getProductAdviceSnapshotId([completed])).toBe("snapshot-new");
    expect(
      getProductAdviceSnapshotId([{ ...completed, tokens_out: null }]),
    ).toBeNull();
    expect(
      getProductAdviceSnapshotId([
        completed,
        { ...completed, role: "user" as const },
      ]),
    ).toBeNull();
    expect(
      getProductAdviceSnapshotId([
        { ...completed, generation_status: "failed" as const },
      ]),
    ).toBeNull();
    expect(
      getProductAdviceSnapshotId([{ ...completed, snapshot_id: null }]),
    ).toBeNull();
  });

  it("submits the server-owned implementation prompt through the normal chat", async () => {
    const submit = vi.fn().mockResolvedValue(true);

    await expect(submitProductAdvice(ITEMS[0], submit)).resolves.toBe(true);
    expect(submit).toHaveBeenCalledWith(ITEMS[0].prompt, []);
  });
});
