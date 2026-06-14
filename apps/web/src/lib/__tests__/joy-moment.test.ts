import { describe, expect, it } from "vitest";

import type { StreamBrief } from "@/lib/api/types";
import {
  JOY_DURATION_MS,
  JOY_FALLBACK_ACCENT,
  buildJoyTrigger,
  joyAccent,
  joyFiresForTurn,
  joyShouldShow,
} from "@/lib/joy-moment";

/**
 * V3.8 JOY-MOMENT — falsifiable-ядро reward-ноты на build-complete.
 *
 * NORTH STAR столп «ощущается как игра/кайф» получил первую creator-facing
 * reward-точку. Эти тесты пиннят КАЖДЫЙ гейт задачи как машинный ассерт:
 * фаярит на build, ровно 1× на билд, ушло < 2.5s, подавлено под reduced-motion,
 * 0 на ошибке/правке, цвет = бренд-акцент ЭТОГО билда (а не фикс-токен).
 */

function brief(palette: Record<string, string>): StreamBrief {
  return {
    palette,
    fonts: {},
    motion: "",
    sections: [],
  } as unknown as StreamBrief;
}

describe("joyAccent — бренд-цвет празднования = акцент ЭТОГО билда", () => {
  it("берёт акцент-HEX из брифа (роль ведёт), не фолбэк", () => {
    expect(joyAccent(brief({ accent: "#112233", background: "#ffffff" }))).toBe(
      "#112233",
    );
  });

  it("РАЗНЫЕ брифы → РАЗНЫЕ акценты (adversary: цвет не захардкожен)", () => {
    const a = joyAccent(brief({ accent: "#aa0000" }));
    const b = joyAccent(brief({ accent: "#00bb00" }));
    expect(a).toBe("#aa0000");
    expect(b).toBe("#00bb00");
    expect(a).not.toBe(b);
  });

  it("пустой/нулевой бриф → продуктовый фолбэк-акцент", () => {
    expect(joyAccent(null)).toBe(JOY_FALLBACK_ACCENT);
    expect(joyAccent(undefined)).toBe(JOY_FALLBACK_ACCENT);
    expect(joyAccent(brief({}))).toBe(JOY_FALLBACK_ACCENT);
    // невалидный цвет в палитре не подменяет фолбэк
    expect(joyAccent(brief({ accent: "не-цвет" }))).toBe(JOY_FALLBACK_ACCENT);
  });
});

describe("joyFiresForTurn / buildJoyTrigger — анти-спам: только полный билд", () => {
  it("build-ход фаярит, edit-ход — нет", () => {
    expect(joyFiresForTurn("build")).toBe(true);
    expect(joyFiresForTurn("edit")).toBe(false);
    // неизвестный ход трактуется как build (дефолт usePromptStream)
    expect(joyFiresForTurn(undefined)).toBe(true);
    expect(joyFiresForTurn(null)).toBe(true);
  });

  it("buildJoyTrigger на build → триггер с id и бренд-акцентом", () => {
    const t = buildJoyTrigger("msg-1", "build", brief({ accent: "#abcdef" }));
    expect(t).toEqual({ id: "msg-1", accent: "#abcdef" });
  });

  it("buildJoyTrigger на edit → null (нет ноты на правке)", () => {
    expect(buildJoyTrigger("msg-2", "edit", brief({ accent: "#abcdef" }))).toBeNull();
  });
});

describe("joyShouldShow — ровно 1× на билд + подавление reduced-motion", () => {
  const trigger = { id: "msg-9", accent: "#123456" };

  it("новый build-триггер показывается", () => {
    expect(joyShouldShow(trigger, null, false)).toBe(true);
  });

  it("тот же id второй раз НЕ показывается (ровно 1× на билд)", () => {
    expect(joyShouldShow(trigger, "msg-9", false)).toBe(false);
  });

  it("новый билд (другой id) после прошлого — показывается снова", () => {
    expect(joyShouldShow({ id: "msg-10", accent: "#123456" }, "msg-9", false)).toBe(
      true,
    );
  });

  it("reduced-motion подавляет ноту целиком", () => {
    expect(joyShouldShow(trigger, null, true)).toBe(false);
  });

  it("нет триггера (0 на ошибке/без билда) → не показывается", () => {
    expect(joyShouldShow(null, null, false)).toBe(false);
    expect(joyShouldShow(undefined, null, false)).toBe(false);
  });
});

describe("JOY_DURATION_MS — гейт «ушла < 2.5s»", () => {
  it("длительность ноты строго меньше 2500мс", () => {
    expect(JOY_DURATION_MS).toBeLessThan(2500);
    expect(JOY_DURATION_MS).toBeGreaterThan(0);
  });
});
