import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MaxProjectWizard } from "@/components/max/MaxProjectWizard";
import type { MaxFeature, MaxProjectBrief } from "@/lib/max-brief";

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
  expect(
    [...featureList!.querySelectorAll("li")].map(
      item => item.querySelector(".max-project-review__feature-label")?.textContent,
    ),
  ).toEqual(features);
  // Каждая функция объясняется словами, а не только называется.
  expect(featureList?.querySelector("small")?.textContent?.length ?? 0).toBeGreaterThan(20);

  const facts = review?.querySelector<HTMLElement>(".max-project-review__facts");
  expect([...facts!.querySelectorAll("h4")].map(term => term.textContent)).toEqual([
    "Тип приложения",
    "Для кого",
    "Оформление",
  ]);
  expect(facts?.textContent).toContain("Каталог");
  expect(facts?.textContent).toContain("Яркий");
  expect(facts?.textContent).toContain(values.audience);

  // Владелец должен понимать, что произойдёт по кнопке «Создать проект».
  const next = review?.querySelector('[aria-labelledby="max-review-next"]');
  expect(next?.querySelectorAll("ol li")).toHaveLength(3);
});

it("uses wrapping, shrinkable review groups that remain bounded at 320px", async () => {
  await renderReview();

  const review = document.querySelector<HTMLElement>('[aria-label="Сводка проекта"]');
  expect(review).toBeTruthy();
  if (!review) return;
  review.style.width = "320px";
  const title = review.querySelector<HTMLElement>("h3")!;
  const action = review.querySelector<HTMLElement>('[aria-labelledby="max-review-primary-action"]')!;
  const facts = review.querySelector<HTMLElement>(".max-project-review__facts")!;

  expect(getComputedStyle(review).maxWidth).toBe("100%");
  expect(getComputedStyle(title).overflowWrap).toBe("anywhere");
  expect(getComputedStyle(action).minWidth).toBe("0px");
  expect(getComputedStyle(facts).gridTemplateColumns).toBe("minmax(0, 1fr)");
});

async function selectableWizard(initial: MaxProjectBrief) {
  const submit = vi.fn(async (_brief: MaxProjectBrief) => {});
  function Harness() {
    const [answers, setAnswers] = useState(initial);
    return <MaxProjectWizard open onOpenChange={vi.fn()} values={answers}
      onChange={patch => setAnswers(previous => ({ ...previous, ...patch }))}
      pending={false} onSubmit={() => submit(answers)} />;
  }
  await act(async () => root.render(<Harness />));
  const typeButton = (label: string) => {
    const group = [...document.querySelectorAll("fieldset")].find(fieldset => fieldset.querySelector("legend")?.textContent === "Тип приложения")!;
    return [...group.querySelectorAll("button")].find(button => button.querySelector("span")?.textContent?.startsWith(label))!;
  };
  const click = async (name: string) => {
    const button = [...document.querySelectorAll("button")].find(candidate => candidate.textContent?.trim() === name)!;
    expect(button).toBeDefined();
    await act(async () => button.click());
  };
  return { submit, typeButton, click };
}

it("preserves three selected types through back and review and submits the full brief only once", async () => {
  const ui = await selectableWizard({ ...values, appType: "loyalty", appTypes: ["loyalty"] });
  await act(async () => ui.typeButton("Каталог").click());
  await act(async () => ui.typeButton("Запись").click());
  for (const label of ["Лояльность", "Каталог", "Запись"]) {
    expect(ui.typeButton(label).getAttribute("aria-pressed"), label).toBe("true");
  }
  for (let index = 0; index < 3; index += 1) await ui.click("Далее");
  const facts = document.querySelector('[aria-label="Сводка проекта"] .max-project-review__facts');
  expect(facts?.textContent).toContain("Лояльность, Каталог, Запись");
  for (let index = 0; index < 3; index += 1) await ui.click("Назад");
  for (const label of ["Лояльность", "Каталог", "Запись"]) expect(ui.typeButton(label).getAttribute("aria-pressed")).toBe("true");
  for (let index = 0; index < 3; index += 1) await ui.click("Далее");
  const submit = [...document.querySelectorAll("button")].find(button => button.textContent?.includes("Создать проект"))!;
  await act(async () => { submit.click(); submit.click(); });
  expect(ui.submit).toHaveBeenCalledTimes(1);
  expect(ui.submit.mock.calls[0][0]).toMatchObject({
    appType: "loyalty", appTypes: ["loyalty", "catalog", "booking"],
    managementModules: ["catalog", "orders", "leads", "bookings"],
  });
});

it("keeps the last type selected and retains manually chosen modules when another type is added or removed", async () => {
  const ui = await selectableWizard({ ...values, appType: "education", appTypes: ["education"], managementModules: ["catalog", "leads", "bookings"] });
  await act(async () => ui.typeButton("Обучение").click());
  expect(ui.typeButton("Обучение").getAttribute("aria-pressed")).toBe("true");
  await act(async () => ui.typeButton("Каталог").click());
  await act(async () => ui.typeButton("Обучение").click());
  expect(ui.typeButton("Обучение").getAttribute("aria-pressed")).toBe("false");
  expect(ui.typeButton("Каталог").getAttribute("aria-pressed")).toBe("true");
  await ui.click("Далее"); await ui.click("Далее");
  const modules = [...document.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')];
  expect(modules.map(input => input.checked)).toEqual([true, true, true, true]);
  expect(modules[1].disabled).toBe(false);
  await act(async () => modules[1].click());
  await ui.click("Назад"); await ui.click("Назад");
  await act(async () => ui.typeButton("Запись").click());
  await ui.click("Далее"); await ui.click("Далее");
  expect([...document.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')].map(input => input.checked)).toEqual([true, false, true, true]);
});

it("retains single-type selection for legacy parents without appTypes", async () => {
  const ui = await selectableWizard({ ...values, appType: "loyalty" });
  await act(async () => ui.typeButton("Каталог").click());
  expect(ui.typeButton("Каталог").getAttribute("aria-pressed")).toBe("true");
  expect(ui.typeButton("Лояльность").getAttribute("aria-pressed")).toBe("false");
});
