import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { RouterContext } from "next/dist/shared/lib/router-context.shared-runtime";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { SupportAssistant } from "@/components/support/SupportAssistant";
import { findSupportAnswers, searchSupportAnswers, relatedSupportAnswers, supportAnswers, supportTopics } from "@/lib/support-faq";

let root: Root, host: HTMLDivElement;
const contact = vi.fn();
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  contact.mockReset(); vi.stubGlobal("fetch", vi.fn(() => { throw new Error("FAQ must not call the network"); }));
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); vi.unstubAllGlobals(); });
async function mount() { await act(async () => root.render(<SupportAssistant onContact={contact} />)); }
async function click(text: string) {
  const node = [...host.querySelectorAll<HTMLButtonElement>("button")].find(button => button.textContent?.replace("↗", "").trim() === text || button.getAttribute("aria-label") === text);
  expect(node, text).toBeTruthy(); await act(async () => node!.click());
}
async function fillQuestion(text: string) {
  await act(async () => {
    const field = host.querySelector<HTMLTextAreaElement>('textarea[aria-label="Вопрос автопомощнику"]')!;
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(field, text);
    field.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
async function ask(text: string) { await fillQuestion(text); await act(async () => host.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }))); }

it.each([
  ["НЕ ПРИХОДИТ СМС КОД", "verification"],
  ["Я забыла пароль", "password"],
  ["Сколько стоит Pro?", "plans"],
  ["Оплата прошла, но тариф не появился", "payment"],
  ["Как отменить подписку?", "renewal"],
  ["Как создать приложение?", "create"],
  ["Генерация долго висит в очереди", "waiting"],
  ["Сбой 503", "generation-error"],
  ["В превью белый экран", "preview"],
  ["Как опубликовать приложение?", "publish"],
  ["Хочу связаться с оператором", "operator"],
])("finds grounded guidance for %s", (question, expected) => {
  expect(findSupportAnswers(question).map(answer => answer.id)).toEqual([expected]);
});
it("keeps unrelated questions unknown and presents genuine ties without choosing an answer", () => {
  expect(findSupportAnswers("")).toEqual([]);
  expect(findSupportAnswers("Где находится северный полюс?")).toEqual([]);
  expect(findSupportAnswers("problem")).toEqual([]);
  expect(findSupportAnswers("ошибка правки").map(answer => answer.id)).toEqual(["create", "generation-error"]);
});
it("offers typos and partial words as choices while automatic answers remain exact", () => {
  expect(searchSupportAnswers("аплата")[0]?.id).toBe("payment");
  expect(searchSupportAnswers("гнеерация")[0]?.id).toBe("waiting");
  expect(searchSupportAnswers("опл")[0]?.id).toBe("payment");
  expect(findSupportAnswers("аплата")).toEqual([]);
  expect(findSupportAnswers("5030")).toEqual([]);
  const plans = supportAnswers.find(answer => answer.id === "plans")!;
  expect(relatedSupportAnswers(plans).map(answer => answer.id)).toEqual(["payment", "renewal"]);
});
it("shows live suggestions without sending, and confirms a fuzzy match before displaying advice", async () => {
  await mount(); await fillQuestion("аплата");
  expect(host.querySelector('[aria-label="Подходящие вопросы"]')?.textContent).toContain("Оплата прошла");
  expect(host.querySelector('[role="log"]')).toBeNull();
  await act(async () => host.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
  expect(host.querySelector('[role="log"]')?.textContent).toContain("Выберите");
  expect(host.querySelector('[role="log"]')?.textContent).not.toContain("Откройте «Операции»");
  await click("Оплата прошла, а тариф или пакет не появился");
  expect(host.querySelector('[role="log"]')?.textContent).toContain("Откройте «Операции»");
  expect(fetch).not.toHaveBeenCalled();
});
it("keeps normal multiline caret editing and supports the explicit suggestion shortcut", async () => {
  await mount(); await fillQuestion("Генерация\nдолго выполняется");
  const field = host.querySelector("textarea")!;
  field.focus(); field.setSelectionRange(14, 14);
  const move = new KeyboardEvent("keydown", { key: "ArrowUp", bubbles: true, cancelable: true });
  await act(async () => field.dispatchEvent(move));
  expect(move.defaultPrevented).toBe(false); expect(document.activeElement).toBe(field);
  field.setSelectionRange(0, 0);
  const select = new KeyboardEvent("keydown", { key: "ArrowUp", bubbles: true, cancelable: true });
  await act(async () => field.dispatchEvent(select));
  expect(select.defaultPrevented).toBe(true);
  expect(document.activeElement).toBe(host.querySelector(".support-suggestions button"));
  const escape = new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true });
  await act(async () => document.activeElement!.dispatchEvent(escape));
  expect(document.activeElement).toBe(field); expect(field.value).toBe("Генерация\nдолго выполняется");
});
it("preserves Shift+Enter and IME composition while Enter sends exactly once", async () => {
  await mount(); await fillQuestion("Не приходит код");
  const field = host.querySelector("textarea")!;
  await act(async () => field.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", shiftKey: true, bubbles: true, cancelable: true })));
  expect(host.querySelector('[role="log"]')).toBeNull();
  await act(async () => field.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", isComposing: true, bubbles: true, cancelable: true })));
  expect(host.querySelector('[role="log"]')).toBeNull();
  await act(async () => field.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true })));
  expect(host.querySelectorAll(".support-exchange")).toHaveLength(1); expect(field.value).toBe("");
});
it("copies only the public answer and keeps helpfulness feedback local", async () => {
  const writeText = vi.fn(async () => {});
  const fixtureNavigator = Object.create(navigator);
  Object.defineProperty(fixtureNavigator, "clipboard", { value: { writeText }, configurable: true });
  vi.stubGlobal("navigator", fixtureNavigator);
  await mount(); await ask("Не приходит код");
  await click("Скопировать ответ");
  expect(writeText).toHaveBeenCalledWith(supportAnswers.find(answer => answer.id === "verification")!.paragraphs.join("\n\n"));
  expect(host.textContent).toContain("Ответ скопирован");
  await click("Ответ помог"); expect(host.textContent).toContain("Рады помочь");
  await click("Ответ не помог"); expect(host.textContent).toContain("Давайте подключим");
  expect(contact).not.toHaveBeenCalled();
  await click("Написать по этому вопросу");
  expect(contact).toHaveBeenCalledWith("Не приходит код"); expect(fetch).not.toHaveBeenCalled();
});
it("reports clipboard rejection instead of claiming success", async () => {
  const fixtureNavigator = Object.create(navigator);
  Object.defineProperty(fixtureNavigator, "clipboard", { value: { writeText: vi.fn(async () => { throw new Error("denied"); }) }, configurable: true });
  vi.stubGlobal("navigator", fixtureNavigator);
  await mount(); await ask("Не приходит код"); await click("Скопировать ответ");
  expect(host.textContent).toContain("Не удалось скопировать");
  expect(host.textContent).not.toContain("Ответ скопирован");
});
it("retains a bounded conversation and can start again without requests", async () => {
  await mount();
  for (let index = 0; index < 23; index++) await ask("Вопрос " + index + " про код");
  expect(host.querySelectorAll(".support-exchange")).toHaveLength(20);
  expect(host.querySelector(".support-exchange")?.textContent).toContain("Вопрос 3 про код");
  await click("Новый диалог");
  expect(host.querySelector('[role="log"]')).toBeNull();
  expect(host.querySelector("textarea")?.value).toBe(""); expect(fetch).not.toHaveBeenCalled();
});
it("covers every topic with unique public answers and safe local navigation", () => {
  expect(new Set(supportAnswers.map(answer => answer.id)).size).toBe(supportAnswers.length);
  expect(supportAnswers.find(answer => answer.id === "password")?.links).toEqual([{ href: "/forgot-password", label: "Восстановить пароль" }]);
  for (const topic of supportTopics) expect(supportAnswers.some(answer => answer.topic === topic.id)).toBe(true);
  for (const answer of supportAnswers) for (const link of answer.links ?? []) {
    expect(link.href).toMatch(/^\/(?!\/)/); expect(link.href).not.toContain("?");
  }
});
it("uses client navigation for help links so the global support session is not reloaded", async () => {
  // Vitest resolves Next's package entry; the Next app build aliases Link to its app-router entry.
  const router = {
    route: "/", pathname: "/", asPath: "/", query: {}, basePath: "",
    isLocaleDomain: false, isFallback: false, isReady: true, isPreview: false,
    back: vi.fn(), forward: vi.fn(), reload: vi.fn(), beforePopState: vi.fn(),
    push: vi.fn(async () => true), replace: vi.fn(async () => true), prefetch: vi.fn(async () => {}),
    events: { on: vi.fn(), off: vi.fn(), emit: vi.fn() },
  };
  await act(async () => root.render(<RouterContext.Provider value={router}><SupportAssistant onContact={contact} /></RouterContext.Provider>));
  await click("Тарифы и оплата"); await click("Где посмотреть тарифы и доступные правки?");
  const link = host.querySelector<HTMLAnchorElement>('a[href="/billing/plan"]')!;
  const event = new MouseEvent("click", { bubbles: true, cancelable: true, button: 0 });
  await act(async () => link.dispatchEvent(event));
  expect(event.defaultPrevented).toBe(true);
  expect(router.push).toHaveBeenCalledTimes(1);
  expect(router.push).toHaveBeenCalledWith("/billing/plan", "/billing/plan", expect.any(Object));
  expect(router.prefetch).not.toHaveBeenCalled(); expect(fetch).not.toHaveBeenCalled();
});
it("answers a selected question in each topic without a network request or an operator submission", async () => {
  await mount();
  for (const topic of supportTopics) {
    await click("Все темы"); await click(topic.title);
    const answer = supportAnswers.find(answer => answer.topic === topic.id)!;
    await click(answer.question);
    expect(host.querySelector('[role="log"]')?.textContent).toContain(answer.paragraphs[0]);
  }
  expect(fetch).not.toHaveBeenCalled(); expect(contact).not.toHaveBeenCalled();
  expect(host.querySelectorAll('[role="log"] > li')).toHaveLength(5);
});
it("lets the user disambiguate a typed question and hands the original question over only on demand", async () => {
  await mount(); await ask("ошибка правки");
  expect(host.querySelector('[role="log"]')?.textContent).toContain("Выберите");
  const choices = host.querySelectorAll<HTMLButtonElement>(".support-bot-answer button");
  await act(async () => choices[1].click());
  expect(host.querySelector('[role="log"] > li:last-child')?.textContent).toContain("Сохраните точный текст ошибки");
  expect(contact).not.toHaveBeenCalled();
  await click("Не помогло — передать вопрос оператору");
  expect(contact).toHaveBeenCalledWith("ошибка правки"); expect(fetch).not.toHaveBeenCalled();
});
it("does not invent an answer or render submitted markup, and can hand over the unsent question", async () => {
  await mount(); await ask("<img src=x onerror=alert(1)> Где северный полюс?");
  expect(host.querySelector('[role="log"]')?.textContent).toContain("пока нет точного совпадения");
  expect(host.querySelector("img")).toBeNull();
  await act(async () => {
    const field = host.querySelector<HTMLTextAreaElement>("textarea")!;
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(field, "Мой отдельный вопрос");
    field.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await click("Не помогло — передать вопрос оператору");
  expect(contact).toHaveBeenCalledWith("Мой отдельный вопрос"); expect(fetch).not.toHaveBeenCalled();
});
