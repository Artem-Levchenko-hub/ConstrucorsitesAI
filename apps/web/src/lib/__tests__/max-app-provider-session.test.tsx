import * as React from "react";
import { act, createElement, type ComponentType, type ReactNode } from "react";
import * as jsxRuntime from "react/jsx-runtime";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createMaxTemplateLoader } from "./helpers/max-template-loader";

const user = { id: "123", firstName: "Мария", lastName: null, username: null, languageCode: "ru", photoUrl: null };

function loadProvider(fetch: ReturnType<typeof vi.fn>, initData = "", hostname = "app.example.com", override?: Window) {
  const windowBoundary = (override ?? { fetch, parent: window.parent, crypto: window.crypto, addEventListener: window.addEventListener.bind(window), removeEventListener: window.removeEventListener.bind(window), setTimeout: window.setTimeout.bind(window), clearTimeout: window.clearTimeout.bind(window), location: { hostname, href: `https://${hostname}/`, origin: `https://${hostname}` } }) as Window;
  if (!override) Object.defineProperty(windowBoundary, "parent", { value: windowBoundary });
  const imports: Record<string, unknown> = {
    react: React, "react/jsx-runtime": jsxRuntime,
    "next/dynamic": { default: () => ({ children }: { children: ReactNode }) => children },
    "@/lib/max/bridge": { getMaxWebApp: () => ({ initData, platform: "ios" }), configureMaxShell: vi.fn() },
    "@/components/YleumCompliance": { YleumCompliance: () => null },
  };
  const exports = createMaxTemplateLoader(imports, windowBoundary).load("components/MaxAppProvider.tsx") as {
    MaxAppProvider: ComponentType<{ children: ReactNode }>;
    useMaxApp: () => { mode: string; user: { id: string } | null };
  };
  function Product() {
    const context = exports.useMaxApp();
    const [draft, setDraft] = React.useState("original");
    return <><div data-product>{context.mode}:{context.user?.id}</div><input aria-label="draft" value={draft} onInput={event => setDraft(event.currentTarget.value)} /><button data-save onClick={() => void windowBoundary.fetch("/api/tasks/own", { method: "PATCH", body: draft, headers: { "If-Match": "revision-7" } })}>Save</button></>;
  }
  return createElement(exports.MaxAppProvider, { children: createElement(Product) });
}

function ownerFrame(fetch: ReturnType<typeof vi.fn>, origin = "https://private.example.com") {
  const listeners = new Set<(event: MessageEvent) => void>();
  const events = new EventTarget();
  const parent = { postMessage: vi.fn(message => queueMicrotask(() => listeners.forEach(fn => fn({
    source: parent, origin: "https://yleum.ru", data: { type: "omnia:preview-session:result", nonce: message.nonce,
      url: `${origin}/api/omnia/preview-session?signature=opaque` },
  } as unknown as MessageEvent)))) };
  const boundary = { fetch, parent, crypto, location: { origin, href: `${origin}/` },
    setTimeout, clearTimeout,
    addEventListener: (type: string, fn: EventListener) => type === "message" ? listeners.add(fn as (e: MessageEvent) => void) : events.addEventListener(type, fn),
    removeEventListener: (type: string, fn: EventListener) => type === "message" ? listeners.delete(fn as (e: MessageEvent) => void) : events.removeEventListener(type, fn),
  } as unknown as Window;
  return { boundary, parent, listeners };
}

