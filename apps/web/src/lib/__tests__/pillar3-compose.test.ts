import { describe, expect, it } from "vitest";

import type { StreamBrief } from "@/lib/api/types";
import { HEX_RE, briefNarration } from "@/lib/brief-narration";
import { briefSectionPlan, briefSwatches } from "@/lib/brief-swatches";
import {
  JOY_DURATION_MS,
  JOY_FALLBACK_ACCENT,
  buildJoyTrigger,
  joyAccent,
  joyFiresForTurn,
} from "@/lib/joy-moment";

/**
 * V3.11 — PILLAR-3 PROVEN-FINISH (synthetic composition harness, money-free).
 *
 * Mirror of V4.7-SYNTHETIC (apps/api/tests/test_fork_tree_depth2.py): each of
 * the three visible pillar-3 layers — narration (V3.10), brief-swatches (V3.4),
 * joy-moment (V3.8) — already has its OWN isolated unit proof. None proved they
 * COMPOSE: that the hypnosis layers, fired together over ONE build's brief,
 * agree (never show contradictory colours), occupy disjoint time phases (no
 * conflict), and degrade COHERENTLY together on a barren brief instead of
 * crashing or showing half a story. That "code-proven, isolated; never played
 * as one chain" gap is exactly the NORTH STAR pillar-3 hole the plan keeps
 * naming — this harness closes the VISIBLE/creator-facing half of it.
 *
 * Like V4.7 it runs over SYNTHETIC static fixtures (no LLM, no browser, no
 * stream): four niche briefs mirroring the committed frozen-corpus niches
 * (agency / ecommerce / editorial / saas). And like V4.7 it carries the
 * adversarial must-FAIL cases (defaced brief → gate bites; distinct niches →
 * distinct output) so the proof is that the layers DISCRIMINATE, not just
 * always render.
 *
 * The python pillar-3 gates (V3.12 render-registry + V3.3 compose) already fold
 * into the keystone e2e-manifest (V-MANIFEST-COVER). They judge RENDERED HTML;
 * they structurally cannot reach this React reveal layer. This vitest leg is
 * the creator-facing companion proof.
 */

type Niche = {
  key: string;
  brief: StreamBrief;
};

function brief(b: {
  palette: Record<string, string>;
  fonts?: { display?: string; text?: string };
  motion?: string;
  sections?: string[];
}): StreamBrief {
  return {
    palette: b.palette,
    fonts: b.fonts ?? {},
    motion: b.motion ?? "",
    sections: (b.sections ?? []).map((name, i) => ({ id: `s${i}`, name })),
  } as unknown as StreamBrief;
}

/**
 * Four synthetic niche briefs — palette keys use role names so paletteRole
 * orders them deterministically (accent → primary → background). Pairwise
 * distinct accents prove the layers are not hardcoded/memoised across niches.
 */
const NICHES: Niche[] = [
  {
    key: "agency",
    brief: brief({
      palette: { accent: "#FF5A36", primary: "#111111", background: "#FAF7F2" },
      fonts: { display: "Clash Display", text: "Inter" },
      motion: "плавный parallax-reveal секций на скролле",
      sections: ["Hero", "Работы", "Услуги", "Команда", "Контакты"],
    }),
  },
  {
    key: "ecommerce",
    brief: brief({
      palette: { accent: "#16A34A", primary: "#0F172A", background: "#FFFFFF" },
      fonts: { display: "Satoshi", text: "Inter" },
      motion: "карточки товара мягко всплывают по очереди",
      sections: ["Витрина", "Категории", "Хит-продаж", "Корзина", "Отзывы"],
    }),
  },
  {
    key: "editorial",
    brief: brief({
      palette: { accent: "#B91C1C", primary: "#1C1917", background: "#FFFEF9" },
      fonts: { display: "Fraunces", text: "Source Serif" },
      motion: "буквица анимируется, текст проявляется построчно",
      sections: ["Лента", "Рубрики", "Статья", "Подписка"],
    }),
  },
  {
    key: "saas",
    brief: brief({
      palette: { accent: "#7C3AED", primary: "#0B1020", background: "#0E1117" },
      fonts: { display: "Geist", text: "Geist" },
      motion: "градиентный shader тон-в-тон в герое",
      sections: ["Hero", "Возможности", "Тарифы", "Интеграции", "FAQ"],
    }),
  },
];

/** The V3.4 reveal gate: BriefReveal renders only with ≥3 swatches. */
const SWATCH_REVEAL_FLOOR = 3;
/** The V3.10 narration floor mirrored from brief-narration.test.ts. */
const NARRATION_FLOOR = 3;

