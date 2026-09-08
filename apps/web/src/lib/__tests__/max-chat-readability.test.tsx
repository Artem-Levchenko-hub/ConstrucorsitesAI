import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it } from "vitest";
import { Markdown } from "@/components/workspace/Markdown";

let root: Root;
let container: HTMLDivElement;
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
});
afterEach(() => { act(() => root.unmount()); container.remove(); });

it("folds technical implementation lists while keeping the result and required actions visible", () => {
  act(() => root.render(createElement(Markdown, { collapseTechnical: true,
    text: "Добавлен дневник питания.\n\n- `src/app/page.tsx` — навигация.\n- `store.ts` — сохранение записей.\n\nПроверьте добавление завтрака в превью.",
  })));
  const details = container.querySelector("details")!;
  expect(details).not.toBeNull();
  expect(details.open).toBe(false);
  expect(details.textContent).toContain("store.ts");
  expect(details.textContent).not.toContain("Проверьте добавление");
  expect(container.querySelector("p")?.textContent).toBe("Добавлен дневник питания.");
});

it("does not hide errors and limitations inside technical details", () => {
  act(() => root.render(createElement(Markdown, { collapseTechnical: true,
    text: "- `src/app/page.tsx` — сборка не прошла, исправьте ошибку.\n- `store.ts` — не удалось сохранить изменения.",
  })));
  expect(container.querySelector("details")).toBeNull();
  expect(container.textContent).toContain("не удалось сохранить");
});

it("keeps ordinary workspace code expanded", () => {
  act(() => root.render(createElement(Markdown, { text: "```js\nconst count = 1;\n```" })));
  expect(container.querySelector("details")).toBeNull();
  expect(container.querySelector("pre")?.textContent).toContain("const count");
});

it("never hides an action list merely because it mentions source files", () => {
  act(() => root.render(createElement(Markdown, { collapseTechnical: true,
    text: "Перед публикацией выполните два шага:\n\n- Укажите название организации в `src/legal.ts`.\n- Заполните обязательные реквизиты в `src/operator.ts`.",
  })));
  expect(container.querySelector("details")).toBeNull();
  expect(container.querySelector("ul")?.textContent).toContain("Заполните обязательные реквизиты");
});
