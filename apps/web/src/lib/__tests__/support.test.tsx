import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { SupportWidget } from "@/components/support/SupportWidget";
import { toast } from "sonner";
import { AdminControlCenter } from "@/components/account/AdminControlCenter";

vi.mock("next/navigation", () => ({ usePathname: () => window.location.pathname }));
vi.mock("sonner", () => ({ toast: { message: vi.fn() } }));
const ticket = { id: "11111111-1111-4111-8111-111111111111", reference: "SUP-101", subject: "Не работает публикация", status: "new", version: 1, requester_email: "guest@example.test", created_at: "2026-10-26T10:00:00Z", updated_at: "2026-10-26T10:00:00Z", resolved_at: null, archived: false, source_path: "/max", assigned_admin_email: null, message_count: 1, last_message_preview: "Помогите", messages: [{ id: "message1", author: "customer", body: "Помогите", created_at: "2026-10-26T10:00:00Z" }] };
class TestSocket {
  static OPEN = 1; static instances: TestSocket[] = []; readyState = 1; onopen: (() => void) | null = null; onmessage: ((event: { data: string }) => void) | null = null; onclose: ((event: { code: number }) => void) | null = null;
  constructor() { TestSocket.instances.push(this); } close = vi.fn(); send() {}
  event(payload: unknown) { this.onmessage?.({ data: JSON.stringify(payload) }); }
}
let root: Root, host: HTMLDivElement, client: QueryClient;
let identity: { user_id: string | null; email: string | null; is_admin: boolean };
let posts: Record<string, unknown>[], failPost: boolean, paths: string[];
let patches: Record<string, unknown>[], conflict: boolean, newCount: number, failContext: boolean;
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  localStorage.clear(); sessionStorage.clear(); window.history.replaceState({}, "", "/max");
  identity = { user_id: null, email: null, is_admin: false }; posts = []; paths = []; failPost = false; patches = []; conflict = false; failContext = false; newCount = 1; TestSocket.instances = []; vi.clearAllMocks();
  vi.stubGlobal("WebSocket", TestSocket);
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const parsed = new URL(url, window.location.origin); paths.push(parsed.pathname + parsed.search);
    if (parsed.pathname === "/api/support/context") { if (failContext) throw new Error("Context temporarily unavailable"); return Response.json(identity); }
    if (init?.method === "PATCH") { patches.push(JSON.parse(String(init.body))); return conflict ? Response.json({ error: { code: "conflict", message: "Changed" } }, { status: 409 }) : Response.json({ ...ticket, ...patches.at(-1), version: 2 }); }
    if (init?.method === "POST") { posts.push(JSON.parse(String(init.body))); if (failPost) throw new Error("offline"); return Response.json(ticket); }
    if (parsed.pathname.endsWith(ticket.id)) return Response.json(ticket);
    if (parsed.pathname.includes("support/tickets")) return Response.json({ items: identity.user_id ? [ticket] : [], total: identity.user_id ? 1 : 0, new_count: newCount });
    return Response.json([]);
  }));
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); host.remove(); vi.unstubAllGlobals(); });
const wait = async (check: () => void) => { await act(async () => { await vi.waitFor(check); }); };
async function mount(admin = false) { await act(async () => root.render(<QueryClientProvider client={client}>{admin ? <AdminControlCenter currentEmail="admin@example.test" /> : <SupportWidget />}</QueryClientProvider>)); }
async function click(label: string) { await wait(() => expect([...document.querySelectorAll<HTMLButtonElement>("button")].some(node => node.textContent?.trim() === label || node.getAttribute("aria-label") === label)).toBe(true)); const button = [...document.querySelectorAll<HTMLButtonElement>("button")].find(node => node.textContent?.trim() === label || node.getAttribute("aria-label") === label); expect(button).toBeTruthy(); await act(async () => button!.click()); }
async function fill(label: string, value: string) { await wait(() => expect(document.querySelector(`[aria-label="${label}"]`)).not.toBeNull()); const el = document.querySelector<HTMLInputElement | HTMLTextAreaElement>(`[aria-label="${label}"]`)!; expect(el).toBeTruthy(); await act(async () => { Object.getOwnPropertyDescriptor(el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype, "value")!.set!.call(el, value); el.dispatchEvent(new Event("input", { bubbles: true })); }); }
async function open() { await mount(); await click("Открыть поддержку"); await wait(() => expect(document.querySelector('[aria-label="Тема обращения"]')).not.toBeNull()); }
async function submit() { await act(async () => document.querySelector<HTMLFormElement>(".support-form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }))); }
it("keeps a failed guest draft and retries the same capability/request without acknowledging early", async () => {
  await open(); await fill("Тема обращения", ticket.subject); await fill("Сообщение", "Помогите"); await fill("Ваш email", "guest@example.test");
  await act(async () => document.querySelector<HTMLInputElement>('input[type="checkbox"]')!.click());
  failPost = true; await submit(); await wait(() => expect(posts).toHaveLength(1));
  expect(document.body.textContent).not.toContain(ticket.reference);
  expect((document.querySelector('[aria-label="Сообщение"]') as HTMLTextAreaElement).value).toBe("Помогите");
  expect(posts[0].guest_token).toMatch(/^[a-f0-9]{64}$/);
  failPost = false; await submit(); await wait(() => expect(document.body.textContent).toContain(ticket.reference));
  expect(posts[1]).toEqual(posts[0]); expect(window.location.href).not.toContain(String(posts[0].guest_token));
});
it("uses the registered session for submission and renders messages as text", async () => {
  identity = { user_id: "owner1", email: "owner@example.test", is_admin: false };
  await open(); expect(document.querySelector('[aria-label="Ваш email"]')).toBeNull();
  await fill("Тема обращения", "Проблема"); await fill("Сообщение", "Текст"); await submit();
  await wait(() => expect(document.body.textContent).toContain(ticket.reference));
  expect(posts[0]).not.toHaveProperty("guest_token"); expect(posts[0]).not.toHaveProperty("email");
  await fill("Ответ", "<img src=x onerror=alert(1)>"); await click("Отправить ответ");
  await wait(() => expect(posts).toHaveLength(2)); expect(posts[1].message).toBe("<img src=x onerror=alert(1)>"); expect(document.querySelector(".support-thread img")).toBeNull();
});
it("clears a private conversation when the authenticated account changes", async () => {
  identity = { user_id: "owner1", email: "owner@example.test", is_admin: false }; await open();
  await click(`${ticket.reference} · ${ticket.subject}`); await wait(() => expect(document.body.textContent).toContain("Помогите"));
  identity = { user_id: "owner2", email: "second@example.test", is_admin: false };
  await act(async () => window.dispatchEvent(new Event("focus")));
  await wait(() => expect(document.querySelector(".support-thread")).toBeNull());
});
it("opens an admin mail deep link and preserves unrelated URL params when navigating to archive", async () => {
  identity = { user_id: "admin1", email: "admin@example.test", is_admin: true };
  window.history.replaceState({}, "", `/admin/max?tab=support&ticket=${ticket.id}&keep=yes`); await mount(true);
  await wait(() => expect(document.body.textContent).toContain("Помогите"));
  expect(document.querySelector('[role="tab"][aria-selected="true"]')?.textContent).toContain("Поддержка");
  await click("Настройки"); await click("Архив поддержки");
  await wait(() => expect(paths.some(path => path.includes("archived=true"))).toBe(true));
  expect(new URLSearchParams(window.location.search).get("keep")).toBe("yes");
  expect(new URLSearchParams(window.location.search).get("section")).toBe("support-archive");
});
it("restores an ambiguous guest submission after a browser remount with the original id", async () => {
  await open(); await fill("Тема обращения", "Проблема"); await fill("Сообщение", "Не терять черновик"); await fill("Ваш email", "guest@example.test");
  await act(async () => document.querySelector<HTMLInputElement>('input[type="checkbox"]')!.click());
  failPost = true; await submit(); await wait(() => expect(posts).toHaveLength(1));
  await act(async () => root.unmount()); client.clear(); root = createRoot(host);
  await mount(); await click("Открыть поддержку"); await wait(() => expect(document.querySelector('[aria-label="Сообщение"]')).not.toBeNull());
  expect((document.querySelector('[aria-label="Сообщение"]') as HTMLTextAreaElement).value).toBe("Не терять черновик");
  failPost = false; await submit(); await wait(() => expect(posts).toHaveLength(2)); expect(posts[1]).toEqual(posts[0]);
});
it("keeps a reply draft while identity is rechecked and restores it after closing the conversation", async () => {
  identity = { user_id: "owner1", email: "owner@example.test", is_admin: false }; await open(); await click(`${ticket.reference} · ${ticket.subject}`);
  await wait(() => expect(document.querySelector('[aria-label="Ответ"]')).not.toBeNull()); await fill("Ответ", "Нужна помощь");
  failPost = true; await click("Отправить ответ"); await wait(() => expect(posts).toHaveLength(1));
  await act(async () => window.dispatchEvent(new Event("focus")));
  await wait(() => expect((document.querySelector('[aria-label="Ответ"]') as HTMLTextAreaElement)?.value).toBe("Нужна помощь"));
  await click("← Все обращения"); await click(`${ticket.reference} · ${ticket.subject}`);
  await wait(() => expect((document.querySelector('[aria-label="Ответ"]') as HTMLTextAreaElement)?.value).toBe("Нужна помощь"));
  failPost = false; await click("Отправить ответ"); await wait(() => expect(posts).toHaveLength(2)); expect(posts[1]).toEqual(posts[0]);
});
it("honours popstate and removes the admin thread after role revocation", async () => {
  identity = { user_id: "admin1", email: "admin@example.test", is_admin: true }; await mount(true);
  await act(async () => { window.history.pushState({}, "", `/admin/max?tab=support&ticket=${ticket.id}`); window.dispatchEvent(new PopStateEvent("popstate")); });
  await wait(() => expect(document.querySelector(".support-thread")).not.toBeNull());
  identity = { ...identity, is_admin: false }; await act(async () => { await client.invalidateQueries({ queryKey: ["support-context"] }); });
  await wait(() => expect(document.querySelector(".support-thread")).toBeNull());
  expect(document.body.textContent).not.toContain("Помогите");
});


it("shows each admin customer notification once and refetches the authoritative count", async () => {
  identity = { user_id: "admin1", email: "admin@example.test", is_admin: true }; await mount();
  await wait(() => expect(document.querySelector('.support-badge')?.textContent).toBe("1"));
  const socket = TestSocket.instances[0]; expect(socket).toBeTruthy();
  const event = { type: "support.changed", ticket_id: ticket.id, version: 2, reason: "message", author: "customer" };
  newCount = 2; await act(async () => { socket.event(event); socket.event(event); });
  await wait(() => expect(document.querySelector('.support-badge')?.textContent).toBe("2"));
  expect(toast.message).toHaveBeenCalledTimes(1);
  await act(async () => socket.event({ ...event, version: 3, author: "support" })); expect(toast.message).toHaveBeenCalledTimes(1);
});
it("recovers a stale admin status without losing the reply draft", async () => {
  identity = { user_id: "admin1", email: "admin@example.test", is_admin: true }; window.history.replaceState({}, "", `/admin/max?tab=support&ticket=${ticket.id}`); await mount(true);
  await fill("Ответ", "Проверяем ваш случай"); conflict = true; await click("Готово");
  await wait(() => expect(document.body.textContent).toContain("Обращение уже изменилось"));
  expect(patches).toEqual([{ status: "done", expected_version: 1 }]);
  expect((document.querySelector('[aria-label="Ответ"]') as HTMLTextAreaElement).value).toBe("Проверяем ваш случай");
});
it("scopes the portal theme, closes with Escape and restores launcher focus", async () => {
  await open(); expect(document.querySelector('[role="dialog"]')?.hasAttribute("data-max-studio")).toBe(true);
  await fill("Тема обращения", "Сохранить тему");
  await act(async () => document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
  await wait(() => expect(document.querySelector('[role="dialog"]')).toBeNull());
  await wait(() => expect(document.activeElement?.getAttribute("aria-label")).toBe("Открыть поддержку"));
  await click("Открыть поддержку"); expect((document.querySelector('[aria-label="Тема обращения"]') as HTMLInputElement).value).toBe("Сохранить тему");
});
it("clears guest capability and reply drafts on an already authenticated mount after OAuth reload", async () => {
  sessionStorage.setItem("yleum-support-pending", JSON.stringify({ scope: "guest", draft: { request_id: "request1", subject: "Private guest subject", message: "Private guest message", guest_token: "a".repeat(64) } }));
  sessionStorage.setItem("yleum-support-reply", JSON.stringify({ scope: "guest", ticketId: ticket.id, draft: { request_id: "reply1", message: "Private guest reply" } }));
  identity = { user_id: "owner1", email: "owner@example.test", is_admin: false }; await mount();
  await wait(() => expect(client.getQueryData(["support-context"])).toEqual(identity));
  expect(sessionStorage.getItem("yleum-support-pending")).toBeNull();
  expect(sessionStorage.getItem("yleum-support-reply")).toBeNull();
  await act(async () => root.unmount()); client.clear(); root = createRoot(host);
  identity = { user_id: null, email: null, is_admin: false }; await open();
  expect((document.querySelector('[aria-label="Тема обращения"]') as HTMLInputElement).value).toBe("");
});
it("preserves the signed-in user's own creation and reply drafts on a full reload", async () => {
  sessionStorage.setItem("yleum-support-pending", JSON.stringify({ scope: "owner1", draft: { request_id: "request1", subject: "My subject", message: "My message" } }));
  sessionStorage.setItem("yleum-support-reply", JSON.stringify({ scope: "owner1", ticketId: ticket.id, draft: { request_id: "reply1", message: "My reply" } }));
  identity = { user_id: "owner1", email: "owner@example.test", is_admin: false }; await open();
  expect((document.querySelector('[aria-label="Тема обращения"]') as HTMLInputElement).value).toBe("My subject");
  await click(`${ticket.reference} · ${ticket.subject}`); await wait(() => expect(document.querySelector('[aria-label="Ответ"]')).not.toBeNull());
  expect((document.querySelector('[aria-label="Ответ"]') as HTMLTextAreaElement).value).toBe("My reply");
});
it("revalidates the same authenticated identity after a policy close and restores realtime", async () => {
  identity = { user_id: "owner1", email: "owner@example.test", is_admin: false }; await mount();
  await wait(() => expect(TestSocket.instances).toHaveLength(1));
  const before = paths.filter(path => path === "/api/support/context").length;
  await act(async () => TestSocket.instances[0].onclose?.({ code: 1008 }));
  await wait(() => expect(paths.filter(path => path === "/api/support/context").length).toBeGreaterThan(before));
  await act(async () => { await vi.waitFor(() => expect(TestSocket.instances).toHaveLength(2), { timeout: 4_000 }); });
});

it("retains an unsubmitted draft across a failed context refresh while hiding private UI and stopping updates", async () => {
  identity = { user_id: "owner1", email: "owner@example.test", is_admin: false }; await open();
  await fill("Тема обращения", "Неотправленная тема"); await fill("Сообщение", "Неотправленное сообщение");
  const socket = TestSocket.instances[0];
  failContext = true;
  await act(async () => { await client.invalidateQueries({ queryKey: ["support-context"] }); });
  await wait(() => expect(document.body.textContent).toContain("Не удалось подключиться"));
  expect(document.querySelector('[aria-label="Тема обращения"]')).toBeNull();
  expect(document.body.textContent).not.toContain(ticket.subject);
  expect(socket.close).toHaveBeenCalledTimes(1);
  const historyReads = paths.filter(path => path.startsWith("/api/support/tickets?")).length;
  await act(async () => { await client.invalidateQueries({ queryKey: ["support"] }); });
  expect(paths.filter(path => path.startsWith("/api/support/tickets?"))).toHaveLength(historyReads);
  failContext = false; await click("Повторить");
  await wait(() => expect(document.querySelector('[aria-label="Тема обращения"]')).not.toBeNull());
  expect((document.querySelector('[aria-label="Тема обращения"]') as HTMLInputElement).value).toBe("Неотправленная тема");
  expect((document.querySelector('[aria-label="Сообщение"]') as HTMLTextAreaElement).value).toBe("Неотправленное сообщение");
  expect(posts).toHaveLength(0);
  expect(sessionStorage.getItem("yleum-support-pending")).toBeNull();
});
it("discards the previous principal's unsubmitted inputs when identity changes after a context outage", async () => {
  identity = { user_id: "owner1", email: "owner@example.test", is_admin: false }; await open();
  await fill("Тема обращения", "Private owner1 subject"); await fill("Сообщение", "Private owner1 message");
  failContext = true;
  await act(async () => { await client.invalidateQueries({ queryKey: ["support-context"] }); });
  await wait(() => expect(document.querySelector('[aria-label="Тема обращения"]')).toBeNull());
  identity = { user_id: "owner2", email: "second@example.test", is_admin: false };
  failContext = false; await click("Повторить");
  await wait(() => expect(document.querySelector('[aria-label="Тема обращения"]')).not.toBeNull());
  expect((document.querySelector('[aria-label="Тема обращения"]') as HTMLInputElement).value).toBe("");
  expect((document.querySelector('[aria-label="Сообщение"]') as HTMLTextAreaElement).value).toBe("");
  expect(document.body.textContent).not.toContain("Private owner1");
});
