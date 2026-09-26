import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { HeroStage } from "@/components/marketing/landing/HeroStage";
import { startWithPrompt, takeLandingPrompt } from "@/lib/landing-prompt";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

/**
 * Поле на первом экране — единственное место, где человек формулирует задачу ДО
 * регистрации. Раньше здесь была витрина: текст печатался сам и никуда не вёл.
 * Проверяется ровно то, что делает его настоящим, — поле ввода, кнопка отправки
 * и решение о том, что происходит по нажатию.
 */
describe("поле запроса на первом экране", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("это настоящее поле ввода, а не надпись", () => {
    const html = renderToStaticMarkup(<HeroStage />);

    expect(html).toContain("<textarea");
    expect(html).toContain('id="yl-hero-prompt"');
    // Подпись связана с полем, иначе экранному диктору нечего объявить.
    expect(html).toContain('for="yl-hero-prompt"');
  });

  it("рядом есть кнопка отправки, а не украшение", () => {
    const html = renderToStaticMarkup(<HeroStage />);

    expect(html).toContain('type="submit"');
    expect(html).toContain('class="ys-stage-prompt"');
    // Метка воронки сохранена: переходы с первого экрана должны быть отличимы.
    expect(html).toContain('data-placement="hero_prompt"');
  });

  it("написанное сохраняется и человек идёт на регистрацию", () => {
    const visited: string[] = [];

    startWithPrompt("Нужен магазин растений с самовывозом", (href) => visited.push(href));

    expect(visited).toEqual(["/max/register"]);
    expect(takeLandingPrompt()).toBe("Нужен магазин растений с самовывозом");
  });

  it("пустое поле — не ошибка: человек просто идёт регистрироваться", () => {
    const visited: string[] = [];

    startWithPrompt("   ", (href) => visited.push(href));

    expect(visited).toEqual(["/max/register"]);
    expect(takeLandingPrompt()).toBeNull();
  });

  it("отказ хранилища не мешает переходу", () => {
    const spy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("QuotaExceededError");
    });
    const visited: string[] = [];

    startWithPrompt("Приложение кофейни", (href) => visited.push(href));

    expect(visited).toEqual(["/max/register"]);
    spy.mockRestore();
  });
});
