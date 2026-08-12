import { describe, expect, it } from "vitest";

import {
  isGenerationActive,
  shouldDeferMaxRuntimeStart,
} from "../max-runtime-start";

describe("MAX runtime start coordination", () => {
  it.each(["pending", "running", "cancel_requested"])(
    "keeps preview startup deferred while generation is %s",
    (status) => {
      expect(isGenerationActive(status)).toBe(true);
      expect(
        shouldDeferMaxRuntimeStart({
          generationQueryPending: false,
          generationStatus: status,
          hasGeneration: true,
          hasStarterHandoff: false,
          starterHandoffExpired: false,
        }),
      ).toBe(true);
    },
  );

  it("allows runtime recovery after a generation becomes terminal", () => {
    expect(
      shouldDeferMaxRuntimeStart({
        generationQueryPending: false,
        generationStatus: "failed",
        hasGeneration: true,
        hasStarterHandoff: true,
        starterHandoffExpired: false,
      }),
    ).toBe(false);
  });

  it("bounds a starter handoff that never creates a generation", () => {
    const state = {
      generationQueryPending: false,
      generationStatus: null,
      hasGeneration: false,
      hasStarterHandoff: true,
    };

    expect(
      shouldDeferMaxRuntimeStart({ ...state, starterHandoffExpired: false }),
    ).toBe(true);
    expect(
      shouldDeferMaxRuntimeStart({ ...state, starterHandoffExpired: true }),
    ).toBe(false);
  });
});
