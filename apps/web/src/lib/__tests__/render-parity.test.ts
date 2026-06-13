import { afterEach, describe, expect, it } from "vitest";
import morphdom from "morphdom";

import { BOOTSTRAP_HTML } from "@/lib/streaming-preview-bootstrap";
import {
  extractRenderSignature,
  scoreParity,
  signatureFromHtml,
} from "@/lib/render-parity";

/**
 * V3.2b — MORPH-FINAL ≡ COMMITTED PARITY-GATE (frontend-harness ратчет, money-
 * free, 0 LLM). Делает будущий kill-swap (V3.2c) БЕЗОПАСНЫМ: доказывает, что
 * DOM, который юзер видел рождающимся на morph-холсте, структурно идентичен
 * server-render'у `/p/<slug>`. Скорер гоняется против РЕАЛЬНОГО morph-рантайма
 * (bootstrap IIFE + morphdom в jsdom), а не синтетики — поэтому ловит реальную
 * дивергенцию morphdom-патчинга / неразрешённых плейсхолдеров.
 *
 * Адверсар: морф, который роняет/переставляет секцию, мутирует `--primary` или
 * оставляет src-less `data-omnia-gen` — ОБЯЗАН провалить parity, иначе no-swap
 * заморозит неверную страницу.
 */

type OmniaWindow = typeof window & {
  morphdom?: typeof morphdom;
  __omniaImages?: Record<string, string>;
  __omniaBrief?: unknown;
};

const w = window as OmniaWindow;

const IIFE = BOOTSTRAP_HTML.match(/<script>([\s\S]*?)<\/script>\s*<\/body>/)![1];
const PLACEHOLDER = BOOTSTRAP_HTML.match(/<body>([\s\S]*?)<script>/)![1];

let activeListeners: EventListener[] = [];

/** Монтирует свежий bootstrap-iframe: чистый DOM + morphdom + исполненный IIFE. */
function boot(): void {
  document.head.innerHTML = '<style id="omnia-css"></style>';
  document.body.innerHTML = PLACEHOLDER;
  delete w.__omniaImages;
  delete w.__omniaBrief;
  w.morphdom = morphdom;

  const orig = window.addEventListener.bind(window);
  window.addEventListener = function (
    type: string,
    fn: EventListenerOrEventListenerObject,
    opts?: boolean | AddEventListenerOptions,
  ) {
    if (type === "message" && typeof fn === "function") {
      activeListeners.push(fn as EventListener);
    }
    return orig(type, fn, opts);
  } as typeof window.addEventListener;

  new Function(IIFE)();
  window.addEventListener = orig;
}

function send(data: unknown): void {
  window.dispatchEvent(new MessageEvent("message", { data }));
}

/** Сигнатура того, что юзер видит ВЖИВУЮ на morph-холсте (document.body + #omnia-css). */
function morphFinalSignature() {
  const css = document.getElementById("omnia-css")?.textContent ?? "";
  return extractRenderSignature(document.body, css);
}

// Сгенерированное тело (3 секции) + его CSS-токены. ИМЕННО это бэкенд и
// застримит кадрами, и закоммитит в `/p/<slug>` — поэтому morph-final обязан
// совпасть со server-render'ом этой же разметки.
const BODY_HTML =
  '<header id="hero"><h1>Кофейня</h1></header>' +
  '<section data-section="menu"><h2>Меню</h2></section>' +
  '<footer data-section="contacts"><p>Адрес</p></footer>';
const CSS = ":root{--primary:#0A0A0A;--accent:#6366F1;--font:'Playfair Display', serif}";

/** Полный server-render `/p/<slug>` той же разметки (как отдаёт бэкенд). */
function serverHtml(body: string, css: string): string {
  return `<!doctype html><html><head><style>${css}</style></head><body>${body}</body></html>`;
}

afterEach(() => {
  for (const fn of activeListeners) window.removeEventListener("message", fn);
  activeListeners = [];
});

