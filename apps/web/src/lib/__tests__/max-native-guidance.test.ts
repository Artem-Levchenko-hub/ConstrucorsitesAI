import { describe, expect, it } from "vitest";

import { getMaxNativeGuidance } from "@/lib/max-native-guidance";

describe("MAX native guidance", () => {
  it("keeps external MAX work out of the demo stage", () => {
    const guidance = getMaxNativeGuidance("demo");

    expect(guidance.maxRequiredNow).toBe(false);
    expect(guidance.maxAction).toContain("не нужны");
    expect(guidance.userAction).toContain("превью");
  });

  it("gives an exact MAX Partner path when the bot is required", () => {
    const guidance = getMaxNativeGuidance("max");

    expect(guidance.maxRequiredNow).toBe(true);
    expect(guidance.maxAction).toContain("MAX Partner → Чат-боты");
    expect(guidance.userAction).toContain("дождитесь одобрения");
  });

  it("explains how to attach the production URL", () => {
    const guidance = getMaxNativeGuidance("verify");

    expect(guidance.maxAction).toContain("Расширенные настройки → Мини-приложение");
    expect(guidance.successSignal).toContain("внутри MAX");
  });

  it("switches to a real-user check after all stages", () => {
    const guidance = getMaxNativeGuidance(undefined);

    expect(guidance.title).toContain("как пользователь");
    expect(guidance.userAction).toContain("второго аккаунта");
  });
});
