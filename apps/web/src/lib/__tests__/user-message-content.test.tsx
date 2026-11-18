import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it } from "vitest";
import { ChatMessage } from "@/components/workspace/ChatMessage";
import { buildMaxProjectPrompt } from "@/lib/max-brief";
import type { Message } from "@/lib/api/types";

let root: Root;
let container: HTMLDivElement;
const task = "Получать баллы за покупку.\nБез корзины, сохранить слово <script> как текст.";
const prompt = buildMaxProjectPrompt({ name: "Кофе рядом", idea: task, appType: "loyalty",
  audience: "Гости", primaryAction: "Получить награду", features: ["Баллы и награды"], brandColors: "#b71c1c" });
const message = (content: string, id = "m1"): Message => Object.freeze({
  id, project_id: "p", role: "user", content, model_id: null, snapshot_id: null,
  tokens_in: null, tokens_out: null, created_at: "2026-11-18T00:00:00Z",
});

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
});
afterEach(() => { act(() => root.unmount()); container.remove(); });

it.each(["studio", "default"] as const)("shows name/task first and exposes exact source through native details in %s chat", presentation => {
  const original = message(prompt);
  act(() => root.render(<ChatMessage message={original} presentation={presentation} />));
  expect(container.querySelector("h3")?.textContent).toBe("Кофе рядом");
  const view = container.querySelector("[data-user-message-presentation]")!;
  expect(view.querySelector("div[id]")?.textContent).toBe(task);
  expect(view.querySelectorAll("summary").length).toBe(4);
  expect([...view.querySelectorAll("summary")].map(item => item.textContent)).toEqual([
    "Сценарий и функции", "Оформление", "Управление", "Полный исходный запрос",
  ]);
  expect([...view.querySelectorAll("details")].every(item => !item.open)).toBe(true);
  expect(view.querySelectorAll("script")).toHaveLength(0);
  const source = view.querySelector("[data-user-message-original]")!;
  expect(source.textContent).toBe(prompt);
  const summary = source.closest("details")!.querySelector("summary")!;
  act(() => { summary.focus(); summary.click(); });
  expect(document.activeElement).toBe(summary);
  expect(source.closest("details")!.open).toBe(true);
  expect(original.content).toBe(prompt);
  act(() => summary.click()); expect(source.closest("details")!.open).toBe(false);
});

it("shows parameters in labeled semantic blocks without truncating their values", () => {
  act(() => root.render(<ChatMessage message={message(prompt)} presentation="studio" />));
  const details = [...container.querySelectorAll("details")].find(item => item.querySelector("summary")?.textContent === "Сценарий и функции")!;
  act(() => details.querySelector("summary")!.click());
  expect(details.open).toBe(true);
  expect([...details.querySelectorAll("dt")].map(item => item.textContent)).toEqual(["Сценарий", "Аудитория", "Главное действие", "Возможности"]);
  expect([...details.querySelectorAll("dd")].map(item => item.textContent)).toEqual(["Лояльность", "Гости", "Получить награду", "Баллы и награды"]);
});

it("keeps exact nested wrapper sources available while presenting the innermost task", () => {
  const quiz = `${prompt}\n— Бриф из опроса —\n• Цвет: #abcdef (палитра: #abcdef)`;
  const outer = buildMaxProjectPrompt({ name: "Внешний бриф", idea: quiz, appType: "custom", audience: "", primaryAction: "", features: [], brandColors: "" });
  act(() => root.render(<ChatMessage message={message(outer)} presentation="studio" />));
  expect([...container.querySelectorAll("h3")].map(item => item.textContent)).toEqual(["Внешний бриф"]);
  expect(container.querySelector("h4")?.textContent).toBe("Кофе рядом");
  expect([...container.querySelectorAll("[data-user-message-original]")].map(item => item.textContent)).toEqual([outer]);
  expect(container.textContent).toContain("Параметры из вложенных описаний");
  expect(container.textContent).toContain("#abcdef (палитра: #abcdef)");
  expect(container.querySelector("div[id]")?.textContent).toBe(task);
});

it("keeps unknown freeform intact, with unique stable disclosure IDs for long messages", () => {
  const source = "Название продукта: это свободный текст.\n" + "длинноебезпробелов🎨".repeat(500) + "\nКОНЕЦ";
  const render = () => act(() => root.render(<>
    <ChatMessage key="first" message={message(source)} presentation="studio" />
    <ChatMessage key="second" message={message(source, "m2")} presentation="studio" />
  </>));
  render();
  expect(container.querySelector("[data-user-message-presentation]")).toBeNull();
  const buttons = [...container.querySelectorAll<HTMLButtonElement>("button[aria-controls]")];
  expect(buttons).toHaveLength(2);
  const ids = buttons.map(item => item.getAttribute("aria-controls")!);
  expect(new Set(ids).size).toBe(2);
  for (const id of ids) {
    const text = document.getElementById(id)!;
    expect(text.textContent).toBe(source);
    expect(text.className).toContain("[overflow-wrap:anywhere]");
  }
  act(() => buttons[0].click()); expect(buttons[0].getAttribute("aria-expanded")).toBe("true");
  expect(buttons[1].getAttribute("aria-expanded")).toBe("false");
  render(); expect([...container.querySelectorAll("button[aria-controls]")].map(item => item.getAttribute("aria-controls"))).toEqual(ids);
});

it("does not discard an unknown suffix after old quiz answers", () => {
  const source = "Идея\n— Бриф из опроса —\n• Тон: Спокойный\nВажное дополнительное условие.";
  act(() => root.render(<ChatMessage message={message(source)} presentation="studio" />));
  expect(container.querySelector("[data-user-message-presentation]")).toBeNull();
  expect(container.querySelector("div[id]")?.textContent).toBe(source);
});

it("exposes every original CRLF character while using readable normalized fields", () => {
  const source = prompt.replaceAll("\n", "\r\n");
  act(() => root.render(<ChatMessage message={message(source)} presentation="studio" />));
  expect(container.querySelector("div[id]")?.textContent).toBe(task);
  expect(container.querySelector("[data-user-message-original]")?.textContent).toBe(source);
});
