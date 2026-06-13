import { afterEach, describe, expect, it } from "vitest";
import morphdom from "morphdom";

import {
  BOOTSTRAP_HTML,
  buildBootstrap,
} from "@/lib/streaming-preview-bootstrap";

/**
 * V3.0b FRONTEND-HARNESS — вечный ратчет ВИДИМОГО слоя столпа 3.
 *
 * Бутстрап стрим-превью (`streaming-preview-bootstrap.ts`) — это HTML-строка с
 * inline-IIFE, которая внутри iframe-а: (1) морфит DOM in-place через morphdom,
 * (2) СОХРАНЯЕТ уже подставленную картинку через `onBeforeElUpdated`-гард,
 * (3) принимает art-director-бриф в `window.__omniaBrief`. До этого харнесса всё
 * это проверял ТОЛЬКО `tsc`/`next build` = тип-валидность, НОЛЬ поведения. Здесь
 * мы реально исполняем IIFE в jsdom и гоняем `omnia:*`-сообщения, превращая
 * каждый generator-wide live-render-инвариант в falsifiable-ассерт (money-free,
 * 0 LLM, без прод-деплоя).
 */

type OmniaWindow = typeof window & {
  morphdom?: typeof morphdom;
  __omniaImages?: Record<string, string>;
  __omniaBrief?: unknown;
};

const w = window as OmniaWindow;

// Inline-IIFE = единственный <script> БЕЗ атрибутов прямо перед </body> (CDN-
// теги — `<script src=...>`, под этот паттерн не подпадают).
const IIFE = BOOTSTRAP_HTML.match(/<script>([\s\S]*?)<\/script>\s*<\/body>/)![1];
// Контент <body> до inline-скрипта = плейсхолдер (breathe-bar + #omnia-status).
const PLACEHOLDER = BOOTSTRAP_HTML.match(/<body>([\s\S]*?)<script>/)![1];

let activeListeners: EventListener[] = [];

/** Монтирует свежий bootstrap-iframe: чистый DOM + morphdom + исполненный IIFE. */
function boot(): void {
  document.head.innerHTML = '<style id="omnia-css"></style>';
  document.body.innerHTML = PLACEHOLDER;
  delete w.__omniaImages;
  delete w.__omniaBrief;
  w.morphdom = morphdom;

  // Перехватываем message-листенер, который регистрирует IIFE, чтобы afterEach
  // мог его снять — иначе листенеры копятся между тестами и каждый dispatch
  // отрабатывал бы N раз.
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

  // Свежий IIFE = свежее замыкание (placeholderRemoved=false и т.д.).
  new Function(IIFE)();

  window.addEventListener = orig;
}

/** Шлёт postMessage-событие в bootstrap, как делает StreamingPreviewFrame. */
function send(data: unknown): void {
  window.dispatchEvent(new MessageEvent("message", { data }));
}

const HERO_WITH_IMG =
  '<section id="hero"><h1>Привет</h1><img data-omnia-gen></section>';

function genImg(): HTMLImageElement {
  return document.querySelector("img[data-omnia-gen]") as HTMLImageElement;
}

afterEach(() => {
  for (const fn of activeListeners) window.removeEventListener("message", fn);
  activeListeners = [];
});

