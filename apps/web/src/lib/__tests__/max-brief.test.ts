import { describe, expect, it } from "vitest";

import { buildMaxProjectPrompt } from "@/lib/max-brief";

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
});

const longBrief = {
  name: "Склад", idea: "я".repeat(19_990) + "КОНЕЦ ТЗ", appType: "custom" as const,
  audience: "", primaryAction: "", features: [], style: "clean" as const, brandColors: "",
};
it("preserves the complete long brief including its final requirement", () => {
  expect(buildMaxProjectPrompt(longBrief)).toContain(longBrief.idea);
});
it("rejects an oversized brief instead of sending a partial request", () => {
  expect(() => buildMaxProjectPrompt({ ...longBrief, idea: "я".repeat(20_001) })).toThrow();
});
it("checks the assembled request including optional fields", () => {
  expect(() => buildMaxProjectPrompt({ ...longBrief, audience: "я".repeat(30_000) })).toThrow();
});
