import { describe, expect, it } from "vitest";

import { canActivateMaxWebhook } from "@/lib/max-integration-flow";

describe("MAX integration launch order", () => {
  it("requires a connected bot and a published app URL", () => {
    expect(canActivateMaxWebhook({ connected: true, app_url: null })).toBe(false);
    expect(
      canActivateMaxWebhook({
        connected: true,
        app_url: "https://example.com",
      }),
    ).toBe(true);
  });

  it("does not activate a published URL without a connected bot", () => {
    expect(
      canActivateMaxWebhook({
        connected: false,
        app_url: "https://example.com",
      }),
    ).toBe(false);
  });
});
