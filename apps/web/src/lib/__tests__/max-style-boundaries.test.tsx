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

  it("restores the complete legacy dark token boundary only for admin content", async () => {
    const style = document.createElement("style");
    style.textContent = [
      rule(maxStudioCss, "[data-max-studio]"),
      rule(globalCss, "[data-product-shell]"),
    ].join("\n");
    document.head.append(style);
    document.body.innerHTML = renderToStaticMarkup(
      await AccountShell({ email: "admin@example.test", active: "admin", children: <input /> }),
    );

    const admin = document.querySelector<HTMLElement>("main[data-product-shell]");
    expect(admin).not.toBeNull();
    const computed = getComputedStyle(admin!);
    expect(computed.colorScheme).toBe("dark");
    expect(computed.getPropertyValue("--color-ink")).toBe("#ffffff");
    expect(computed.getPropertyValue("--color-warm-white")).toBe("#121519");
    expect(computed.getPropertyValue("--color-label-1")).toBe("#ffffff");
    expect(computed.getPropertyValue("--color-surface-input")).toBe("#2b2d32");
    expect(computed.getPropertyValue("--color-accent-fg")).toBe("#121519");
    expect(computed.getPropertyValue("--color-success-fg")).toBe("#4ade80");
    expect(computed.getPropertyValue("--color-danger-fg")).toBe("#f87171");
    expect(computed.getPropertyValue("--color-warning")).toBe("#e8c547");

    document.body.innerHTML = renderToStaticMarkup(
      await AccountShell({ email: "user@example.test", active: "profile", children: null }),
    );
    expect(document.querySelector("main[data-product-shell]")).toBeNull();
  });
});
