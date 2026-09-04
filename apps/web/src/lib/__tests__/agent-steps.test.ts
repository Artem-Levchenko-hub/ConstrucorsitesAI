import { describe, expect, it } from "vitest";

import type { AgentStep } from "@/lib/api/types";
import {
  mergeAgentStepsBySequence,
  restorePersistedAgentSteps,
} from "@/lib/agent-steps";

const persisted: AgentStep[] = [
  {
    step: 1,
    kind: "step",
    action: "Проверяю проект",
    path: "",
    tool: "runtime_check",
  },
];

describe("agent step history", () => {
  it("restores persisted steps when the observer created an empty cache first", () => {
    expect(restorePersistedAgentSteps([], persisted)).toEqual(persisted);
  });

  it("does not overwrite an active live transcript", () => {
    const live = [{ ...persisted[0], action: "Пишу страницу" }];
    expect(restorePersistedAgentSteps(live, persisted)).toBe(live);
  });

  it("keeps the cache unchanged when no persisted history exists", () => {
    expect(restorePersistedAgentSteps([], null)).toEqual([]);
    expect(restorePersistedAgentSteps(undefined, undefined)).toBeUndefined();
  });

  it("merges durable history over a shorter local prefix", () => {
    const rows = (start: number, end: number): AgentStep[] =>
      Array.from({ length: end - start + 1 }, (_, offset) => {
        const seq = start + offset;
        return {
          eventId: `event-${seq}`,
          runId: "00000000-0000-0000-0000-000000000001",
          seq,
          step: seq,
          kind: "step",
          action: "build",
          path: "",
        };
      });

    expect(
      mergeAgentStepsBySequence(rows(1, 51), rows(1, 130)).map(
        (step) => step.seq,
      ),
    ).toEqual(Array.from({ length: 130 }, (_, index) => index + 1));
  });
});
