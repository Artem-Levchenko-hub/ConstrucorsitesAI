import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import * as React from "react";
import { act, createElement, type ComponentType, type ReactNode } from "react";
import * as jsxRuntime from "react/jsx-runtime";
import { createRoot, type Root } from "react-dom/client";
import ts from "typescript";
import { installOwnerPreviewFetch } from "../../../../orchestrator/templates/max-miniapp-nextjs/src/lib/max/owner-preview-renewal";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const source = readFileSync(resolve(process.cwd(), "../orchestrator/templates/max-miniapp-nextjs/src/components/MaxAppProvider.tsx"), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: {
  module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
} }).outputText;
const user = { id: "123", firstName: "Мария", lastName: null, username: null, languageCode: "ru", photoUrl: null };

function loadProvider(fetch: ReturnType<typeof vi.fn>, initData = "", hostname = "app.example.com", override?: Window) {
  const exports = {} as {
    MaxAppProvider: ComponentType<{ children: ReactNode }>;
    useMaxApp: () => { mode: string; user: typeof user | null };
  };
  const windowBoundary = (override ?? { fetch, parent: window.parent, crypto: window.crypto, addEventListener: window.addEventListener.bind(window), removeEventListener: window.removeEventListener.bind(window), setTimeout: window.setTimeout.bind(window), clearTimeout: window.clearTimeout.bind(window), location: { hostname, href: `https://${hostname}/`, origin: `https://${hostname}` } }) as Window;
  const imports: Record<string, unknown> = {
    react: React, "react/jsx-runtime": jsxRuntime,
    "next/dynamic": { default: () => ({ children }: { children: ReactNode }) => children },
    "@/lib/max/bridge": { getMaxWebApp: () => ({ initData, platform: "ios" }), configureMaxShell: vi.fn() },
    "@/lib/max/owner-preview-renewal": { installOwnerPreviewFetch },
    "@/components/YleumCompliance": { YleumCompliance: () => null },
  };
  new Function("exports", "require", "fetch", "window", compiled)(exports, (id: string) => {
    if (!(id in imports)) throw new Error(`Unexpected import ${id}`);
    return imports[id];
  }, (...args: Parameters<typeof window.fetch>) => windowBoundary.fetch(...args), windowBoundary);
  function Product() {
    const context = exports.useMaxApp();
    const [draft, setDraft] = React.useState("original");
    return <><div data-product>{context.mode}:{context.user?.firstName}</div><input aria-label="draft" value={draft} onInput={event => setDraft(event.currentTarget.value)} /><button data-save onClick={() => void windowBoundary.fetch("/api/tasks/own", { method: "PATCH", body: draft, headers: { "If-Match": "revision-7" } })}>Save</button></>;
  }
  return createElement(exports.MaxAppProvider, { children: createElement(Product) });
}

describe("canonical MAX provider session recovery", () => {
  let container: HTMLDivElement;
  let root: Root;
  beforeEach(() => {
    Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
  });
  afterEach(() => { act(() => root.unmount()); container.remove(); vi.restoreAllMocks(); });
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
    expect(container.querySelector("[data-product]")?.textContent).toBe("max:Мария");
    expect(fetch).toHaveBeenCalledWith("/api/max/session", { method: "GET", credentials: "include", cache: "no-store" });
  });
  it("requires a fresh MAX launch when no valid cookie session exists", async () => {
    const fetch = vi.fn().mockResolvedValue({ ok: false, status: 401, json: async () => ({}) });
    await render(fetch);
    expect(container.querySelector("[data-product]")).toBeNull();
    expect(container.querySelector('[role="alert"]')?.textContent).toContain("Откройте приложение");
    expect(fetch).toHaveBeenCalledTimes(1);
  });
  it("keeps a temporary recovery failure retryable without granting access", async () => {
    const fetch = vi.fn().mockResolvedValueOnce({ ok: false, status: 503, json: async () => ({}) })
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ user }) });
    await render(fetch);
    expect(container.querySelector("[data-product]")).toBeNull();
    expect(container.querySelector('[role="alert"]')?.textContent).toContain("временно недоступен");
    await act(async () => container.querySelector<HTMLButtonElement>("button")!.click());
    expect(container.querySelector("[data-product]")?.textContent).toBe("max:Мария");
    expect(fetch.mock.calls.map(call => call[1].method)).toEqual(["GET", "GET"]);
  });
  it("authenticates supplied launch data through POST, without cookie fallback", async () => {
    const fetch = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({ user }) });
    await render(fetch, "signed-launch");
    expect(container.querySelector("[data-product]")?.textContent).toBe("max:Мария");
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
    expect(container.querySelector("[data-product]")?.textContent).toBe("preview:");
    expect(fetch).toHaveBeenCalledWith("/api/max/session", { method: "GET", credentials: "include", cache: "no-store" });
  });
  it.each(["localhost", "127.0.0.1", "qa-dev.preview.example.com", "qa-dev.dev2.yleum.ru"])("never grants preview access based on hostname %s", async hostname => {
    const fetch = vi.fn().mockResolvedValue({ ok: false, status: 401, json: async () => ({}) });
    await render(fetch, "", hostname);
    expect(container.querySelector("[data-product]")).toBeNull();
    expect(fetch).toHaveBeenCalledTimes(1);
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
    const listeners = new Set<(event: MessageEvent) => void>();
    const parent = { postMessage: vi.fn(message => queueMicrotask(() => listeners.forEach(fn => fn({
      source: parent, origin: "https://yleum.ru", data: { type: "omnia:preview-session:result", nonce: message.nonce,
        url: "https://qa-dev.dev2.yleum.ru/api/omnia/preview-session?signature=opaque" },
    } as unknown as MessageEvent)))) };
    const fetch = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === "/api/max/session") return expired ? new Response(null, { status: 401 }) : Response.json({ mode: "preview", user: { id: "preview" } });
      if (String(input).includes("preview-session?")) { expired = false; return Response.json({ mode: "preview", user: { id: "preview" } }); }
      return Response.json({ saved: true });
    });
    const boundary = { fetch, parent, crypto, location: { origin: "https://qa-dev.dev2.yleum.ru", href: "https://qa-dev.dev2.yleum.ru/" },
      setTimeout, clearTimeout, addEventListener: (_: string, fn: (e: MessageEvent) => void) => listeners.add(fn),
      removeEventListener: (_: string, fn: (e: MessageEvent) => void) => listeners.delete(fn),
    } as unknown as Window;
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

});
