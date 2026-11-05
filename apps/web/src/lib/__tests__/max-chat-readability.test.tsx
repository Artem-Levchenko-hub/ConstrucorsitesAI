import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ChatMessage } from "@/components/workspace/ChatMessage";
import { Markdown } from "@/components/workspace/Markdown";
import type { Message } from "@/lib/api/types";

let root: Root;
let container: HTMLDivElement;
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
});
afterEach(() => { act(() => root.unmount()); container.remove(); vi.unstubAllGlobals(); });

it("keeps author identity, time, attachments and expansion in a MAX owner's message", async () => {
  const message: Message = {
    id: "user-message", project_id: "project", role: "user", snapshot_id: null,
    model_id: null, tokens_in: null, tokens_out: null, created_at: "2026-11-05T14:00:00Z",
    content: "Сделай описание приложения для клиентов.\n".repeat(30),
    reference_images: [{ id: "image", filename: "эскиз.png", mime_type: "image/png", size_bytes: 9, width: 1, height: 1, url: "" }],
  };
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("image", { headers: { "Content-Type": "image/png" } })));
  vi.stubGlobal("URL", class extends URL {
    static createObjectURL = () => "blob:offline-reference";
    static revokeObjectURL = () => {};
  });

  await act(async () => root.render(<ChatMessage message={message} presentation="studio" />));
  const entry = container.querySelector('[role="article"][aria-label="Ваше сообщение"]');
  expect(entry).not.toBeNull();
  expect(entry!.textContent).not.toContain("Вы");
  expect(entry!.querySelector(".lucide-user")).toBeNull();
  expect(entry!.querySelector("time")?.getAttribute("datetime")).toBe(message.created_at);
  expect(entry!.querySelector('a[aria-label="Открыть эскиз.png"]')?.getAttribute("href")).toBe("blob:offline-reference");
  const expand = entry!.querySelector<HTMLButtonElement>("button")!;
  expect(expand.textContent).toBe("Показать полностью");
  expect(expand.getAttribute("aria-expanded")).toBe("false");
  act(() => expand.click());
  expect(expand.textContent).toBe("Свернуть");
  expect(expand.getAttribute("aria-expanded")).toBe("true");
  act(() => expand.click());
  expect(expand.textContent).toBe("Показать полностью");
});

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
