import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MaxProjectWizard } from "@/components/max/MaxProjectWizard";
import type { MaxFeature } from "@/lib/max-brief";

const features: MaxFeature[] = ["Профиль пользователя", "История действий"];
const reviewCss = readFileSync(
  resolve(process.cwd(), "src/components/max/max-project-review.css"),
  "utf8",
);
const values = {
  name: "Очень длинное название приложения без пробелов________________________________",
  idea: "Помогает постоянным гостям находить награды и получать их прямо в кофейне.",
  appType: "catalog" as const,
  audience: "Постоянные гости кофейни",
  primaryAction: "Выбрать награду",
  features,
  style: "bright" as const,
  brandColors: "Молочный и ультрамарин",
};

let root: Root;
let container: HTMLDivElement;

async function renderReview(overrides: Partial<typeof values> = {}) {
  await act(async () => {
    root.render(
      <MaxProjectWizard
        open
        onOpenChange={vi.fn()}
        values={{ ...values, ...overrides }}
        onChange={vi.fn()}
        pending={false}
        onSubmit={vi.fn()}
      />,
    );
  });

  for (let step = 0; step < 3; step += 1) {
    const next = [...document.querySelectorAll("button")].find(
      button => button.textContent?.trim() === "Далее",
    );
    expect(next).toBeTruthy();
    await act(async () => { next!.click(); });
  }
}

beforeEach(() => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  document.head.replaceChildren();
  const style = document.createElement("style");
  style.textContent = reviewCss;
  document.head.append(style);
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => { root.unmount(); });
  container.remove();
});

it("presents the project identity and main action before a semantic feature and detail summary", async () => {
  await renderReview();

  const review = document.querySelector<HTMLElement>('[aria-label="Сводка проекта"]');
  expect(review).toBeTruthy();
  expect(review?.querySelector("h3")?.textContent).toBe(values.name);
  expect(review?.querySelector("header")?.textContent).toContain(values.idea);

  const action = review?.querySelector<HTMLElement>('[aria-labelledby="max-review-primary-action"]');
  expect(action?.querySelector("h4")?.textContent).toBe("Главное действие");
  expect(action?.textContent).toContain(values.primaryAction);

  const featureList = review?.querySelector('[aria-labelledby="max-review-features"] ul');
  expect([...featureList!.querySelectorAll("li")].map(item => item.lastElementChild?.textContent)).toEqual(features);

  const details = review?.querySelector("dl");
  expect([...details!.querySelectorAll("dt")].map(term => term.textContent)).toEqual([
    "Тип приложения",
    "Аудитория",
    "Стиль",
    "Цвета бренда",
  ]);
  expect(details?.textContent).toContain("Каталог");
  expect(details?.textContent).toContain("Яркий");
});

it("uses wrapping, shrinkable review groups that remain bounded at 320px", async () => {
  await renderReview();

  const review = document.querySelector<HTMLElement>('[aria-label="Сводка проекта"]');
  expect(review).toBeTruthy();
  if (!review) return;
  review.style.width = "320px";
  const title = review.querySelector<HTMLElement>("h3")!;
  const action = review.querySelector<HTMLElement>('[aria-labelledby="max-review-primary-action"]')!;
  const details = review.querySelector<HTMLElement>("dl")!;

  expect(getComputedStyle(review).maxWidth).toBe("100%");
  expect(getComputedStyle(title).overflowWrap).toBe("anywhere");
  expect(getComputedStyle(action).minWidth).toBe("0px");
  expect(getComputedStyle(details).gridTemplateColumns).toBe("minmax(0, 1fr)");
});
