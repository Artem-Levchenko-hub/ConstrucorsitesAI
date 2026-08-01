import { describe, expect, it } from "vitest";

import type { AgentStep } from "@/lib/api/types";
import {
  creativeNarration,
  creativePhaseIndex,
  creativePhaseStates,
} from "@/lib/agent-experience";

function step(tool: string, path = "", ok = true): AgentStep {
  return {
    step: 1,
    kind: "step",
    action: tool,
    tool,
    path,
    ok,
  };
}

describe("agent creative experience", () => {
  it("turns technical steps into a four-phase creative journey", () => {
    const steps = [
      step("read_skill"),
      step("write_file", "src/app/page.tsx"),
      step("build"),
      step("see"),
    ];

    expect(creativePhaseIndex(steps)).toBe(3);
    expect(creativePhaseStates(steps, true).map((phase) => phase.status)).toEqual([
      "complete",
      "complete",
      "complete",
      "active",
    ]);
    expect(creativeNarration(steps, true)).toBe(
      "Смотрю глазами пользователя и полирую",
    );
  });

  it("narrates visual craft instead of exposing only a file operation", () => {
    expect(
      creativeNarration([step("write_file", "src/app/globals.css")], true),
    ).toBe("Настраиваю типографику, ритм и детали");
  });

  it("makes a failed latest phase explicit without claiming completion", () => {
    const phases = creativePhaseStates([step("build", "", false)], false);

    expect(phases[2].status).toBe("issue");
    expect(phases[3].status).toBe("upcoming");
    expect(creativeNarration([step("build", "", false)], false)).toBe(
      "Исправляю найденную проблему",
    );
  });

  it("marks every phase complete only after a successful done", () => {
    expect(creativePhaseStates([step("done")], false).every((phase) => phase.status === "complete")).toBe(true);
  });
});
