import { describe, expect, it } from "vitest";

import { getMaxLaunchErrorDescription } from "@/lib/max-launch-error";

describe("MAX launch error", () => {
  it("does not expose raw Docker build logs in a toast", () => {
    const raw = new Error(
      "prod build failed: dependencies: next react drizzle Step 14/26 RUN next build",
    );

    const message = getMaxLaunchErrorDescription(raw);

    expect(message).toContain("Omnia уже повторила её автоматически");
    expect(message).not.toContain("Step 14/26");
    expect(message).not.toContain("dependencies:");
  });

  it("keeps ordinary launch errors concise", () => {
    const message = getMaxLaunchErrorDescription(
      new Error(`Webhook не активирован ${"x".repeat(500)}`),
    );

    expect(message).toContain("Webhook не активирован");
    expect(message.length).toBeLessThanOrEqual(360);
  });

  it("does not claim an automatic retry after a build timeout", () => {
    const message = getMaxLaunchErrorDescription(
      new Error("prod build timed out after 840s total"),
    );

    expect(message).toContain("превысила лимит времени");
    expect(message).not.toContain("повторила её автоматически");
  });
});