describe("render-parity scorer (V3.2b — morph-final ≡ committed)", () => {
  // ── PASS: реальный morph-холст структурно == server-render той же разметки ──
  it("(pass) morph-final canvas matches the committed server render", () => {
    boot();
    // Стримим разметку кадрами (как бэкенд: частичные → полный).
    send({ type: "omnia:render", bodyHtml: '<header id="hero"><h1>Кофейня</h1></header>', cssText: CSS });
    send({ type: "omnia:render", bodyHtml: BODY_HTML, cssText: CSS });

    const final = morphFinalSignature();
    const committed = signatureFromHtml(serverHtml(BODY_HTML, CSS));

    // Сигнатура реально извлеклась (не пустышка).
    expect(final.sections).toEqual(["hero", "menu", "contacts"]);
    expect(final.primary).toBe("#0a0a0a");
    expect(final.fontFamily).toBe("playfair display, serif");
    expect(final.unresolvedImages).toBe(0);

    const result = scoreParity(final, committed);
    expect(result.mismatches).toEqual([]);
    expect(result.pass).toBe(true);
  });

  // ── FAIL (адверсар): server-render разошёлся по ≥2 осям → parity краснеет ──
  it("(fail-divergent) flags ≥2-axis divergence as a parity failure", () => {
    boot();
    send({ type: "omnia:render", bodyHtml: BODY_HTML, cssText: CSS });
    const final = morphFinalSignature();

    // Committed уронил секцию (count) И сменил primary (color-token) = 2 оси.
    const divergentBody =
      '<header id="hero"><h1>Кофейня</h1></header>' +
      '<section data-section="menu"><h2>Меню</h2></section>';
    const divergentCss = ":root{--primary:#ff0000;--accent:#6366F1;--font:'Playfair Display', serif}";
    const committed = signatureFromHtml(serverHtml(divergentBody, divergentCss));

    const result = scoreParity(final, committed);
    expect(result.mismatches).toContain("section-count");
    expect(result.mismatches).toContain("color-token");
    expect(result.mismatches.length).toBeGreaterThan(1);
    expect(result.pass).toBe(false);
  });

  // ── Толеранс: ровно 1 ось разошлась → parity ещё ЗЕЛЁНЫЙ (спека «>1 = fail») ──
  it("(tolerance) a single-axis diff stays within tolerance (>1 = fail)", () => {
    boot();
    send({ type: "omnia:render", bodyHtml: BODY_HTML, cssText: CSS });
    const final = morphFinalSignature();

    // Отличается ТОЛЬКО font — 1 mismatch, в пределах толеранса.
    const committed = signatureFromHtml(
      serverHtml(BODY_HTML, ":root{--primary:#0A0A0A;--accent:#6366F1;--font:Inter, sans-serif}"),
    );
    const result = scoreParity(final, committed);
    expect(result.mismatches).toEqual(["font-family"]);
    expect(result.pass).toBe(true);
  });

  // ── ЖЁСТКИЙ гейт: src-less data-omnia-gen на финале → fail даже при 0 structural ──
  it("(hard-gate) any unresolved data-omnia-gen placeholder fails parity", () => {
    boot();
    // Финальный кадр НЕСЁТ неразрешённый плейсхолдер (картинка не подъехала).
    const bodyWithImg = BODY_HTML.replace(
      "<h2>Меню</h2>",
      '<h2>Меню</h2><img data-omnia-gen alt="dish">',
    );
    send({ type: "omnia:render", bodyHtml: bodyWithImg, cssText: CSS });
    const final = morphFinalSignature();
    expect(final.unresolvedImages).toBe(1);

    // Committed-render идентичен по структуре (0 structural-mismatch)…
    const committed = signatureFromHtml(serverHtml(bodyWithImg, CSS));
    const result = scoreParity(final, committed);
    expect(result.mismatches).toEqual([]);
    // …но неразрешённый плейсхолдер = жёсткий fail.
    expect(result.unresolvedPlaceholders).toBe(1);
    expect(result.pass).toBe(false);
  });

  // ── Резолв картинки снимает hard-gate: morph-preserve src → parity зеленеет ──
  it("(hard-gate-clears) a resolved image lifts the placeholder fail", () => {
    boot();
    const bodyWithImg = BODY_HTML.replace(
      "<h2>Меню</h2>",
      '<h2>Меню</h2><img data-omnia-gen alt="dish">',
    );
    send({ type: "omnia:render", bodyHtml: bodyWithImg, cssText: CSS });
    // Картинка подъехала через реальный omnia:image-флоу (morph её сохраняет).
    send({ type: "omnia:image", idx: 0, url: "https://cdn.test/dish.jpg" });
    const final = morphFinalSignature();
    expect(final.unresolvedImages).toBe(0);

    const committedBody = bodyWithImg.replace(
      "<img data-omnia-gen",
      '<img data-omnia-gen src="https://cdn.test/dish.jpg"',
    );
    const result = scoreParity(final, signatureFromHtml(serverHtml(committedBody, CSS)));
    expect(result.pass).toBe(true);
  });
});
