import { describe, expect, it } from "vitest";

import { briefSectionPlan, briefSwatches } from "@/lib/brief-swatches";
import type { StreamBrief } from "@/lib/api/types";

/**
 * V3.4 SURFACING — falsifiable ратчет видимого «AI рисует»-брифа.
 *
 * Гейт: чат показывает ≥3 свотча, чьи HEX извлечены ИЗ `brief.palette` (не из
 * фиксированной preset-палитры), + план секций. Здесь money-free проверяем
 * чистые экстракторы: свотчи = HEX брифа (adversary: другой бриф ⇒ другие HEX;
 * захардкоженная preset-палитра провалит этот тест), пустой бриф молчит.
 */

const LANDING: StreamBrief = {
  palette: {
    "ФОН": "#0a0a0a",
    "ТЕКСТ": "#fafafa",
    "PRIMARY": "#1d4ed8",
    "АКЦЕНТ": "#ff5a36",
  },
  fonts: { display: "Playfair Display", text: "Inter" },
  motion: ".omnia-shader тон-в-тон в герое",
  sections: [
    { id: "hero", name: "Герой" },
    { id: "services", name: "Услуги" },
    { id: "contacts", name: "Контакты" },
  ],
};

describe("briefSwatches (V3.4)", () => {
  it("emits ≥3 swatches whose HEX are taken FROM the brief palette", () => {
    const sw = briefSwatches(LANDING);
    expect(sw.length).toBeGreaterThanOrEqual(3);
    const hexes = sw.map((s) => s.hex);
    // Каждый HEX — дословно из брифа (surfaced, не выдуман).
    for (const h of hexes) {
      expect(Object.values(LANDING.palette)).toContain(h);
    }
    // Акцент ведёт (приоритет роли), как и в наррации.
    expect(sw[0].hex).toBe("#ff5a36");
    expect(sw[0].label).toBe("Акцент");
  });

  it("ADVERSARY: a hardcoded preset palette would fail — output equals THIS brief", () => {
    const a = briefSwatches(LANDING).map((s) => s.hex);
    const other: StreamBrief = {
      palette: { "PRIMARY": "#14532d", "FOREGROUND": "#f5f5f4", "АКЦЕНТ": "#b91c1c" },
      fonts: { display: "Fraunces", text: "Source Sans 3" },
      motion: ".fx-waves органик",
      sections: [
        { id: "menu", name: "Меню" },
        { id: "booking", name: "Бронь" },
      ],
    };
    const b = briefSwatches(other).map((s) => s.hex);
    expect(a).not.toEqual(b);
    // B несёт HEX B, а НЕ A — иначе свотчи захардкожены.
    expect(b).toContain("#14532d");
    expect(b).toContain("#b91c1c");
    expect(b).not.toContain("#ff5a36");
    expect(b).not.toContain("#1d4ed8");
  });

  it("skips non-hex palette values and dedupes identical colours", () => {
    const sw = briefSwatches({
      palette: {
        "ФОН": "not-a-color",
        "PRIMARY": "#abc123",
        "АКЦЕНТ": "#ABC123", // тот же цвет (регистр) → один свотч
        "ТЕКСТ": "#123456",
      },
      fonts: {},
      motion: "",
      sections: [],
    });
    const hexes = sw.map((s) => s.hex);
    expect(hexes).not.toContain("not-a-color");
    // #abc123 / #ABC123 дедуплицированы → ровно 2 уникальных цвета.
    expect(hexes.length).toBe(2);
    expect(hexes).toContain("#123456");
  });

  it("returns [] for an empty or null brief (fail-soft, reveal stays silent)", () => {
    expect(briefSwatches(null)).toEqual([]);
    expect(briefSwatches(undefined)).toEqual([]);
    expect(
      briefSwatches({ palette: {}, fonts: {}, motion: "", sections: [] }),
    ).toEqual([]);
  });
});

describe("briefSectionPlan (V3.4)", () => {
  it("returns the brief's section names, deduped", () => {
    expect(briefSectionPlan(LANDING)).toEqual(["Герой", "Услуги", "Контакты"]);
    expect(
      briefSectionPlan({
        ...LANDING,
        sections: [
          { id: "a", name: "Меню" },
          { id: "b", name: "Меню" },
          { id: "c", name: "Контакты" },
        ],
      }),
    ).toEqual(["Меню", "Контакты"]);
  });

  it("returns [] for an empty or null brief", () => {
    expect(briefSectionPlan(null)).toEqual([]);
    expect(briefSectionPlan({ palette: {}, fonts: {}, motion: "", sections: [] })).toEqual([]);
  });
});
