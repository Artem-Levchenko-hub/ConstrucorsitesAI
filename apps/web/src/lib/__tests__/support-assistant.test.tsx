import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { RouterContext } from "next/dist/shared/lib/router-context.shared-runtime";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { SupportAssistant } from "@/components/support/SupportAssistant";
import { findSupportAnswers, supportAnswers, supportTopics } from "@/lib/support-faq";

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
  const node = [...host.querySelectorAll<HTMLButtonElement>("button")].find(button => button.textContent?.replace("↗", "").trim() === text);
  expect(node, text).toBeTruthy(); await act(async () => node!.click());
}
async function ask(text: string) {
  await act(async () => {
    const field = host.querySelector<HTMLInputElement>('input[aria-label="Вопрос автопомощнику"]')!;
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(field, text);
    field.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => host.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
}

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
    await click(topic.title);
    const answer = supportAnswers.find(answer => answer.topic === topic.id)!;
    await click(answer.question);
    expect(host.querySelector('[role="log"]')?.textContent).toContain(answer.paragraphs[0]);
  }
  expect(fetch).not.toHaveBeenCalled(); expect(contact).not.toHaveBeenCalled();
  expect(host.querySelectorAll('[role="log"] > li')).toHaveLength(3);
});
it("lets the user disambiguate a typed question and hands the original question over only on demand", async () => {
  await mount(); await ask("ошибка правки");
  expect(host.querySelector('[role="log"]')?.textContent).toContain("Нашёл несколько");
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
    const field = host.querySelector<HTMLInputElement>("input")!;
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(field, "Мой отдельный вопрос");
    field.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await click("Не помогло — передать вопрос оператору");
  expect(contact).toHaveBeenCalledWith("Мой отдельный вопрос"); expect(fetch).not.toHaveBeenCalled();
});
