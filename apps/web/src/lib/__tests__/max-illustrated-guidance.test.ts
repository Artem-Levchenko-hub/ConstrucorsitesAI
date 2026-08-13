import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const source = (path: string) =>
  readFileSync(join(process.cwd(), "src", path), "utf8");

describe("MAX illustrated guidance surfaces", () => {
  it("replaces the old launch hints with the illustrated dialog", () => {
    const launch = source("components/max/MaxLaunchPanel.tsx");

    expect(launch).toContain("MaxHowToDialog");
    expect(launch).not.toContain("Сейчас вы:");
    expect(launch).not.toContain("Omnia подготовит:");
    expect(launch).not.toContain("Вы делаете в MAX Partner:");
  });

  it("replaces the onboarding hint grid with a visible how-to button", () => {
    const onboarding = source("components/max/MaxOnboarding.tsx");

    expect(onboarding).toContain('data-testid="max-onboarding-how-to"');
    expect(onboarding).toContain("MaxHowToDialog");
    expect(onboarding).not.toContain("max-onboarding-native-guide");
  });

  it("renders a large dialog with a visual and numbered steps", () => {
    const dialog = source("components/max/MaxHowToDialog.tsx");

    expect(dialog).toContain('data-testid="max-how-to-dialog"');
    expect(dialog).toContain("GuideVisual");
    expect(dialog).toContain("Номера на изображении показывают, куда нажать");
    expect(dialog).toContain("guide.steps.map");
  });
});
