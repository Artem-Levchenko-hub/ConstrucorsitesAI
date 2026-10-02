import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MaxStudioHeader } from "@/components/max/MaxStudioHeader";

const boundary = vi.hoisted(() => ({
  getCookie: vi.fn(),
  setCookie: vi.fn(),
  redirect: vi.fn(),
  fetch: vi.fn(),
}));

// Keep the real logoutAction and Radix/React DOM interaction. Only replace
// server request boundaries: this is a local submission contract, not live
// browser session revocation or Next's Server Action transport acceptance.
vi.mock("next/headers", () => ({
  cookies: async () => ({ get: boundary.getCookie, set: boundary.setCookie }),
}));
vi.mock("next/navigation", () => ({ redirect: boundary.redirect }));

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

describe("MAX studio header logout submission", () => {
  let container: HTMLDivElement;
  let root: Root;
  let submitted: number;
  let onSubmit: (event: Event) => void;

  beforeEach(async () => {
    vi.clearAllMocks();
    vi.stubEnv("INTERNAL_API_URL", "https://api.example.test");
    vi.stubEnv("COOKIE_DOMAIN", ".example.test");
    boundary.getCookie.mockReturnValue({ value: "current-session" });
    boundary.fetch.mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", boundary.fetch);
    submitted = 0;
    onSubmit = () => { submitted += 1; };
    document.addEventListener("submit", onSubmit, true);
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    await act(async () => { root.render(<MaxStudioHeader email="owner@example.test" />); });
    await act(async () => {
      container.querySelector<HTMLButtonElement>('[aria-label="Аккаунт"]')!
        .dispatchEvent(new MouseEvent("pointerdown", { bubbles: true, button: 0 }));
      await vi.waitFor(() => expect(document.querySelector('[role="menu"]')).not.toBeNull());
    });
  });

  afterEach(async () => {
    await act(async () => { root.unmount(); });
    container.remove();
    document.removeEventListener("submit", onSubmit, true);
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  it("keeps the submitting form mounted while the logout API request is pending", async () => {
    let complete!: (response: Response) => void;
    boundary.fetch.mockReturnValueOnce(new Promise<Response>((resolve) => { complete = resolve; }));
    const logout = [...document.querySelectorAll<HTMLButtonElement>('[role="menuitem"]')]
      .find((item) => item.textContent?.trim() === "Выйти")!;
    const form = logout.form!;

    await act(async () => { logout.click(); });
    const stillMounted = form.isConnected;
    await act(async () => { complete(new Response(null, { status: 204 })); });

    // The browser's default submit runs after click handlers; the menu must
    // preserve its form rather than unmounting it during item selection.
    expect(stillMounted).toBe(true);
    expect(submitted).toBe(1);
    expect(boundary.redirect).toHaveBeenCalledExactlyOnceWith("/");
  });

  it.each(["pointer", "Enter"])("submits the real logout action exactly once using %s", async (activation) => {
    const logout = [...document.querySelectorAll<HTMLButtonElement>('[role="menuitem"]')]
      .find((item) => item.textContent?.trim() === "Выйти")!;
    expect(logout).toBeTruthy();
    await act(async () => {
      if (activation === "pointer") {
        logout.dispatchEvent(new MouseEvent("pointerdown", { bubbles: true, button: 0 }));
        logout.dispatchEvent(new MouseEvent("pointerup", { bubbles: true, button: 0 }));
        logout.click();
      } else {
        logout.focus();
        logout.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, cancelable: true, key: "Enter" }));
      }
    });

    expect(submitted).toBe(1);
    expect(boundary.fetch).toHaveBeenCalledExactlyOnceWith("https://api.example.test/api/auth/logout", {
      method: "POST",
      headers: { Cookie: "omnia_session=current-session" },
      cache: "no-store",
    });
    expect(boundary.setCookie).toHaveBeenCalledExactlyOnceWith({
      name: "omnia_session", value: "", path: "/", maxAge: 0, domain: ".example.test",
    });
    expect(boundary.redirect).toHaveBeenCalledExactlyOnceWith("/");
  });
});
