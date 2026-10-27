import { act, StrictMode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, afterEach, expect, it, vi } from "vitest";
import { AccountProfile } from "@/components/account/AccountProfile";
import { AccountSecurity } from "@/components/account/AccountSecurity";

const navigation = vi.hoisted(() => ({ replace: vi.fn(), refresh: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => navigation }));
let container: HTMLDivElement, root: Root, client: QueryClient;
let hasPassword: boolean;
let post: (path: string, body: Record<string, string>) => Promise<Response>;
const requests: { path: string; body: Record<string, string> }[] = [];
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  navigation.replace.mockReset(); navigation.refresh.mockReset(); requests.length = 0; hasPassword = true;
  post = async () => Response.json({ ok: true, reauthenticate: true });
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const path = new URL(url, window.location.origin).pathname;
    if (init?.method === "POST") {
      const body = JSON.parse(String(init.body)); requests.push({ path, body }); return post(path, body);
    }
    if (path === "/api/account/credentials") return Response.json({ has_password: hasPassword });
    if (path === "/api/auth/sessions") return Response.json([]);
    return new Response(null, { status: 204 });
  }));
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); container.remove(); vi.unstubAllGlobals(); });
async function render(view: "profile" | "security", email = "first@example.test") {
  await act(async () => root.render(<StrictMode><QueryClientProvider client={client}>{view === "profile" ? <AccountProfile email={email} /> : <AccountSecurity email={email} />}</QueryClientProvider></StrictMode>));
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
}
function fill(id: string, value: string) {
  const input = container.querySelector<HTMLInputElement>(`#${id}`);
  expect(input, `Missing field ${id}`).not.toBeNull();
  input!.value = value;
}
async function submit() { await act(async () => container.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }))); }

it("rejects mismatched new passwords without submitting credentials", async () => {
  await render("security"); fill("account-current-password", "test-current-secret"); fill("account-new-password", "test-new-secret1"); fill("account-confirm-password", "test-other-secret2");
  await submit(); expect(container.querySelector('[role="alert"]')?.textContent).toContain("не совпадают"); expect(requests.length).toBe(0);
});
it("rejects a new password without a digit before making a request", async () => {
  await render("security"); fill("account-current-password", "test-current-secret"); fill("account-new-password", "test-no-digit-secret"); fill("account-confirm-password", "test-no-digit-secret");
  await submit();
  expect(requests.length).toBe(0);
  expect(container.querySelector('[role="alert"]')?.textContent).toContain("хотя бы одну цифру");
  expect(navigation.replace).not.toHaveBeenCalled();
});
it("reports a wrong current password and permits correction", async () => {
  post = async () => Response.json({ error: { code: "bad_request", message: "Неверный текущий пароль" } }, { status: 400 });
  await render("security"); fill("account-current-password", "test-current-secret"); fill("account-new-password", "test-new-secret1"); fill("account-confirm-password", "test-new-secret1");
  await submit(); expect(container.querySelector('[role="alert"]')?.textContent).toContain("Неверный текущий пароль"); expect(navigation.replace).not.toHaveBeenCalled();
  expect(container.querySelector<HTMLButtonElement>('button[type="submit"]')?.disabled).toBe(false);
});
it("clears private query data and returns to login after password replacement", async () => {
  client.setQueryData(["private-account"], { private: true });
  await render("security"); fill("account-current-password", "test-current-secret"); fill("account-new-password", "test-new-secret1"); fill("account-confirm-password", "test-new-secret1"); await submit();
  expect(requests[0].path).toBe("/api/account/credentials/password"); expect(Object.keys(requests[0].body).sort()).toEqual(["current_password", "new_password"]);
  expect(client.getQueryData(["private-account"])).toBeUndefined(); expect(navigation.replace).toHaveBeenCalledWith("/login");
});
it.each(["profile", "security"] as const)("routes an OAuth-only %s user through password recovery", async view => {
  hasPassword = false; await render(view);
  expect(container.querySelector('a[href="/forgot-password"]')).not.toBeNull(); expect(container.querySelector('input[type="password"]')).toBeNull();
});
it("keeps the current login and explains confirmation of both mailboxes", async () => {
  post = async () => Response.json({ status: "confirmation_sent" }, { status: 202 });
  await render("profile"); fill("account-new-email", "NEXT@example.test"); fill("account-email-password", "test-current-secret"); await submit();
  expect(requests[0].path).toBe("/api/account/credentials/email"); expect(requests[0].body.new_email).toBe("NEXT@example.test");
  expect(container.querySelector<HTMLInputElement>("#account-email")?.value).toBe("first@example.test");
  expect(container.querySelector('[role="status"]')?.textContent).toContain("оба"); expect(navigation.replace).not.toHaveBeenCalled();
});
it("does not redirect a different account after an old pending password change completes", async () => {
  let finish!: (response: Response) => void; post = () => new Promise(resolve => { finish = resolve; });
  await render("security"); fill("account-current-password", "test-current-secret"); fill("account-new-password", "test-new-secret1"); fill("account-confirm-password", "test-new-secret1"); await submit();
  await render("security", "second@example.test");
  await act(async () => finish(Response.json({ ok: true, reauthenticate: true })));
  expect(navigation.replace).not.toHaveBeenCalled(); expect(container.querySelector<HTMLInputElement>("#account-current-password")?.value).toBe("");
});
it("prevents duplicate password requests and keeps secrets out of the mutation cache", async () => {
  let finish!: (response: Response) => void; post = () => new Promise(resolve => { finish = resolve; });
  await render("security"); fill("account-current-password", "test-current-secret"); fill("account-new-password", "test-new-secret1"); fill("account-confirm-password", "test-new-secret1");
  await submit(); await submit();
  expect(requests.length).toBe(1); expect(container.querySelector<HTMLButtonElement>('button[type="submit"]')?.disabled).toBe(true);
  expect(client.getMutationCache().getAll().length).toBe(0);
  await act(async () => finish(Response.json({ ok: true, reauthenticate: true })));
});
it("does not report old email-change success in a newly selected account", async () => {
  let finish!: (response: Response) => void; post = () => new Promise(resolve => { finish = resolve; });
  await render("profile"); fill("account-new-email", "next@example.test"); fill("account-email-password", "test-current-secret"); await submit();
  await render("profile", "second@example.test");
  await act(async () => finish(Response.json({ status: "confirmation_sent" }, { status: 202 })));
  expect(container.textContent).not.toContain("Письма отправлены");
  expect(container.querySelector<HTMLInputElement>("#account-new-email")?.value).toBe("");
});
it("shows mail delivery failure without claiming the address changed", async () => {
  post = async () => Response.json({ error: { code: "email_delivery_unavailable", message: "Не удалось отправить оба подтверждения" } }, { status: 503 });
  await render("profile"); fill("account-new-email", "next@example.test"); fill("account-email-password", "test-current-secret"); await submit();
  expect(container.querySelector('[role="alert"]')?.textContent).toContain("Не удалось отправить");
  expect(container.textContent).not.toContain("Письма отправлены");
  expect(container.querySelector<HTMLInputElement>("#account-email")?.value).toBe("first@example.test");
});
