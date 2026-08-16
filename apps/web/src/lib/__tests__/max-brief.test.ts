import { describe, expect, it } from "vitest";

import {
  buildMaxProductSpec,
  buildMaxProjectPrompt,
  parseMaxStarterHandoff,
  sanitizeMaxProjectBrief,
  serializeMaxStarterHandoff,
  type MaxProjectBrief,
} from "@/lib/max-brief";

describe("buildMaxProjectPrompt", () => {
  it("locks a guided project to the MAX mini-app platform", () => {
    const prompt = buildMaxProjectPrompt({
      name: "Кофе рядом",
      idea: "Карта лояльности с заказом напитка",
      appType: "loyalty",
      audience: "Гости сети кофеен",
      primaryAction: "Получить награду за баллы",
      features: ["Профиль пользователя", "Баллы и награды"],
      style: "brand",
      brandColors: "фиолетовый и молочный",
    });

    expect(prompt).toContain("именно для мессенджера MAX");
    expect(prompt).toContain("Не превращай его в обычный сайт");
    expect(prompt).toContain("Сценарий: Лояльность");
    expect(prompt).toContain("Баллы и награды");
    expect(prompt).toContain("MAX Bridge");
    expect(prompt).toContain("Не добавляй отдельную регистрацию");
    expect(prompt).not.toContain("демонстрационные данные");
  });

  it("adds useful defaults when optional fields are omitted", () => {
    const prompt = buildMaxProjectPrompt({
      name: "Помощник",
      idea: "Сервис быстрых консультаций",
      appType: "custom",
      audience: "",
      primaryAction: "",
      features: [],
      style: "clean",
      brandColors: "",
    });

    expect(prompt).toContain("определи по описанию продукта");
    expect(prompt).toContain("определи главное действие пользователя");
    expect(prompt).toContain("подбери уместную палитру");
  });

  it("builds a deterministic bounded product contract from the questionnaire", () => {
    const brief: MaxProjectBrief = {
      name: "Кофе рядом",
      idea: "Карта лояльности с заказом напитка",
      appType: "loyalty" as const,
      audience: "Гости сети кофеен",
      primaryAction: "Получить награду за баллы",
      features: ["Профиль пользователя", "Баллы и награды", "История действий"],
      style: "brand" as const,
      brandColors: "фиолетовый и молочный",
    };

    const spec = buildMaxProductSpec(brief);

    expect(spec.screens).toEqual([
      "Главная с балансом",
      "Награды",
      "История действий",
      "Профиль",
    ]);
    expect(spec.data).toContain("Баланс и операции лояльности");
    expect(spec.capabilities).toEqual(brief.features);
    expect(spec.history).toBe(true);
    expect(spec.primary_action_kind).toBe("managed_write");
    expect(spec.acceptance).toHaveLength(3);
  });

  it("preserves every selected capability in the strict product contract", () => {
    const features = [
      "Профиль пользователя",
      "Каталог или лента",
      "Поиск и фильтры",
      "Избранное",
      "Баллы и награды",
      "Онлайн-запись",
      "Уведомления бота",
      "История действий",
    ] as const;
    const spec = buildMaxProductSpec({
      name: "Все функции",
      idea: "Проверка полного контракта",
      appType: "custom",
      audience: "Пользователи MAX",
      primaryAction: "Выполнить действие",
      features: [...features],
      style: "clean",
      brandColors: "синий",
    });

    expect(spec.capabilities).toEqual(features);
  });

  it("requires persistence for built-in business actions without inventing a history tab", () => {
    const spec = buildMaxProductSpec({
      name: "Столик",
      idea: "Бронирование столика",
      appType: "booking",
      audience: "Гости ресторана",
      primaryAction: "Забронировать столик",
      features: ["Профиль пользователя"],
      style: "clean",
      brandColors: "зелёный",
    });

    expect(spec.history).toBe(true);
    expect(spec.primary_action_kind).toBe("managed_write");
    expect(spec.screens).not.toContain("История действий");
    expect(spec.acceptance.at(-1)).toContain("восстанавливается после перезагрузки");
  });

  it("derives the closed action kind only from structured questionnaire fields", () => {
    const base: MaxProjectBrief = {
      name: "Витрина",
      idea: "Показывать актуальные товары",
      appType: "custom",
      audience: "Клиенты",
      primaryAction: "Открыть заявку на услугу",
      features: [],
      style: "clean",
      brandColors: "синий",
    };

    expect(buildMaxProductSpec(base).primary_action_kind).toBe("local_navigation");
    expect(
      buildMaxProductSpec({ ...base, features: ["Каталог или лента"] })
        .primary_action_kind,
    ).toBe("local_navigation");
    expect(
      buildMaxProductSpec({ ...base, features: ["История действий"] })
        .primary_action_kind,
    ).toBe("managed_write");
  });

  it("redacts credentials before writing the project, config, prompt, or spec", () => {
    const safe = sanitizeMaxProjectBrief({
      name: "Кофе рядом",
      idea: `Карта лояльности sk-${"a".repeat(24)}`,
      appType: "loyalty",
      audience: "Гости",
      primaryAction: "Получить награду",
      features: [],
      style: "brand",
      brandColors: "фиолетовый",
    });

    expect(safe.credentialsRemoved).toBe(true);
    expect(safe.brief.idea).toContain("[CREDENTIAL REDACTED]");
    expect(buildMaxProductSpec(safe.brief).purpose).not.toContain(`sk-${"a".repeat(24)}`);
  });

  it("preserves the strict product spec through a starter retry", () => {
    const spec = buildMaxProductSpec({
      name: "Столик",
      idea: "Бронирование столика",
      appType: "booking",
      audience: "Гости ресторана",
      primaryAction: "Забронировать столик",
      features: ["История действий"],
      style: "clean",
      brandColors: "зелёный",
    });

    expect(parseMaxStarterHandoff(serializeMaxStarterHandoff("build", spec))).toEqual({
      version: 1,
      prompt: "build",
      productSpec: spec,
    });
  });

  it("rejects prompt-only and malformed starter retries", () => {
    expect(parseMaxStarterHandoff("старый prompt-only handoff")).toBeNull();
    expect(
      parseMaxStarterHandoff(
        JSON.stringify({ version: 1, prompt: "build", productSpec: { purpose: "x" } }),
      ),
    ).toBeNull();
  });
});
