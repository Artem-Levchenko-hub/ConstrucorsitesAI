import { describe, expect, it } from "vitest";

import { getMaxJourney, getMaxJourneyItemHref } from "@/lib/max-journey";

const item = (id: string, done: boolean) => ({
  id,
  label: id,
  done,
  blocking: !done,
  action: done ? null : `Сделайте ${id}`,
});

describe("getMaxJourney", () => {
  it("groups server readiness into one six-stage user journey", () => {
    const journey = getMaxJourney("project-1", [
      item("business", true),
      item("legal", true),
      item("build", true),
      item("max_business", true),
      item("plan", true),
      item("bot", false),
      item("publish", false),
      item("webhook", false),
      item("max_url", false),
    ]);

    expect(journey.total).toBe(6);
    expect(journey.completedCount).toBe(3);
    expect(journey.progress).toBe(50);
    expect(journey.currentStage).toMatchObject({
      id: "max",
      href: "/max/project-1/settings?tab=bot",
      status: "current",
    });
  });

  it("uses the dashboard as the final destination when every stage is complete", () => {
    const journey = getMaxJourney("project-1", [
      item("business", true),
      item("legal", true),
      item("build", true),
      item("max_business", true),
      item("plan", true),
      item("bot", true),
      item("publish", true),
      item("webhook", true),
      item("max_url", true),
    ]);

    expect(journey.currentStage).toBeUndefined();
    expect(journey.progress).toBe(100);
  });
});

describe("getMaxJourneyItemHref", () => {
  it("routes each server blocker to the screen where it can be fixed", () => {
    expect(getMaxJourneyItemHref("project-1", "legal")).toBe(
      "/max/project-1/settings?tab=app",
    );
    expect(getMaxJourneyItemHref("project-1", "publish")).toBe(
      "/max/project-1/publish",
    );
    expect(getMaxJourneyItemHref("project-1", "max_business")).toBe(
      "/max/onboarding?next=%2Fmax%2Fproject-1",
    );
  });
});