describe("V3.11 — все три гипноз-слоя СОГЛАСУЮТСЯ на ОДНОМ брифе (per niche)", () => {
  for (const { key, brief: b } of NICHES) {
    it(`${key}: narration ≥3 + swatches ≥3 + joy — все эмиттят на богатом брифе`, () => {
      const narration = briefNarration(b);
      const swatches = briefSwatches(b);
      const joy = buildJoyTrigger(`build-${key}`, "build", b);

      expect(narration.length).toBeGreaterThanOrEqual(NARRATION_FLOOR);
      expect(swatches.length).toBeGreaterThanOrEqual(SWATCH_REVEAL_FLOOR);
      expect(joy).not.toBeNull();
    });

    it(`${key}: лидирующий акцент ЕДИН во всех трёх слоях (0 расхождений)`, () => {
      const swatches = briefSwatches(b);
      const joy = buildJoyTrigger(`build-${key}`, "build", b);
      const narration = briefNarration(b);

      const lead = swatches[0].hex;
      // joy-нота окрашена ровно в лидирующий свотч (joyAccent reuse briefSwatches, R-04)
      expect(joy?.accent).toBe(lead);
      // наррация ВЕДЁТ той же палитрой — первая строка несёт тот же лид-HEX
      expect(narration[0]).toContain(lead);
      // единый источник истины: joyAccent() == первый свотч == лид наррации
      expect(joyAccent(b)).toBe(lead);
    });
  }

  it("ниши попарно РАЗЛИЧНЫ — лид-акцент не захардкожен (adversary)", () => {
    const leads = NICHES.map(({ brief: b }) => briefSwatches(b)[0].hex);
    expect(new Set(leads).size).toBe(NICHES.length);
    // и наррация различается между нишами (не один шаблон на всех)
    const firstLines = NICHES.map(({ brief: b }) => briefNarration(b)[0]);
    expect(new Set(firstLines).size).toBe(NICHES.length);
  });
});

describe("V3.11 — темпоральная НЕ-конфликтность: слои занимают разные фазы", () => {
  it("единственный таймер-слой (joy) ограничен и одноразов на build-complete", () => {
    // joy — ЕДИНСТВЕННЫЙ слой с длительностью; ограничен < 2.5s → не голодает рендер
    expect(JOY_DURATION_MS).toBeLessThan(2500);
    expect(JOY_DURATION_MS).toBeGreaterThan(0);
    // и привязан к build-complete, не к pre-build reveal-фазе
    expect(joyFiresForTurn("build")).toBe(true);
    expect(joyFiresForTurn("edit")).toBe(false);
  });

  it("reveal-слои (narration/swatches) — ЧИСТЫЕ функции брифа, без скрытого таймера", () => {
    // идемпотентность = нет темпорального состояния → доступны синхронно при
    // приходе брифа (pre-build), не конкурируют с joy-фазой (build-complete)
    for (const { brief: b } of NICHES) {
      expect(briefNarration(b)).toEqual(briefNarration(b));
      expect(briefSwatches(b)).toEqual(briefSwatches(b));
      expect(briefSectionPlan(b)).toEqual(briefSectionPlan(b));
    }
  });
});

describe("V3.11 — barren-бриф: КОГЕРЕНТНАЯ деградация (storytelling молчит, joy не падает)", () => {
  const barrens: Array<[string, StreamBrief | null | undefined]> = [
    ["null", null],
    ["undefined", undefined],
    ["пустая палитра", brief({ palette: {} })],
  ];

  for (const [label, b] of barrens) {
    it(`${label}: narration И swatches МОЛЧАТ ВМЕСТЕ (никогда полрассказа)`, () => {
      const narration = briefNarration(b);
      const swatches = briefSwatches(b);
      // оба слоя storytelling гаснут СИНХРОННО — общий контракт пустоты,
      // не «свотчи есть, наррации нет» (это и был бы рассинхрон)
      expect(narration).toEqual([]);
      expect(swatches).toEqual([]);
      expect(narration.length).toBe(swatches.length);
    });

    it(`${label}: joy НЕ падает — деградирует в валидный safe-фолбэк`, () => {
      const accent = joyAccent(b);
      expect(accent).toBe(JOY_FALLBACK_ACCENT);
      // фолбэк — настоящий валидный HEX, не мусор/не undefined
      expect(HEX_RE.test(accent)).toBe(true);
    });
  }
});

describe("V3.11 — adversary: дефейс-бриф ВКЛЮЧАЕТ зубы reveal-гейтов", () => {
  it("палитра из не-HEX мусора → swatches < reveal-floor (BriefReveal молчит)", () => {
    const defaced = brief({
      palette: { accent: "не-цвет", primary: "rgb(1,2,3)", background: "blue" },
      fonts: { display: "Inter" },
      sections: ["Hero", "Цены"],
    });
    // ни один «цвет» не проходит HEX_RE → свотчей нет, гейт BriefReveal кусает
    expect(briefSwatches(defaced).length).toBeLessThan(SWATCH_REVEAL_FLOOR);
    // и наррация роняет палитра-строку (но не падает — остальные строки живут)
    const narration = briefNarration(defaced);
    expect(narration.every((l) => !l.startsWith("Подбираю палитру"))).toBe(true);
  });

  it("захардкоженный пресет-свотч провалил бы per-niche лид-ассерт", () => {
    // если бы joyAccent возвращал фикс-токен, лиды бы СОВПАЛИ между нишами —
    // прямое опровержение «ниши попарно различны» выше; дублируем как явный
    // фальсифик-якорь: фолбэк-акцент НЕ равен ни одному нишевому лиду
    const leads = NICHES.map(({ brief: b }) => joyAccent(b));
    expect(leads).not.toContain(JOY_FALLBACK_ACCENT);
  });
});
