import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import * as React from "react";
import { act, createElement, type ComponentType, type ReactNode } from "react";
import * as jsxRuntime from "react/jsx-runtime";
import { createRoot, type Root } from "react-dom/client";
import ts from "typescript";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const source = readFileSync(resolve(process.cwd(), "../orchestrator/templates/max-miniapp-nextjs/src/components/MaxAppProvider.tsx"), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: {
  module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
} }).outputText;
const user = { id: "123", firstName: "Мария", lastName: null, username: null, languageCode: "ru", photoUrl: null };

function loadProvider(fetch: ReturnType<typeof vi.fn>, initData = "", hostname = "app.example.com") {
  const exports = {} as {
    MaxAppProvider: ComponentType<{ children: ReactNode }>;
    useMaxApp: () => { mode: string; user: typeof user | null };
  };
  const windowBoundary = { fetch, location: { hostname, href: `https://${hostname}/`, origin: `https://${hostname}` } };
  const imports: Record<string, unknown> = {
    react: React, "react/jsx-runtime": jsxRuntime,
    "next/dynamic": { default: () => ({ children }: { children: ReactNode }) => children },
    "@/lib/max/bridge": { getMaxWebApp: () => ({ initData, platform: "ios" }), configureMaxShell: vi.fn() },
    "@/components/OmniaCompliance": { OmniaCompliance: () => null },
  };
  new Function("exports", "require", "fetch", "window", compiled)(exports, (id: string) => {
    if (!(id in imports)) throw new Error(`Unexpected import ${id}`);
    return imports[id];
  }, fetch, windowBoundary);
  function Product() {
    const context = exports.useMaxApp();
    return <div data-product>{context.mode}:{context.user?.firstName}</div>;
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
  it("preserves the existing local preview path without probing public auth", async () => {
    const fetch = vi.fn();
    await render(fetch, "", "localhost");
    expect(container.querySelector("[data-product]")?.textContent).toContain("preview:");
    expect(fetch).not.toHaveBeenCalled();
  });
});