describe("streaming-preview bootstrap harness (V3.0b)", () => {
  // ── Гейт (a): live-swapped <img> ВЫЖИВАЕТ ≥3 morphdom-патча ──────────────
  // Адверсар плана: удалить `onBeforeElUpdated`-IMG-гард → этот тест краснеет.
  it("(a-guard) preserves a live-swapped image across ≥3 morphdom patches (guard-isolated)", () => {
    boot();
    // Первый стрим-кадр: герой с пустым (src-less) плейсхолдером картинки.
    send({ type: "omnia:render", bodyHtml: HERO_WITH_IMG, cssText: "" });
    expect(document.getElementById("omnia-placeholder")).toBeNull();
    const img = genImg();
    expect(img).toBeTruthy();
    expect(img.getAttribute("src")).toBeFalsy();

    // ВАЖНО: ставим src НАПРЯМУЮ, не через omnia:image → карта __omniaImages
    // остаётся пустой, поэтому страховочный reapplyImages() НЕ может замаскировать
    // отсутствие гарда. Выживание src здесь зависит ИСКЛЮЧИТЕЛЬНО от
    // onBeforeElUpdated-гарда → чистая falsifiable-изоляция.
    img.setAttribute("src", "https://cdn.test/direct.jpg");
    expect(w.__omniaImages ?? {}).toEqual({});

    // 3 последующих стрим-кадра несут тот же src-less плейсхолдер.
    for (let i = 0; i < 3; i++) {
      send({ type: "omnia:render", bodyHtml: HERO_WITH_IMG, cssText: "" });
    }
    // morphdom — это update-in-place → ссылка на узел стабильна в обоих исходах.
    expect(genImg()).toBe(img);
    expect(img.getAttribute("src")).toBe("https://cdn.test/direct.jpg");
  });

  it("(a-e2e) preserves a resolved image through the real omnia:image flow", () => {
    boot();
    send({ type: "omnia:render", bodyHtml: HERO_WITH_IMG, cssText: "" });
    send({ type: "omnia:image", idx: 0, url: "https://cdn.test/p.jpg" });
    expect(genImg().getAttribute("src")).toBe("https://cdn.test/p.jpg");
    for (let i = 0; i < 3; i++) {
      send({ type: "omnia:render", bodyHtml: HERO_WITH_IMG, cssText: "" });
    }
    expect(genImg().getAttribute("src")).toBe("https://cdn.test/p.jpg");
  });

  // ── Гейт (b): omnia:brief → window.__omniaBrief непустой ─────────────────
  it("(b) stashes the art-director brief into window.__omniaBrief", () => {
    boot();
    expect(w.__omniaBrief).toBeUndefined();

    const brief = {
      palette: ["#0a0a0a", "#fafafa", "#6366f1"],
      tone: "warm-minimal",
      sections: ["hero", "features", "pricing"],
    };
    send({ type: "omnia:brief", brief });

    expect(w.__omniaBrief).toBeTruthy();
    expect(w.__omniaBrief).toEqual(brief);

    // Пустой бриф нормализуется в null (а не undefined/throw).
    send({ type: "omnia:brief" });
    expect(w.__omniaBrief).toBeNull();
  });

  // ── Гейт (b2): omnia:status РЕНДЕРИТСЯ в #omnia-status (V3.10 render-path) ─
  // Доказывает, что наррация-строки (StreamingPreviewFrame → briefNarration)
  // реально попадают в видимый лейбл плейсхолдера, а не теряются.
  it("(b2) renders an omnia:status narration line into #omnia-status", () => {
    boot();
    const label = () =>
      document.getElementById("omnia-status")?.textContent ?? "";
    expect(label()).toBe("AI пишет ответ");

    send({ type: "omnia:status", text: "Подбираю палитру — #ff5a36 и #0a0a0a" });
    expect(label()).toBe("Подбираю палитру — #ff5a36 и #0a0a0a");

    // Следующая строка наррации сменяет предыдущую (последовательная подача).
    send({ type: "omnia:status", text: "Беру шрифт «Playfair Display»" });
    expect(label()).toBe("Беру шрифт «Playfair Display»");
  });

  // ── Гейт (c): .reveal остаётся ВИДИМЫМ (грузим kit-CSS, НИКОГДА kit-JS) ───
  it("(c-contract) buildBootstrap links kit CSS and never injects kit JS", () => {
    const out = buildBootstrap("https://api.example.com/");
    expect(out).toContain(
      '<link rel="stylesheet" href="https://api.example.com/api/kit/omnia-kit.css">',
    );
    // Никакого нового <script>: число script-тегов не меняется vs базовый HTML.
    const countScripts = (s: string) => (s.match(/<script/g) ?? []).length;
    expect(countScripts(out)).toBe(countScripts(BOOTSTRAP_HTML));
    // И буквально никакого .js-ассета кита (только CSS).
    expect(out).not.toMatch(/kit[^"']*\.js/);
    // Пустой origin → graceful-фолбэк на чистый бутстрап.
    expect(buildBootstrap("")).toBe(BOOTSTRAP_HTML);
  });

  it("(c-render) keeps a streamed .reveal node present and unhidden", () => {
    boot();
    send({
      type: "omnia:render",
      bodyHtml: '<section class="reveal" id="r"><p>живой контент</p></section>',
      cssText: "",
    });
    const r = document.getElementById("r");
    expect(r).toBeTruthy();
    expect(r!.classList.contains("reveal")).toBe(true);
    // Бутстрап сам НЕ прячет .reveal (скрытый старт гейтится классом, который
    // добавляет ТОЛЬКО kit-JS, а его мы намеренно не грузим).
    const inlineStyle = r!.getAttribute("style") ?? "";
    expect(inlineStyle).not.toMatch(
      /opacity\s*:\s*0|display\s*:\s*none|visibility\s*:\s*hidden/,
    );
    expect(r!.hasAttribute("hidden")).toBe(false);
  });
});
