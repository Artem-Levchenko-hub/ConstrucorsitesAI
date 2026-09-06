import { expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { middleware } from "@/middleware";
import AppLayout from "@/app/(app)/layout";
import { AccountShell } from "@/components/account/AccountShell";
import { renderToStaticMarkup } from "react-dom/server";
import AccountPage from "@/app/(app)/account/page";
import PlanPage from "@/app/(app)/billing/plan/page";
const state = vi.hoisted(() => ({ target: "" }));
vi.mock("@/lib/auth-mock", () => ({ getSession: async () => null, getMaxAdminAccessServer: async () => false }));
vi.mock("next/headers", () => ({ headers: async () => new Headers({ "x-omnia-return-to": state.target }) }));
vi.mock("@/app/(auth)/actions", () => ({ logoutAction: vi.fn() }));
it.each(["/account?payment=pay-1", "/billing/plan?payment=pay-1"])("preserves payment return through missing and stale-cookie auth: %s", async (path) => {
  vi.stubEnv("NEXT_PUBLIC_USE_MOCKS", "false");
  try {
    const response = middleware(new NextRequest(`https://example.test${path}`));
    expect(new URL(response.headers.get("location")!).searchParams.get("next")).toBe(path);
    const stale = middleware(new NextRequest(`https://example.test${path}`, { headers: { cookie: "omnia_session=stale", "x-omnia-return-to": "https://evil.test" } }));
    state.target = stale.headers.get("x-middleware-request-x-omnia-return-to")!;
    expect(state.target).toBe(path);
    await expect(AppLayout({ children: null })).rejects.toMatchObject({ digest: expect.stringContaining(encodeURIComponent(path)) });
    expect(middleware(new NextRequest("https://example.test/login", { headers: { cookie: "omnia_session=stale" } })).headers.get("location")).toBeNull();
  } finally { vi.unstubAllEnvs(); }
});
it("provides six accessible account links on mobile and desktop", async () => {
  const html = renderToStaticMarkup(await AccountShell({ email: "qa@example.test", active: "billing", children: null }));
  const dom = document.createElement("div"); dom.innerHTML = html;
  const nav = dom.querySelector('nav[aria-label="Разделы аккаунта"]')!;
  expect(nav).not.toBeNull(); expect(nav.querySelectorAll("a")).toHaveLength(6);
  expect(nav.querySelector('[aria-current="page"]')?.getAttribute("href")).toBe("/billing");
  expect(nav.closest(".hidden")).toBeNull();
});
it.each([[AccountPage, "/account"], [PlanPage, "/billing/plan"]] as const)("preserves the return even if the page redirects before its outer layout", async (Page, path) => {
  await expect(Page({ searchParams: Promise.resolve({ payment: "pay-1" }) })).rejects.toMatchObject({ digest: expect.stringContaining(encodeURIComponent(path + "?payment=pay-1")) });
});
it.each(["https://evil.test", "//evil.test", "/accounting?payment=x", "/billing-evil"])("rejects a forged or non-account return header %s", async target => {
  state.target = target;
  await expect(AppLayout({ children: null })).rejects.toMatchObject({ digest: expect.stringContaining(";/login;") });
});
