import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import postcss from "postcss";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

import PricingPage from "@/app/pricing/page";
import { AccountShell } from "@/components/account/AccountShell";

const auth = vi.hoisted(() => ({ getSession: vi.fn() }));
vi.mock("@/lib/auth-mock", () => ({
  getSession: auth.getSession,
  getMaxAdminAccessServer: async () => true,
}));
vi.mock("@/app/(auth)/actions", () => ({ logoutAction: vi.fn() }));

const maxStudioCss = readFileSync(
  resolve(process.cwd(), "src/components/max/max-studio.css"),
  "utf8",
);
const publicCss = readFileSync(
  resolve(process.cwd(), "src/components/marketing/max-public.css"),
  "utf8",
);
const globalCss = readFileSync(
  resolve(process.cwd(), "src/app/globals.css"),
  "utf8",
);
const accountCss = readFileSync(resolve(process.cwd(), "src/components/account/account.css"), "utf8");

function declarations(css: string, selector: string) {
  const result: Record<string, string> = {};
  postcss.parse(css).walkRules(selector, (rule) => {
    rule.walkDecls((declaration) => {
      result[declaration.prop] = declaration.value;
    });
  });
  return result;
}

function rule(css: string, selector: string) {
  let result = "";
  postcss.parse(css).walkRules(selector, (candidate) => {
    result = candidate.toString();
  });
  return result;
}

function resolveVars(css: string, tokens: Record<string, string>) {
  return css.replace(
    /var\((--[\w-]+)(?:,\s*([^)]+))?\)/g,
    (_match, token: string, fallback: string | undefined) =>
      tokens[token] ?? fallback ?? "transparent",
  );
}

function contrast(first: string, second: string) {
  const luminance = (rgb: string) => {
    const channels = rgb.match(/[\d.]+/g)?.slice(0, 3).map(Number) ?? [];
    return channels
      .map((value) => value / 255)
      .map((value) =>
        value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4,
      )
      .reduce(
        (sum, value, index) => sum + value * [0.2126, 0.7152, 0.0722][index],
        0,
      );
  };
  const values = [luminance(first), luminance(second)].sort((a, b) => b - a);
  return (values[0] + 0.05) / (values[1] + 0.05);
}

describe("MAX style boundaries", () => {
  beforeEach(() => {
    auth.getSession.mockResolvedValue(null);
    document.head.replaceChildren();
    document.body.replaceChildren();
  });

  it("keeps the real pricing CTA text and icon readable at rest and hover", async () => {
    const tokens = declarations(maxStudioCss, "[data-max-studio]");
    const style = document.createElement("style");
    style.textContent = resolveVars(
      `${maxStudioCss}\n${publicCss.replaceAll(":hover", ".is-hovered")}`,
      tokens,
    );
    document.head.append(style);
    document.body.innerHTML = renderToStaticMarkup(await PricingPage());

    const action = document.querySelector<HTMLElement>(
      ".max-public-content .max-public-button--primary",
    )!;
    const icon = action.querySelector("svg")!;

    for (const hovered of [false, true]) {
      action.classList.toggle("is-hovered", hovered);
      const actionStyle = getComputedStyle(action);
      expect(
        contrast(actionStyle.color, actionStyle.backgroundColor),
        hovered ? "hover" : "rest",
      ).toBeGreaterThanOrEqual(4.5);
      expect(getComputedStyle(icon).color).toBe(actionStyle.color);
    }
  });

  it("keeps admin content in the same readable light theme and marks its current navigation", async () => {
    const style = document.createElement("style");
    style.textContent = resolveVars([
      rule(maxStudioCss, "[data-max-studio]"),
      rule(globalCss, "[data-product-shell]"),
      accountCss,
    ].join("\n"), declarations(maxStudioCss, "[data-max-studio]"));
    document.head.append(style);
    document.body.innerHTML = renderToStaticMarkup(
      await AccountShell({ email: "admin@example.test", active: "admin", children: <input /> }),
    );

    expect(document.querySelector("main[data-product-shell]")).toBeNull();
    expect(document.querySelector('a[href="/admin/max"]')?.getAttribute("aria-current")).toBe("page");
    const shell = document.querySelector<HTMLElement>("[data-max-studio]")!;
    expect(getComputedStyle(shell).colorScheme).toBe("light");
    expect(contrast(getComputedStyle(shell).color, getComputedStyle(shell).backgroundColor)).toBeGreaterThanOrEqual(4.5);

    document.body.innerHTML = renderToStaticMarkup(
      await AccountShell({ email: "user@example.test", active: "profile", children: null }),
    );
    expect(document.querySelector("main[data-product-shell]")).toBeNull();
  });
});
