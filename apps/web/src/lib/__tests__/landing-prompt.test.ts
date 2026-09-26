import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  LANDING_PROMPT_MAX_LENGTH,
  saveLandingPrompt,
  takeLandingPrompt,
} from "@/lib/landing-prompt";

/**
 * Между «человек написал задачу на витрине» и «человек попал в кабинет» лежит
 * регистрация, иногда с подтверждением почты из другой вкладки и через час.
 * Всё, что здесь проверяется, — это чтобы текст пережил дорогу ровно один раз
 * и не всплыл потом, когда он уже не к месту.
 */
describe("запрос с витрины", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("сохраняется и забирается целиком", () => {
    saveLandingPrompt("Нужен магазин растений: витрина с ценами и корзина с самовывозом.");

    expect(takeLandingPrompt()).toBe(
      "Нужен магазин растений: витрина с ценами и корзина с самовывозом.",
    );
  });

  it("забирается ровно один раз — он предназначен для одного проекта", () => {
    saveLandingPrompt("Приложение кофейни");

    expect(takeLandingPrompt()).toBe("Приложение кофейни");
    expect(takeLandingPrompt()).toBeNull();
  });

  it("не всплывает через сутки", () => {
    const saved = Date.parse("2026-09-25T10:00:00Z");
    saveLandingPrompt("Старая задача", saved);

    expect(takeLandingPrompt(saved + 23 * 60 * 60 * 1000)).toBe("Старая задача");

    saveLandingPrompt("Старая задача", saved);
    expect(takeLandingPrompt(saved + 25 * 60 * 60 * 1000)).toBeNull();
  });

  it("пустой текст не сохраняется", () => {
    expect(saveLandingPrompt("   ")).toBe(false);
    expect(takeLandingPrompt()).toBeNull();
  });

  it("пробелы по краям срезаются", () => {
    saveLandingPrompt("   Приложение клуба   ");

    expect(takeLandingPrompt()).toBe("Приложение клуба");
  });

  it("слишком длинный текст обрезается до предела поля в мастере", () => {
    saveLandingPrompt("я".repeat(LANDING_PROMPT_MAX_LENGTH + 500));

    expect(takeLandingPrompt()).toHaveLength(LANDING_PROMPT_MAX_LENGTH);
  });

  it("испорченное значение не роняет кабинет", () => {
    window.localStorage.setItem("yleum:landing-prompt", "{это не json");

    expect(takeLandingPrompt()).toBeNull();
  });

  it("значение из будущего игнорируется — часы могли переставить", () => {
    const now = Date.parse("2026-09-25T10:00:00Z");
    saveLandingPrompt("Задача", now + 60 * 60 * 1000);

    expect(takeLandingPrompt(now)).toBeNull();
  });

  it("возвращённый обратно текст снова доступен — забрали, но показать не успели", () => {
    // Так ведёт себя кабинет, если открытие мастера отменили до отрисовки:
    // забранный текст кладётся обратно, иначе он исчезает навсегда. Ровно это
    // и случалось при повторном монтировании в режиме разработки.
    saveLandingPrompt("Нужна доставка пиццы");
    const taken = takeLandingPrompt();
    expect(taken).toBe("Нужна доставка пиццы");

    saveLandingPrompt(taken as string);

    expect(takeLandingPrompt()).toBe("Нужна доставка пиццы");
  });

  it("предел совпадает с пределом поля в мастере — текст не режется по дороге", async () => {
    const { MAX_BRIEF_LENGTH } = await import("@/lib/max-brief");

    expect(LANDING_PROMPT_MAX_LENGTH).toBe(MAX_BRIEF_LENGTH);
  });

  it("запрет на хранилище не ломает переход", () => {
    const spy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("QuotaExceededError");
    });

    // Главное здесь — что не бросает: человек всё равно должен попасть на регистрацию.
    expect(saveLandingPrompt("Задача")).toBe(false);

    spy.mockRestore();
  });
});
