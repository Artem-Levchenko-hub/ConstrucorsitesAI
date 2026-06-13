/**
 * V3.2b — MORPH-FINAL ≡ COMMITTED PARITY-SCORER.
 *
 * Столп 3 («магия live-рендера») собирается убить reveal-swap (V3.2c): на
 * `llm.done` НЕ монтировать свежий `/p/<slug>`-iframe поверх morph'нутого
 * streaming-iframe, а оставить живой morph-холст как финальное превью. Это
 * безопасно ТОЛЬКО если DOM, который юзер видел рождающимся (morph-холст),
 * структурно идентичен тому, что сервер реально отдаёт на `/p/<slug>`. Иначе
 * «no-swap» заморозит ДИВЕРГЕНТНУЮ страницу (morphdom-артефакты, неразрешённые
 * img-плейсхолдеры, kit-JS reveal-эффекты, которые bootstrap намеренно подавил).
 *
 * Этот модуль — чистый детерминированный скорер (R-04, money-free, 0 LLM):
 * извлекает структурную сигнатуру из любого DOM-дерева + его CSS и сравнивает
 * две сигнатуры по 4 осям спеки V3.2b — count секций, порядок секций,
 * цвет-токен (`--primary`/`--accent`), font-family-стек. `>1 mismatch = fail`
 * (толеранс 1). Плюс ЖЁСТКИЙ гейт: на финальном холсте должно остаться 0
 * src-less `data-omnia-gen`-плейсхолдеров (любой → fail независимо от
 * structural-диффа).
 *
 * V3.2c (kill-swap) и V3.5 (container hand-off) переиспользуют ЭТОТ скорер, а не
 * изобретают свой — единый parity-контракт на весь live-render-слой.
 */

/** Структурный отпечаток одного отрендеренного документа. */
export interface RenderSignature {
  /** Упорядоченные сигнатуры секций (data-section || id || tagName). */
  sections: string[];
  /** Резолвнутый `--primary`-токен, нормализованный (lowercase/trim). */
  primary: string | null;
  /** Резолвнутый `--accent`-токен, нормализованный. */
  accent: string | null;
  /** Первый font-family-стек, нормализованный (без кавычек, схлопнутые пробелы). */
  fontFamily: string | null;
  /** Число `<img data-omnia-gen>` без непустого `src` (неразрешённые плейсхолдеры). */
  unresolvedImages: number;
}

/** Результат parity-сравнения двух сигнатур. */
export interface ParityResult {
  /** Список разошедшихся осей (`section-count` | `section-order` | `color-token` | `font-family`). */
  mismatches: string[];
  /** Финальный холст несёт неразрешённые плейсхолдеры (жёсткий fail). */
  unresolvedPlaceholders: number;
  /** Зелёный = ≤1 structural-mismatch И 0 неразрешённых плейсхолдеров на финале. */
  pass: boolean;
}

// Лендмарк/структурные элементы, формирующие «секции» страницы. Любой узел с
// явным `data-section` тоже считается секцией, где бы он ни лежал.
const SECTION_SELECTOR =
  "[data-section], section, header, footer, main, article";

function normToken(value: string | null | undefined): string | null {
  if (value == null) return null;
  const v = value.replace(/\s+/g, " ").trim().toLowerCase();
  return v.length ? v : null;
}

function normFont(value: string | null | undefined): string | null {
  if (value == null) return null;
  const v = value
    .replace(/["']/g, "")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();
  return v.length ? v : null;
}

/**
 * Первое значение CSS-custom-property/декларации в склеенном CSS-тексте.
 * Регекс по тексту, а не `getComputedStyle` — jsdom не резолвит custom-props из
 * `<style>`, а скорер должен быть детерминированным в обоих рантаймах (jsdom +
 * браузер). Берём ПЕРВОЕ объявление (обычно `:root`).
 */
function firstDecl(css: string, prop: string): string | null {
  const re = new RegExp(prop.replace(/[-]/g, "\\-") + "\\s*:\\s*([^;}\\n]+)", "i");
  const m = css.match(re);
  return m ? m[1].trim() : null;
}

/**
 * Извлекает структурную сигнатуру из корня поддерева + его CSS-текста.
 *
 * @param root  Корень DOM (`document.body` для morph-холста; `doc.body` от
 *              DOMParser для server-render).
 * @param css   Склеенный CSS-текст (содержимое `#omnia-css` / всех `<style>`).
 */
export function extractRenderSignature(
  root: ParentNode,
  css: string,
): RenderSignature {
  const sections: string[] = [];
  root.querySelectorAll(SECTION_SELECTOR).forEach((el) => {
    const sig =
      el.getAttribute("data-section") ||
      (el as HTMLElement).id ||
      el.tagName.toLowerCase();
    sections.push(sig);
  });

  let unresolvedImages = 0;
  root.querySelectorAll("img[data-omnia-gen]").forEach((img) => {
    const src = img.getAttribute("src");
    if (!src) unresolvedImages += 1;
  });

  const cssText = css ?? "";
  return {
    sections,
    primary: normToken(firstDecl(cssText, "--primary")),
    accent: normToken(firstDecl(cssText, "--accent")),
    fontFamily: normFont(
      firstDecl(cssText, "--font") ?? firstDecl(cssText, "font-family"),
    ),
    unresolvedImages,
  };
}

/** Парсит HTML-строку server-render'а (`/p/<slug>`) в сигнатуру. */
export function signatureFromHtml(html: string): RenderSignature {
  const doc = new DOMParser().parseFromString(html, "text/html");
  const css = Array.from(doc.querySelectorAll("style"))
    .map((s) => s.textContent ?? "")
    .join("\n");
  return extractRenderSignature(doc.body, css);
}

/**
 * Сравнивает финальный morph-холст с committed server-render по 4 осям V3.2b.
 * `>1 mismatch = fail` (толеранс 1) + жёсткий гейт на 0 неразрешённых
 * плейсхолдеров финала.
 *
 * @param finalCanvas  Сигнатура DOM, который юзер видел рождающимся (morph).
 * @param committed    Сигнатура свежего server-render `/p/<slug>`.
 */
export function scoreParity(
  finalCanvas: RenderSignature,
  committed: RenderSignature,
): ParityResult {
  const mismatches: string[] = [];

  if (finalCanvas.sections.length !== committed.sections.length) {
    mismatches.push("section-count");
  } else if (
    finalCanvas.sections.join("") !== committed.sections.join("")
  ) {
    mismatches.push("section-order");
  }

  if (
    finalCanvas.primary !== committed.primary ||
    finalCanvas.accent !== committed.accent
  ) {
    mismatches.push("color-token");
  }

  if (finalCanvas.fontFamily !== committed.fontFamily) {
    mismatches.push("font-family");
  }

  const unresolvedPlaceholders = finalCanvas.unresolvedImages;
  const pass = mismatches.length <= 1 && unresolvedPlaceholders === 0;

  return { mismatches, unresolvedPlaceholders, pass };
}
