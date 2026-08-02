import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/link", () => ({
  default: ({
    children,
    href,
    onClick,
    ...props
  }: React.ComponentProps<"a">) =>
    createElement(
      "a",
      {
        href,
        ...props,
        onClick: (event: React.MouseEvent<HTMLAnchorElement>) => {
          event.preventDefault();
          onClick?.(event);
        },
      },
      children,
    ),
}));

vi.mock("@/app/(auth)/actions", () => ({
  logoutAction: vi.fn(),
}));

import { MaxAccountMenu } from "@/components/max/MaxAccountMenu";

const workspace = readFileSync(
  resolve(process.cwd(), "src/components/max/MaxWorkspaceShell.tsx"),
  "utf8",
);

const accountLinks = [
  ["/account", "Профиль"],
  ["/account/organization", "Организация"],
  ["/account/security", "Безопасность"],
  ["/billing", "Баланс"],
  ["/billing/transactions", "Операции"],
  ["/billing/plan", "Тариф"],
] as const;

describe("MAX account disclosure", () => {
  let container: HTMLDivElement;
  let root: Root;
  let navigationCount: number;

  beforeEach(async () => {
    (
      globalThis as typeof globalThis & {
        IS_REACT_ACT_ENVIRONMENT: boolean;
      }
    ).IS_REACT_ACT_ENVIRONMENT = true;
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    navigationCount = 0;

    await act(async () => {
      root.render(
        createElement(MaxAccountMenu, {
          email: "owner@example.com",
          onNavigate: () => {
            navigationCount += 1;
          },
        }),
      );
    });
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    vi.restoreAllMocks();
  });

  function trigger() {
    return container.querySelector<HTMLButtonElement>(
      '[data-testid="max-account-menu-trigger"]',
    )!;
  }

  async function openMenu() {
    await act(async () => trigger().click());
  }

  it("keeps the existing shell behind one closed account trigger", () => {
    expect(workspace).toContain("<MaxAccountMenu");
    expect(trigger().getAttribute("aria-expanded")).toBe("false");
    expect(
      container.querySelector('[data-testid="max-account-menu"]'),
    ).toBeNull();
  });

  it("reveals every account destination without moving the sidebar layout", async () => {
    await openMenu();

    const menu = container.querySelector<HTMLElement>(
      '[data-testid="max-account-menu"]',
    );
    expect(trigger().getAttribute("aria-expanded")).toBe("true");
    expect(menu).not.toBeNull();
    expect(menu?.className).toContain("absolute");
    expect(menu?.className).toContain("bottom-full");
    expect(menu?.className).toContain("overflow-y-auto");

    for (const [href, label] of accountLinks) {
      const link = menu?.querySelector<HTMLAnchorElement>(`a[href="${href}"]`);
      expect(link?.textContent).toContain(label);
    }

    const firstLink = menu?.querySelector<HTMLAnchorElement>("a");
    expect(
      trigger().compareDocumentPosition(firstLink!) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("closes on Escape and returns focus to the account trigger", async () => {
    await openMenu();

    await act(async () => {
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    });

    expect(trigger().getAttribute("aria-expanded")).toBe("false");
    expect(document.activeElement).toBe(trigger());
  });

  it("closes on an outside press", async () => {
    await openMenu();

    await act(async () => {
      document.body.dispatchEvent(new Event("pointerdown", { bubbles: true }));
    });

    expect(trigger().getAttribute("aria-expanded")).toBe("false");
  });

  it("closes the mobile navigation after choosing a destination", async () => {
    await openMenu();
    const profileLink = container.querySelector<HTMLAnchorElement>(
      'a[href="/account"]',
    )!;

    await act(async () => {
      profileLink.dispatchEvent(
        new MouseEvent("click", { bubbles: true, cancelable: true }),
      );
    });

    expect(navigationCount).toBe(1);
    expect(trigger().getAttribute("aria-expanded")).toBe("false");
  });
});
