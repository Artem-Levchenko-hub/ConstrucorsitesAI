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
    expect(tokens["--color-accent"]).toBe("#0381fa");
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

  /**
   * Единственное исключение из запрета оранжевого — файл с нарисованными
   * изображениями товаров на витрине. Правило защищает ИНТЕРФЕЙС: оранжевый
   * спорит с синим брендом и грязнит тёмную тему. Но кофе коричневый, а круассан
   * золотистый, и подгонять картинку еды под палитру значит получить синий
   * круассан. Файл содержит только градиенты-картинки; если в него попадёт
   * кнопка, рамка или подложка — исключение надо отзывать, а не расширять.
   */
  const IMAGERY_ONLY = "components/marketing/landing/imagery.css";

  it("contains no orange color or orange utility in production UI source", () => {
    const violations = collectFiles(SRC).flatMap((path) => {
      if (path.endsWith(IMAGERY_ONLY)) return [];
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

describe("light MAX editor theme", () => {
  const editorCss = readFileSync(resolve(SRC, "components/max/max-editor.css"), "utf8");
  const editorTokens: Record<string, string> = {};
  postcss.parse(editorCss).walkRules("[data-max-editor]", (rule) => {
    rule.walkDecls((declaration) => { editorTokens[declaration.prop] = declaration.value; });
  });
  it("keeps secondary text and primary actions readable on the light canvas", () => {
    // Фон берём из самой темы: после перехода кабинета на тёплую бумагу сайта
    // жёстко вписанный сюда цвет проверял бы несуществующий экран.
    const canvas = editorTokens["--color-bg-base"];
    expect(canvas).toBe("#f9f8f6");
    for (const token of ["--color-fg-primary", "--color-fg-secondary", "--color-fg-tertiary"]) {
      expect(contrast(editorTokens[token], canvas), token).toBeGreaterThanOrEqual(4.5);
    }
    expect(contrast("#ffffff", editorTokens["--color-accent"])).toBeGreaterThanOrEqual(4.5);
    expect(editorTokens["--color-accent-fg"]).toBe(editorTokens["--color-fg-on-accent"]);
  });
  it("overrides the legacy dark composer surface, not just its border", () => {
    let background: string | undefined;
    postcss.parse(editorCss).walkRules("[data-max-editor] .max-studio-prompt > .rounded-lg", (rule) => {
      rule.walkDecls("background", (declaration) => { background = declaration.value; });
    });
    expect(background).toBe("#fff");
  });
});

/**
 * Один акцент, один зелёный, один красный.
 *
 * Аудит 26.09 насчитал в кабинете три синих (#0062ee, #0381fa, #285af0),
 * три зелёных и два красных для одного и того же смысла — интерфейс выглядел
 * собранным из кусков. Цвет задаётся токеном темы, а не литералом в компоненте:
 * тогда светлая и тёмная тема получают верные значения сами.
 */
describe("единая палитра продукта", () => {
  /** Литералы старых состояний, которые заменены токенами. */
  const RETIRED = ["#0381fa", "#285af0", "#248a4b", "#237747", "#c63d35", "#30a56d"];
  /** Определения токенов и запасные картинки — единственные законные места. */
  const TOKEN_FILES = [
    "app/globals.css",
    "components/max/max-studio.css",
    "components/max/max-editor.css",
    "components/marketing/landing/imagery.css",
    "components/marketing/landing/screens.css",
    "app/opengraph-image.tsx",
  ];

  it("не держит отменённые цвета в компонентах кабинета", () => {
    const violations = collectFiles(SRC).flatMap((path) => {
      const relative = path.slice(SRC.length + 1);
      if (TOKEN_FILES.some((allowed) => relative === allowed)) return [];
      if (relative.startsWith("lib/__tests__/")) return [];
      // Внутренние инструменты команды живут своей жизнью и владельцу не видны.
      if (relative.startsWith("components/task-board/") || relative.startsWith("components/account/")) return [];
      if (relative.startsWith("app/mvp/") || relative.startsWith("app/changelog/")) return [];
      const source = readFileSync(path, "utf8");
      return RETIRED.filter((color) => source.toLowerCase().includes(color)).map(
        (color) => `${relative}: ${color}`,
      );
    });

    expect(violations).toEqual([]);
  });
});
