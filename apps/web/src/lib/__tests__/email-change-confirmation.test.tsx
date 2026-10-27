import { act, StrictMode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { beforeEach, afterEach, expect, it, vi } from "vitest";
import { EmailChangeConfirmation } from "@/app/(auth)/confirm-email-change/EmailChangeConfirmation";

let container: HTMLDivElement, root: Root;
const capability = "test-only-email-capability-not-a-real-token";
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  window.history.replaceState({}, "", `/confirm-email-change#token=${capability}`);
  localStorage.clear(); sessionStorage.clear();
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ status: "pending", reauthenticate: false })));
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.unstubAllGlobals(); window.history.replaceState({}, "", "/"); });
async function mount() { await act(async () => root.render(<StrictMode><EmailChangeConfirmation /></StrictMode>)); }
async function confirm() { await act(async () => container.querySelector<HTMLButtonElement>("button")!.click()); }

it("strips the capability from the URL and waits for an explicit action, including strict effect replay", async () => {
  await mount();
  expect(window.location.hash.length === 0).toBe(true); expect(fetch).not.toHaveBeenCalled();
  expect(container.innerHTML.includes(capability)).toBe(false); expect(localStorage.length + sessionStorage.length).toBe(0);
  await confirm();
  const request = vi.mocked(fetch).mock.calls[0];
  expect(request[0]).toBe("/api/account/credentials/email/confirm"); expect(JSON.parse(String(request[1]?.body)).token === capability).toBe(true);
  expect(request[1]?.referrerPolicy).toBe("no-referrer");
  expect(container.textContent).toContain("второй"); expect(container.querySelector("button")).toBeNull();
});
it("finishes without logging out or replacing an unrelated authenticated principal", async () => {
  vi.mocked(fetch).mockResolvedValue(Response.json({ status: "completed", reauthenticate: true }));
  await mount(); await confirm();
  expect(container.textContent).toContain("изменён"); expect(container.querySelector('a[href="/login"]')).not.toBeNull();
  expect(fetch).toHaveBeenCalledTimes(1); expect(window.location.pathname).toBe("/confirm-email-change");
});
it("explains an expired or already used capability and offers a new request", async () => {
  vi.mocked(fetch).mockResolvedValue(Response.json({ error: { code: "bad_request", message: "Ссылка истекла или уже использована" } }, { status: 400 }));
  await mount(); await confirm();
  expect(container.querySelector('[role="alert"]')?.textContent).toContain("истекла");
  expect(container.querySelector('a[href="/account"]')).not.toBeNull(); expect(container.querySelector("button")).toBeNull();
});
it("retains an in-memory capability for retry after a network failure", async () => {
  vi.mocked(fetch).mockRejectedValueOnce(new TypeError("Network unavailable"));
  await mount(); await confirm(); expect(container.querySelector('[role="alert"]')).not.toBeNull();
  expect(container.querySelector<HTMLButtonElement>("button")?.disabled).toBe(false);
  await confirm(); expect(container.textContent).toContain("второй"); expect(fetch).toHaveBeenCalledTimes(2);
});
it("does not submit a missing token", async () => {
  window.history.replaceState({}, "", "/confirm-email-change"); await mount();
  expect(container.textContent).toContain("недействительна"); expect(container.querySelector("button")).toBeNull(); expect(fetch).not.toHaveBeenCalled();
});
it("disables confirmation while pending and ignores a result after leaving the page", async () => {
  let finish!: (response: Response) => void;
  vi.mocked(fetch).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  await mount(); await confirm(); await confirm();
  expect(fetch).toHaveBeenCalledTimes(1); expect(container.querySelector<HTMLButtonElement>("button")?.disabled).toBe(true);
  await act(async () => root.render(<p>Другая страница</p>));
  await act(async () => finish(Response.json({ status: "completed", reauthenticate: true })));
  expect(container.textContent).toBe("Другая страница"); expect(fetch).toHaveBeenCalledTimes(1);
});
