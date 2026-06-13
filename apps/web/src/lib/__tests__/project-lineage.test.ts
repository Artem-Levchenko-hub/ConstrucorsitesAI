import { describe, expect, it } from "vitest";

import { REMIX_BADGE_LABEL, isRemix } from "@/lib/project-lineage";

/**
 * V4.2b-finish leg (B) — the workspace shows a remix lineage badge iff the
 * project was forked. `isRemix` is the single gate for that affordance; each
 * assert below flips in exactly one direction — money-free, 0 LLM, pure logic.
 */

const UUID = "3f9a1c7e-2b4d-4e6f-8a1b-9c0d1e2f3a4b";

describe("isRemix", () => {
  it("is true for a project forked from another", () => {
    expect(isRemix({ forked_from: UUID })).toBe(true);
  });

  it("is false when forked_from is null (organic project)", () => {
    expect(isRemix({ forked_from: null })).toBe(false);
  });

  it("is false when forked_from is absent", () => {
    expect(isRemix({})).toBe(false);
  });

  it("is false for an empty-string forked_from (never a valid lineage)", () => {
    expect(isRemix({ forked_from: "" })).toBe(false);
  });
});

describe("REMIX_BADGE_LABEL", () => {
  it("is the Russian remix label shown in the UI", () => {
    expect(REMIX_BADGE_LABEL).toBe("Ремикс");
  });
});
