import { describe, expect, it } from "vitest";

import { briefNarration } from "@/lib/brief-narration";
import type { StreamBrief } from "@/lib/api/types";

/**
 * V3.10 NARRATION-AS-CODESIGNER — falsifiable ратчет ВИДИМОГО гипноз-слоя.
 *
 * Гейт: во время стрима ≥3 РАЗНЫХ человекочитаемых строки, КАЖДАЯ выведенная из
 * поля брифа (палитра/шрифт/секции/motion), → доказывает, что бриф SURFACED, а
 * не выдуман клиентом. Здесь проверяем чистую narration-функцию money-free:
 * строки выводятся ИЗ значений брифа (adversary: другой бриф ⇒ другие строки),
 * пустой бриф молчит.
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

describe("briefNarration (V3.10)", () => {
  it("emits ≥3 distinct human-readable lines for a full brief", () => {
    const lines = briefNarration(LANDING);
    expect(lines.length).toBeGreaterThanOrEqual(3);
    // Все строки различны.
    expect(new Set(lines).size).toBe(lines.length);
    // Человекочитаемые (непустые, не голый токен).
    for (const l of lines) expect(l.trim().length).toBeGreaterThan(8);
  });

  it("each line is DERIVED from a brief field (surfaced, not re-invented)", () => {
    const lines = briefNarration(LANDING);
    const blob = lines.join("\n");
    // Палитра: акцент ведёт (приоритет роли) → его HEX дословно в наррации.
    expect(blob).toContain("#ff5a36");
    // Шрифт: имя дисплей-шрифта дословно.
    expect(blob).toContain("Playfair Display");
    // Секции: имена каркаса дословно.
    expect(blob).toContain("Герой");
    expect(blob).toContain("Контакты");
    // Movement: motion-сигнатура попала в строку.
    expect(blob).toContain(".omnia-shader");
  });

  it("ADVERSARY: a different brief yields different, brief-specific lines", () => {
    const other: StreamBrief = {
      palette: { "PRIMARY": "#14532d", "BACKGROUND": "#f5f5f4" },
      fonts: { display: "Fraunces", text: "Source Sans 3" },
      motion: ".fx-waves органик",
      sections: [
        { id: "menu", name: "Меню" },
        { id: "booking", name: "Бронь" },
        { id: "map", name: "Как добраться" },
      ],
    };
    const a = briefNarration(LANDING).join("\n");
    const b = briefNarration(other).join("\n");
    expect(a).not.toEqual(b);
    // Строки B несут значения B, а НЕ A (иначе они захардкожены).
    expect(b).toContain("#14532d");
    expect(b).toContain("Fraunces");
    expect(b).toContain("Меню");
    expect(b).not.toContain("#ff5a36");
    expect(b).not.toContain("Playfair Display");
    expect(b).not.toContain("Услуги");
  });

  it("falls back to text font when display is missing", () => {
    const lines = briefNarration({
      ...LANDING,
      fonts: { text: "Inter" },
    });
    expect(lines.join("\n")).toContain("Inter");
  });

  it("skips non-hex palette values and caps section list with ellipsis", () => {
    const lines = briefNarration({
      palette: { "ФОН": "not-a-color", "АКЦЕНТ": "#abc123" },
      fonts: { display: "Geist" },
      motion: "",
      sections: [
        { id: "a", name: "A" },
        { id: "b", name: "B" },
        { id: "c", name: "C" },
        { id: "d", name: "D" },
        { id: "e", name: "E" },
      ],
    });
    const blob = lines.join("\n");
    expect(blob).toContain("#abc123");
    expect(blob).not.toContain("not-a-color");
    expect(blob).toContain("…"); // 5 секций → показаны 4 + многоточие
    // motion пустой → нет motion-строки, но ≥3 (палитра+шрифт+секции).
    expect(lines.length).toBeGreaterThanOrEqual(3);
  });

  it("returns [] for an empty or null brief (fail-soft, narration stays silent)", () => {
    expect(briefNarration(null)).toEqual([]);
    expect(briefNarration(undefined)).toEqual([]);
    expect(
      briefNarration({ palette: {}, fonts: {}, motion: "", sections: [] }),
    ).toEqual([]);
  });
});
