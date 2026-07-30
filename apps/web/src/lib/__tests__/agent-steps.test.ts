import { describe, expect, it } from "vitest";

import type { AgentStep } from "@/lib/api/types";
import { restorePersistedAgentSteps } from "@/lib/agent-steps";

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
});
