import { describe, expect, it } from "vitest";

import { getMaxHowToGuide } from "@/lib/max-how-to";

describe("MAX illustrated how-to guides", () => {
  it("keeps external MAX work out of the demo stage", () => {
    const guide = getMaxHowToGuide("demo");

    expect(guide.visual).toBe("builder");
    expect(guide.maxNote).toContain("ничего не создавайте");
    expect(guide.steps).toHaveLength(4);
  });

  it("gives an exact MAX Partner path when the bot is required", () => {
    const guide = getMaxHowToGuide("max");

    expect(guide.visual).toBe("max-bot");
    expect(guide.maxNote).toContain("Чат-боты");
    expect(guide.steps.some((step) => step.text.includes("токен"))).toBe(true);
  });

  it("explains how to attach the production URL", () => {
    const guide = getMaxHowToGuide("verify");

    expect(guide.visual).toBe("partner");
    expect(guide.steps.some((step) => step.text.includes("Мини-приложение"))).toBe(true);
    expect(guide.readyWhen).toContain("внутри MAX");
  });

  it("switches to a real-user check after all stages", () => {
    const guide = getMaxHowToGuide(undefined);

    expect(guide.visual).toBe("dashboard");
    expect(guide.title).toContain("как настоящий пользователь");
    expect(guide.steps.some((step) => `${step.title} ${step.text}`.includes("второго аккаунта"))).toBe(true);
  });
});
