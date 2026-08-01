import { describe, expect, it } from "vitest";

import { agentElapsedSeconds } from "@/lib/agent-elapsed";

describe("agentElapsedSeconds", () => {
  it("restores a running timer from the durable start after a reload", () => {
    expect(
      agentElapsedSeconds(
        "2026-08-01T10:00:00.000Z",
        null,
        Date.parse("2026-08-01T10:01:31.900Z"),
      ),
    ).toBe(91);
  });

  it("keeps a completed duration frozen after later reloads", () => {
    expect(
      agentElapsedSeconds(
        "2026-08-01T10:00:00.000Z",
        "2026-08-01T10:01:13.800Z",
        Date.parse("2026-08-03T12:00:00.000Z"),
      ),
    ).toBe(73);
  });

  it("fails closed for missing or invalid timestamps", () => {
    expect(agentElapsedSeconds(null, null)).toBe(0);
    expect(agentElapsedSeconds("not-a-date", null)).toBe(0);
  });
});
