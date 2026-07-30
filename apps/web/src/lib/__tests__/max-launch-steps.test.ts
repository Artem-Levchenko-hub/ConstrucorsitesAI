import { describe, expect, it } from "vitest";

import {
  copyMaxLaunchUrl,
  getMaxLaunchWizard,
} from "@/lib/max-launch-steps";

describe("MAX launch wizard", () => {
  it("keeps completed, current, and upcoming readiness checks distinct", () => {
    const wizard = getMaxLaunchWizard([
      { id: "business", label: "Профиль", done: true, blocking: true, action: "Заполнить" },
      { id: "legal", label: "Данные", done: false, blocking: true, action: "Подтвердить" },
      { id: "bot", label: "Бот", done: false, blocking: true, action: "Подключить" },
    ]);

    expect(wizard.completedCount).toBe(1);
    expect(wizard.currentStep).toMatchObject({ id: "legal", position: 2, status: "current" });
    expect(wizard.steps.map(({ id, status }) => [id, status])).toEqual([
      ["business", "completed"],
      ["legal", "current"],
      ["bot", "upcoming"],
    ]);
    expect(wizard.currentStep?.instruction).toContain("оператора");
  });

  it("has no current step after every readiness check is complete", () => {
    const wizard = getMaxLaunchWizard([
      { id: "business", label: "Профиль", done: true, blocking: true, action: "Заполнить" },
    ]);

    expect(wizard.currentStep).toBeUndefined();
    expect(wizard.steps[0].status).toBe("completed");
  });

  it("copies the published URL and reports clipboard failures", async () => {
    const copied: string[] = [];
    await expect(
      copyMaxLaunchUrl("https://app.example", async (value) => {
        copied.push(value);
      }),
    ).resolves.toBe(true);
    expect(copied).toEqual(["https://app.example"]);

    await expect(
      copyMaxLaunchUrl("https://app.example", async () => {
        throw new Error("clipboard denied");
      }),
    ).resolves.toBe(false);
  });
});
