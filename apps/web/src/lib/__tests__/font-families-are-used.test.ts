import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * Каждое семейство шрифтов, объявленное в layout, скачивается из Google ВО ВРЕМЯ
 * СБОРКИ. Значит лишнее семейство — это не лишние килобайты, а ещё один способ
 * уронить выкатку в день, когда Google недоступен: сборка падает целиком, и падает
 * она на проде, посреди волны. Так уже дважды краснел CI 25.09.2026, причём обе
 * попытки подряд.
 *
 * Правило простое: объявили шрифт — покажите строку CSS, которая его читает.
 * До этой проверки в сборку ходили пять семейств, а читались стилями три.
 */
const root = join(__dirname, "..", "..");

const CSS_FILES = [
  "app/globals.css",
  "components/marketing/landing.css",
  "components/marketing/landing/screens.css",
  "components/max/max-studio.css",
  "components/marketing/max-public.css",
];

function declaredFontVariables(): string[] {
  const layout = readFileSync(join(root, "app/layout.tsx"), "utf8");
  return [...layout.matchAll(/variable:\s*"(--font-[a-z0-9-]+)"/g)].map((m) => m[1]);
}

function cssSources(): string {
  return CSS_FILES.map((file) => {
    try {
      return readFileSync(join(root, file), "utf8");
    } catch {
      return "";
    }
  }).join("\n");
}

describe("семейства шрифтов", () => {
  it("каждое объявленное семейство читается хотя бы одной строкой стилей", () => {
    const declared = declaredFontVariables();
    expect(declared.length).toBeGreaterThan(0);

    const css = cssSources();
    const unused = declared.filter((variable) => !css.includes(`var(${variable})`));

    expect(unused, `объявлены в layout, но не используются: ${unused.join(", ")}`).toEqual([]);
  });

  it("проверка смотрит на настоящие файлы стилей, а не на пустоту", () => {
    expect(cssSources().length).toBeGreaterThan(1000);
  });
});
