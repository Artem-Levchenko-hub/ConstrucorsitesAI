import { afterEach, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { middleware } from "@/middleware";
import AppLayout from "@/app/(app)/layout";
import LoginPage from "@/app/(auth)/login/page";
import { act } from "react";
import { createRoot } from "react-dom/client";

const state = vi.hoisted(() => ({ target: "", token: undefined as string | undefined, staffEnabled: false }));
vi.mock("@/lib/max-staff-path", async importOriginal => ({
  ...await importOriginal<typeof import("@/lib/max-staff-path")>(),
  get maxStaffEnabled() { return state.staffEnabled; },
}));
vi.mock("next/headers", () => ({
  headers: async () => new Headers({ "x-omnia-return-to": state.target }),
  cookies: async () => ({ get: () => state.token ? { value: state.token } : undefined }),
}));
vi.mock("next-intl", () => ({ useTranslations: () => (key: string) => key }));
vi.mock("next-intl/server", () => ({ getTranslations: async () => (key: string) => key }));
vi.mock("@/lib/oauth-login-server", () => ({ listOAuthProviders: async () => [] }));
vi.mock("@/app/(auth)/actions", () => ({ loginAction: vi.fn() }));
afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); state.target = ""; state.token = undefined; state.staffEnabled = false; });

it.each([undefined, "stale"])("preserves the exact owner preview destination through real layout admission with cookie %s", async token => {
  vi.stubEnv("NEXT_PUBLIC_USE_MOCKS", "false");
  state.token = token;
  const fetch = vi.fn(async () => new Response(null, { status: 401 }));
  vi.stubGlobal("fetch", fetch);
  const path = "/max/00000000-0000-0000-0000-000000000001/preview?source=separate";
  const response = middleware(new NextRequest(`https://example.test${path}`, { headers: {
    ...(token ? { cookie: `omnia_session=${token}` } : {}), "x-omnia-return-to": "https://evil.test",
  } }));
  if (!token) expect(new URL(response.headers.get("location")!).pathname).toBe("/login");
  state.target = token
    ? response.headers.get("x-middleware-request-x-omnia-return-to")!
    : new URL(response.headers.get("location")!).searchParams.get("next")!;
  expect(state.target).toBe(path);
  await expect(AppLayout({ children: null })).rejects.toMatchObject({
    digest: expect.stringContaining(`;/login?next=${encodeURIComponent(path)};`),
  });
  expect(fetch).toHaveBeenCalledTimes(token ? 1 : 0);
});

it("carries a missing-cookie owner destination from the actual middleware Location into the actual login form", async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.stubEnv("NEXT_PUBLIC_USE_MOCKS", "false");
  const path = "/max/owner-project/preview?mode=phone&filter=a%2Fb";
  const response = middleware(new NextRequest(`https://example.test${path}`));
  const location = new URL(response.headers.get("location")!);
  expect(location.pathname).toBe("/login");
  const page = await LoginPage({ searchParams: Promise.resolve({ next: location.searchParams.get("next")! }) });
  const container = document.createElement("div"); document.body.append(container); const root = createRoot(container);
  try {
    await act(async () => root.render(page));
    expect(container.querySelector<HTMLInputElement>('input[name="next"]')?.value).toBe(path);
  } finally { await act(async () => root.unmount()); container.remove(); }
});

it("retains registration admission for other MAX routes", () => {
  vi.stubEnv("NEXT_PUBLIC_USE_MOCKS", "false");
  const response = middleware(new NextRequest("https://example.test/max/owner-project"));
  const location = new URL(response.headers.get("location")!);
  expect(location.pathname).toBe("/max/register");
  expect(location.searchParams.get("next")).toBe("/max/owner-project");
});

it("preserves a clean staff destination with a stale session cookie", async () => {
  state.staffEnabled = true;
  vi.stubEnv("NEXT_PUBLIC_USE_MOCKS", "false");
  state.token = "stale";
  vi.stubGlobal("fetch", vi.fn(async () => new Response(null, { status: 401 })));
  const path = "/max/staff/00000000-0000-4000-8000-000000000001";
  const response = middleware(new NextRequest(`https://example.test${path}?token=discard`, {
    headers: { cookie: "omnia_session=stale", "x-omnia-return-to": "//evil.test" },
  }));
  state.target = response.headers.get("x-middleware-request-x-omnia-return-to")!;
  await expect(AppLayout({ children: null })).rejects.toMatchObject({
    digest: expect.stringContaining(`;/login?next=${encodeURIComponent(path)};`),
  });
});

it.each(["/max/staff", "/max/staff/join", "/max/staff/00000000-0000-4000-8000-000000000001"])("redirects a disabled staff route %s without forwarding an invitation", path => {
  vi.stubEnv("NEXT_PUBLIC_USE_MOCKS", "false");
  vi.stubEnv("PUBLIC_ORIGIN", "https://example.test");
  const response = middleware(new NextRequest(`http://localhost:3000${path}?token=discard#token=discard`));
  expect(response.status).toBe(307);
  expect(response.headers.get("location")).toBe("https://example.test/max#top");
  expect(response.headers.get("referrer-policy")).toBe("no-referrer");
  expect(response.headers.get("cache-control")).toBe("no-store");
});

it("does not forward a POST body from a disabled staff route", () => {
  const response = middleware(new NextRequest("https://example.test/max/staff/join", {
    method: "POST",
    body: "token=discard",
  }));
  expect(response.status).toBe(303);
  expect(new URL(response.headers.get("location")!).pathname).toBe("/max");
});

it("keeps the staff join page through the real login form and registration link", async () => {
  state.staffEnabled = true;
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  const path = "/max/staff/join";
  const page = await LoginPage({ searchParams: Promise.resolve({ next: `${path}?token=discard#token=discard` }) });
  const container = document.createElement("div"); document.body.append(container); const root = createRoot(container);
  try {
    await act(async () => root.render(page));
    expect(container.querySelector<HTMLInputElement>('input[name="next"]')?.value).toBe(path);
    expect(container.querySelector('a[href^="/max/register"]')?.getAttribute("href")).toBe(`/max/register?next=${encodeURIComponent(path)}`);
    expect(container.innerHTML).not.toContain("discard");
  } finally { await act(async () => root.unmount()); container.remove(); }
});

it.each(["https://evil.test/max/p/preview", "//evil.test/max/p/preview", "/max/p/preview/extra",
  "/max/p/preview-evil", "/max/p/editor", "/max/p%2Fq/preview", "/max//preview"])("does not broaden login returns for %s", async target => {
  vi.stubEnv("NEXT_PUBLIC_USE_MOCKS", "false");
  state.target = target;
  await expect(AppLayout({ children: null })).rejects.toMatchObject({ digest: expect.stringContaining(";/login;") });
});
