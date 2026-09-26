import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * Две вещи, которые здесь охраняются.
 *
 * Первая: сборка не должна ходить за шрифтами наружу. Пока семейства
 * подключались через `next/font/google`, КАЖДАЯ сборка образа — и в проверке, и
 * на проде посреди выкатки — зависела от доступности чужого сервиса. 25.09.2026
 * это дважды подряд уронило сборку, включая повторную попытку. Теперь файлы
 * лежат в репозитории, и возврат к загрузке по сети должен быть заметен сразу.
 *
 * Вторая: каждое объявленное семейство должно кем-то использоваться. Лишний
 * шрифт — это лишние килобайты у посетителя и лишний файл в сборке; до уборки
 * 25.09 объявлено было пять семейств, а читалось стилями три.
 */
const root = join(__dirname, "..", "..");

const CSS_FILES = [
  "app/globals.css",
  "components/marketing/landing.css",
  "components/marketing/landing/screens.css",
  "components/max/max-studio.css",
  "components/marketing/max-public.css",
];

function fontsCss(): string {
  return readFileSync(join(root, "app/fonts.css"), "utf8");
}

function declaredFontVariables(): string[] {
  return [...fontsCss().matchAll(/^\s*(--font-[a-z0-9-]+):/gm)].map((m) => m[1]);
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

describe("шрифты", () => {
  it("не скачиваются во время сборки", () => {
    const layout = readFileSync(join(root, "app/layout.tsx"), "utf8");

    expect(layout).not.toContain("next/font/google");
    // Файлы должны браться из репозитория — относительным путём, а не по сети.
    expect(fontsCss()).toContain('url("./fonts/');
    expect(fontsCss()).not.toContain("https://");
  });

  it("каждое объявленное семейство читается хотя бы одной строкой стилей", () => {
    const declared = declaredFontVariables();
    expect(declared.length).toBeGreaterThan(0);

    const css = cssSources();
    const unused = declared.filter((variable) => !css.includes(`var(${variable})`));

    expect(unused, `объявлены, но не используются: ${unused.join(", ")}`).toEqual([]);
  });

  it("у каждого семейства есть и латиница, и кириллица", () => {
    const css = fontsCss();
    for (const family of ["inter", "onest", "jetbrains-mono"]) {
      expect(css, `${family}: нет латиницы`).toContain(`./fonts/${family}-latin.woff2`);
      expect(css, `${family}: нет кириллицы`).toContain(`./fonts/${family}-cyrillic.woff2`);
    }
  });

  it("проверка смотрит на настоящие файлы стилей, а не на пустоту", () => {
    expect(cssSources().length).toBeGreaterThan(1000);
  });
});
