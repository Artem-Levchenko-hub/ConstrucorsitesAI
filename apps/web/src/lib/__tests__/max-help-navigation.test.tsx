import { renderToStaticMarkup } from "react-dom/server";
import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import MaxGuidePage from "@/app/max/guide/page";
import MaxQuickStartPage from "@/app/max/start/page";
import { middleware } from "@/middleware";

const getSession = vi.hoisted(() => vi.fn(async (): Promise<{ isAnon: boolean } | null> => null));
vi.mock("@/lib/auth-mock", () => ({ getSession }));

afterEach(() => vi.unstubAllEnvs());

describe("returning from MAX help to the workspace", () => {
  it.each([
    ["quick start", MaxQuickStartPage],
    ["full guide", MaxGuidePage],
  ] as const)("opens the workspace from the %s header", async (_name, Page) => {
    const document = new DOMParser().parseFromString(renderToStaticMarkup(await Page()), "text/html");
    const entry = [...document.querySelectorAll("header a")].find(
      (link) => link.textContent?.trim() === "Открыть Yleum",
    );

    expect(entry, "the help header must offer a direct return to Yleum").toBeDefined();
    expect(entry?.getAttribute("href")).toBe("/max");
  });

  it("returns to the workspace after reading the full guide", async () => {
    const document = new DOMParser().parseFromString(renderToStaticMarkup(await MaxGuidePage()), "text/html");
    const entry = [...document.querySelectorAll("a")].find(
      (link) => link.textContent?.includes("Перейти в Yleum"),
    );

    expect(entry).toBeDefined();
    expect(entry?.getAttribute("href")).toBe("/max");
  });

  it.each([
    { session: { isAnon: false }, label: "Yleum — мои приложения", startHref: "/max", guideHref: "/max" },
    { session: { isAnon: true }, label: "Yleum — главная", startHref: "/max/product", guideHref: "/" },
    { session: null, label: "Yleum — главная", startHref: "/max/product", guideHref: "/" },
  ])("links the help logos to $startHref and $guideHref for session $session", async ({ session, label, startHref, guideHref }) => {
    getSession.mockResolvedValueOnce(session).mockResolvedValueOnce(session);
    const start = new DOMParser().parseFromString(renderToStaticMarkup(await MaxQuickStartPage()), "text/html");
    const guide = new DOMParser().parseFromString(renderToStaticMarkup(await MaxGuidePage()), "text/html");

    expect(start.querySelector(`header a[aria-label="${label}"]`)?.getAttribute("href")).toBe(startHref);
    expect(guide.querySelector(`header a[aria-label="${label}"]`)?.getAttribute("href")).toBe(guideHref);
    expect(guide.querySelector(`footer a[aria-label="${label}"]`)?.getAttribute("href")).toBe(guideHref);
  });

  it("preserves the signed-in return and still requires anonymous users to authenticate", () => {
    vi.stubEnv("NEXT_PUBLIC_USE_MOCKS", "false");
    const signedIn = middleware(new NextRequest("https://yleum.test/max", {
      headers: { cookie: "omnia_session=test-session" },
    }));
    const anonymous = middleware(new NextRequest("https://yleum.test/max"));

    expect(signedIn.headers.get("location")).toBeNull();
    expect(signedIn.headers.get("set-cookie")).toBeNull();
    expect(anonymous.headers.get("location")).toBe("https://yleum.test/max/register?next=%2Fmax");
  });

  it.each(["/max/start", "/max/guide"])("keeps %s publicly accessible", (path) => {
    vi.stubEnv("NEXT_PUBLIC_USE_MOCKS", "false");
    const response = middleware(new NextRequest(`https://yleum.test${path}`));

    expect(response.headers.get("location")).toBeNull();
    expect(response.headers.get("set-cookie")).toBeNull();
  });
});
