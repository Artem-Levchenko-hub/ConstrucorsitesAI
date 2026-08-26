import { readFileSync, readdirSync, statSync } from "node:fs";
import { extname, join, resolve } from "node:path";

import postcss from "postcss";
import { describe, expect, it } from "vitest";

const SRC = resolve(process.cwd(), "src");
const GLOBALS = resolve(SRC, "app/globals.css");

function collectFiles(directory: string): string[] {
  return readdirSync(directory).flatMap((entry) => {
    const path = join(directory, entry);
    if (statSync(path).isDirectory()) {
      return entry === "__tests__" ? [] : collectFiles(path);
    }
    return [".css", ".svg", ".ts", ".tsx"].includes(extname(path)) ? [path] : [];
  });
}

function themeTokens(css: string): Record<string, string> {
  const tokens: Record<string, string> = {};
  postcss.parse(css).walkAtRules("theme", (theme) => {
    theme.walkDecls((declaration) => {
      tokens[declaration.prop] = declaration.value.toLowerCase();
    });
  });
  return tokens;
}

function channel(value: number): number {
  const srgb = value / 255;
  return srgb <= 0.04045
    ? srgb / 12.92
    : ((srgb + 0.055) / 1.055) ** 2.4;
}

function luminance(hex: string): number {
  const rgb = [1, 3, 5].map((index) =>
    Number.parseInt(hex.slice(index, index + 2), 16),
  );
  return 0.2126 * channel(rgb[0]) + 0.7152 * channel(rgb[1]) + 0.0722 * channel(rgb[2]);
}

function contrast(first: string, second: string): number {
  const values = [luminance(first), luminance(second)].sort((a, b) => b - a);
  return (values[0] + 0.05) / (values[1] + 0.05);
}

function isOrangeRgb(redChannel: number, greenChannel: number, blueChannel: number): boolean {
  const [red, green, blue] = [redChannel, greenChannel, blueChannel].map(
    (value) => value / 255,
  );
  const max = Math.max(red, green, blue);
  const min = Math.min(red, green, blue);
  const delta = max - min;
  if (delta === 0) return false;

  let hue: number;
  if (max === red) hue = 60 * (((green - blue) / delta) % 6);
  else if (max === green) hue = 60 * ((blue - red) / delta + 2);
  else hue = 60 * ((red - green) / delta + 4);
  if (hue < 0) hue += 360;

  const lightness = (max + min) / 2;
  const saturation = delta / (1 - Math.abs(2 * lightness - 1));
  return hue >= 8 && hue <= 45 && saturation >= 0.5 && lightness >= 0.25;
}

function isOrange(hex: string): boolean {
  const [red, green, blue] = [1, 3, 5].map((index) =>
    Number.parseInt(hex.slice(index, index + 2), 16),
  );
  return isOrangeRgb(red, green, blue);
}

describe("dark blue product theme", () => {
  const css = readFileSync(GLOBALS, "utf8");
  const tokens = themeTokens(css);

  it("uses the approved reference palette", () => {
    expect(tokens["--color-bg-base"]).toBe("#121519");
    expect(tokens["--color-bg-elevated-1"]).toBe("#191b20");
    expect(tokens["--color-surface-3"]).toBe("#2b2d32");
    expect(tokens["--color-accent"]).toBe("#4f81f7");
    expect(tokens["--color-system-indigo"]).toBe("#6366f1");
    expect(tokens["--color-fg-primary"]).toBe("#ffffff");
    expect(tokens["--color-fg-secondary"]).toBe("#9fa1b1");
  });

  it("keeps normal text readable on every primary surface", () => {
    expect(contrast(tokens["--color-fg-primary"], tokens["--color-bg-base"])).toBeGreaterThanOrEqual(7);
    expect(contrast(tokens["--color-fg-secondary"], tokens["--color-bg-base"])).toBeGreaterThanOrEqual(4.5);
    expect(contrast(tokens["--color-fg-secondary"], tokens["--color-bg-elevated-1"])).toBeGreaterThanOrEqual(4.5);
    expect(contrast(tokens["--color-fg-on-accent"], tokens["--color-accent"])).toBeGreaterThanOrEqual(4.5);
    expect(contrast(tokens["--color-success-fg"], tokens["--color-bg-elevated-1"])).toBeGreaterThanOrEqual(4.5);
    expect(contrast(tokens["--color-danger-fg"], tokens["--color-bg-elevated-1"])).toBeGreaterThanOrEqual(4.5);
    expect(tokens["--color-fg-on-danger"]).toBe("#ffffff");
    expect(contrast(tokens["--color-fg-on-danger"], tokens["--color-danger"])).toBeGreaterThanOrEqual(4.5);
  });

  it("contains no orange color or orange utility in production UI source", () => {
    const violations = collectFiles(SRC).flatMap((path) => {
      const source = readFileSync(path, "utf8");
      const colors = source.match(/#[0-9a-f]{6}\b/gi) ?? [];
      const orangeColors = colors.filter((color) => isOrange(color));
      const functionalColors = source.match(/rgba?\([^)]*\)/gi) ?? [];
      const orangeFunctionalColors = functionalColors.filter((color) => {
        const channels = color.match(/\d*\.?\d+/g)?.slice(0, 3).map(Number);
        return channels?.length === 3 && isOrangeRgb(channels[0], channels[1], channels[2]);
      });
      const orangeUtilities = source.match(/(?:orange|amber)-\d{2,3}/gi) ?? [];
      return [...new Set([...orangeColors, ...orangeFunctionalColors, ...orangeUtilities])].map(
        (value) => `${path.slice(SRC.length + 1)}: ${value}`,
      );
    });

    expect(violations).toEqual([]);
  });

  it("uses accessible semantic text colors instead of legacy status fills", () => {
    const violations = collectFiles(SRC).flatMap((path) => {
      const source = readFileSync(path, "utf8");
      return source.match(/text-\[#(?:248a4b|c63d35|a9302a)\]/gi)?.map(
        (value) => `${path.slice(SRC.length + 1)}: ${value}`,
      ) ?? [];
    });

    expect(violations).toEqual([]);
  });
});