describe("canonical MAX provider session recovery", () => {
  let container: HTMLDivElement;
  let root: Root;
  const dialogMethods = Object.fromEntries(["showModal", "close"].map(name =>
    [name, Object.getOwnPropertyDescriptor(HTMLDialogElement.prototype, name)]));
  beforeEach(() => {
    Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
    // jsdom has no native top layer; real Chromium coverage lives in the API
    // Provider gate fixture. Here only lifecycle/open cleanup is emulated.
    Object.defineProperties(HTMLDialogElement.prototype, {
      showModal: { configurable: true, value(this: HTMLDialogElement) { this.setAttribute("open", ""); } },
      close: { configurable: true, value(this: HTMLDialogElement) { this.removeAttribute("open"); } },
    });
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
  });
  afterEach(() => {
    act(() => root.unmount()); container.remove(); vi.restoreAllMocks();
    for (const [name, descriptor] of Object.entries(dialogMethods)) {
      if (descriptor) Object.defineProperty(HTMLDialogElement.prototype, name, descriptor);
      else Reflect.deleteProperty(HTMLDialogElement.prototype, name);
    }
  });
  async function render(fetch: ReturnType<typeof vi.fn>, initData = "", hostname?: string) {
    await act(async () => root.render(loadProvider(fetch, initData, hostname)));
  }
  it("opens the product only after the server confirms the cookie session", async () => {
    let finish!: (value: unknown) => void;
    const fetch = vi.fn().mockReturnValue(new Promise(resolve => { finish = resolve; }));
    await render(fetch);
    expect(container.querySelector("[data-product]")).toBeNull();
    expect(container.querySelector('[role="status"]')).not.toBeNull();
    await act(async () => finish({ ok: true, status: 200, json: async () => ({ user }) }));
    expect(container.querySelector("[data-product]")?.textContent).toBe("max:123");
    expect(fetch).toHaveBeenCalledWith("/api/max/session", { method: "GET", credentials: "include", cache: "no-store", signal: expect.any(AbortSignal) });
  });
  it("requires a fresh MAX launch when no valid cookie session exists", async () => {
    const fetch = vi.fn().mockResolvedValue({ ok: false, status: 401, json: async () => ({}) });
    await render(fetch);
    expect(container.querySelector("[data-product]")).toBeNull();
    expect(container.querySelector('[role="alert"]')?.textContent).toContain("Откройте приложение");
    expect(fetch.mock.calls.map(call => [call[0], call[1].method ?? "GET"])).toEqual([
      ["/api/max/session", "GET"], ["/api/max/session", "GET"],
    ]);
  });
  it("keeps a temporary recovery failure retryable without granting access", async () => {
    const fetch = vi.fn().mockResolvedValueOnce({ ok: false, status: 503, json: async () => ({}) })
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ user }) });
    await render(fetch);
    expect(container.querySelector("[data-product]")).toBeNull();
    expect(container.querySelector('[role="alert"]')?.textContent).toContain("временно недоступен");
    await act(async () => container.querySelector<HTMLButtonElement>("button")!.click());
    expect(container.querySelector("[data-product]")?.textContent).toBe("max:123");
    expect(fetch.mock.calls.map(call => call[1].method)).toEqual(["GET", "GET"]);
  });
  it("authenticates supplied launch data through POST, without cookie fallback", async () => {
    const fetch = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({ user }) });
    await render(fetch, "signed-launch");
    expect(container.querySelector("[data-product]")?.textContent).toBe("max:123");
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch.mock.calls[0][1]).toMatchObject({ method: "POST", body: JSON.stringify({ initData: "signed-launch" }) });
  });
  it("never resumes a cookie after supplied launch data is rejected", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const fetch = vi.fn().mockResolvedValue({ ok: false, status: 401, json: async () => ({ code: "max_auth_invalid" }) });
    await render(fetch, "invalid-launch");
    expect(container.querySelector("[data-product]")).toBeNull();
    expect(fetch.mock.calls.map(call => call[1].method)).toEqual(["POST"]);
  });
  it.each([{}, { user: { ...user, id: "preview" } }, { user: { ...user, id: "" } }])("rejects malformed or preview identities on the public origin", async (body) => {
    const fetch = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => body });
    await render(fetch);
    expect(container.querySelector("[data-product]")).toBeNull();
    expect(container.querySelector('[role="alert"]')).not.toBeNull();
  });
  it("resumes the server-verified owner preview on the current dev2 host", async () => {
    let finish!: (value: unknown) => void;
    const fetch = vi.fn().mockReturnValue(new Promise(resolve => { finish = resolve; }));
    await render(fetch, "", "qa-dev.dev2.yleum.ru");
    expect(container.querySelector("[data-product]")).toBeNull();
    await act(async () => finish({ ok: true, status: 200, json: async () => ({
      user: { id: "preview" }, mode: "preview",
    }) }));
    expect(container.querySelector("[data-product]")?.textContent).toBe("preview:preview");
    expect(fetch).toHaveBeenCalledWith("/api/max/session", { method: "GET", credentials: "include", cache: "no-store", signal: expect.any(AbortSignal) });
  });
  it.each(["localhost", "127.0.0.1", "qa-dev.preview.example.com", "qa-dev.dev2.yleum.ru"])("never grants preview access based on hostname %s", async hostname => {
    const fetch = vi.fn().mockResolvedValue({ ok: false, status: 401, json: async () => ({}) });
    await render(fetch, "", hostname);
    expect(container.querySelector("[data-product]")).toBeNull();
    expect(fetch.mock.calls.map(call => [call[0], call[1].method ?? "GET"])).toEqual([
      ["/api/max/session", "GET"], ["/api/max/session", "GET"],
    ]);
  });
  it("rejects preview payloads on failed responses", async () => {
    const fetch = vi.fn().mockResolvedValue({ ok: false, status: 401, json: async () => ({ user: { id: "preview" }, mode: "preview" }) });
    await render(fetch, "", "qa-dev.dev2.yleum.ru");
    expect(container.querySelector("[data-product]")).toBeNull();
  });
  it("never substitutes preview identity for an actual MAX launch", async () => {
    const fetch = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({ user: { id: "preview" }, mode: "preview" }) });
    await render(fetch, "signed-launch");
    expect(container.querySelector("[data-product]")).toBeNull();
    expect(fetch.mock.calls.map(call => call[1].method)).toEqual(["POST"]);
  });
  it("keeps the same mounted form and draft through expired-session renewal", async () => {
    let expired = false;
    const fetch = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === "/api/max/session") return expired ? new Response(null, { status: 401 }) : Response.json({ mode: "preview", user: { id: "preview" } });
      if (String(input).includes("preview-session?")) { expired = false; return Response.json({ mode: "preview", user: { id: "preview" } }); }
      return Response.json({ saved: true });
    });
    const { boundary } = ownerFrame(fetch, "https://qa-dev.dev2.yleum.ru");
    await act(async () => root.render(loadProvider(fetch, "", "qa-dev.dev2.yleum.ru", boundary)));
    const input = container.querySelector<HTMLInputElement>("input")!;
    await act(async () => { input.value = "unsaved personal draft"; input.dispatchEvent(new Event("input", { bubbles: true })); });
    expired = true;
    await act(async () => container.querySelector<HTMLButtonElement>("[data-save]")!.click());
    expect(container.querySelector("input")).toBe(input);
    expect(input.value).toBe("unsaved personal draft");
    expect(container.querySelector('[role="status"]')).toBeNull();
    expect(fetch.mock.calls.filter(c => c[0] === "/api/tasks/own")).toEqual([["/api/tasks/own", { method: "PATCH", body: "unsaved personal draft", headers: { "If-Match": "revision-7" } }]]);
  });

  it("recovers an initial owner iframe 401 without installing a guard before server proof", async () => {
    let expired = true;
    let finish!: (response: Response) => void;
    const fetch = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === "/api/max/session") return expired ? new Response(null, { status: 401 }) : Response.json({ mode: "preview", user: { id: "preview" } });
      return new Promise<Response>(resolve => { finish = resolve; });
    });
    const { boundary, listeners } = ownerFrame(fetch);
    await act(async () => root.render(loadProvider(fetch, "", undefined, boundary)));
    expect(container.querySelector("[data-product]")).toBeNull();
    expect(container.querySelector('[role="status"]')).not.toBeNull();
    expect(finish).toBeTypeOf("function");
    await act(async () => { expired = false; finish(Response.json({ mode: "preview", user: { id: "preview" } })); });
    expect(container.querySelector("[data-product]")?.textContent).toBe("preview:preview");
    expect(listeners.size).toBe(0);
    expect(fetch.mock.calls.every(c => String(c[0]) === "/api/max/session" || String(c[0]).includes("/api/omnia/preview-session?"))).toBe(true);
    act(() => root.unmount()); expect(boundary.fetch).toBe(fetch);
  });
  it("never requests an owner capability after actual MAX initData rejection in an iframe", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const fetch = vi.fn(async () => new Response(null, { status: 401 }));
    const { boundary, parent } = ownerFrame(fetch);
    await act(async () => root.render(loadProvider(fetch, "invalid-launch", undefined, boundary)));
    expect(container.querySelector("[data-product]")).toBeNull();
    expect(container.querySelector('[role="alert"]')).not.toBeNull();
    expect(parent.postMessage).not.toHaveBeenCalled();
    expect(fetch).toHaveBeenCalledTimes(1);
  });
  it.each([
    { mode: "preview", user: { id: "123" } }, { mode: "preview", user: { id: "" } }, { user: { id: "preview" } },
  ])("does not grant access after invalid recovery identity %j", async body => {
    let bootstrapped = false;
    const fetch = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === "/api/max/session") return bootstrapped ? Response.json(body) : new Response(null, { status: 401 });
      bootstrapped = true; return Response.json({ mode: "preview", user: { id: "preview" } });
    });
    const { boundary } = ownerFrame(fetch);
    await act(async () => root.render(loadProvider(fetch, "", undefined, boundary)));
    expect(bootstrapped).toBe(true);
    expect(container.querySelector("[data-product]")).toBeNull();
    expect(container.querySelector('[role="alert"]')).not.toBeNull();
    act(() => root.unmount()); expect(boundary.fetch).toBe(fetch);
  });
  it("rejects a numeric identity mislabeled as owner preview", async () => {
    await render(vi.fn(async () => Response.json({ mode: "preview", user })));
    expect(container.querySelector("[data-product]")).toBeNull();
    expect(container.querySelector('[role="alert"]')).not.toBeNull();
  });
  it("keeps unknown server failures visible without attempting iframe recovery", async () => {
    const fetch = vi.fn(async () => new Response(null, { status: 503 }));
    const { boundary, parent } = ownerFrame(fetch);
    await act(async () => root.render(loadProvider(fetch, "", undefined, boundary)));
    expect(container.querySelector('[role="alert"]')?.textContent).toContain("временно недоступен");
    expect(parent.postMessage).not.toHaveBeenCalled();
    expect(fetch).toHaveBeenCalledTimes(1);
    act(() => root.unmount()); expect(boundary.fetch).toBe(fetch);
  });

});
