import { renderToStaticMarkup } from "react-dom/server";
import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import MaxGuidePage from "@/app/max/guide/page";
import MaxQuickStartPage from "@/app/max/start/page";
import { middleware } from "@/middleware";

afterEach(() => vi.unstubAllEnvs());

describe("returning from MAX help to the workspace", () => {
  it.each([
    ["quick start", MaxQuickStartPage],
    ["full guide", MaxGuidePage],
  ] as const)("opens the workspace from the %s header", (_name, Page) => {
    const document = new DOMParser().parseFromString(renderToStaticMarkup(<Page />), "text/html");
    const entry = [...document.querySelectorAll("header a")].find(
      (link) => link.textContent?.trim() === "Открыть Yleum",
    );

    expect(entry, "the help header must offer a direct return to Yleum").toBeDefined();
    expect(entry?.getAttribute("href")).toBe("/max");
  });

  it("returns to the workspace after reading the full guide", () => {
    const document = new DOMParser().parseFromString(renderToStaticMarkup(<MaxGuidePage />), "text/html");
    const entry = [...document.querySelectorAll("a")].find(
      (link) => link.textContent?.includes("Перейти в Yleum"),
    );

    expect(entry).toBeDefined();
    expect(entry?.getAttribute("href")).toBe("/max");
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
