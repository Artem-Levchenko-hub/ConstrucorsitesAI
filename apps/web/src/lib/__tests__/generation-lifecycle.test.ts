import { describe, expect, it } from "vitest";

import type { GenerationRun, WsEvent } from "@/lib/api/types";
import {
  isActiveGenerationForMessage,
  isGenerationActive,
  isMaxBuildReady,
  streamEventMessageId,
} from "@/lib/generation-lifecycle";

const generation = (
  status: GenerationRun["status"],
  overrides: Partial<GenerationRun> = {},
): GenerationRun => ({
  id: "run-1",
  project_id: "project-1",
  assistant_message_id: "message-1",
  status,
  response_mode: "build",
  created_at: "2026-07-30T00:00:00Z",
  started_at: null,
  finished_at: null,
  ...overrides,
});

describe("generation lifecycle", () => {
  it.each(["pending", "running", "cancel_requested"] as const)(
    "treats %s as active",
    (status) => {
      expect(isGenerationActive(generation(status))).toBe(true);
    },
  );

  it.each(["completed", "failed", "cancelled"] as const)(
    "treats %s as terminal",
    (status) => {
      expect(isGenerationActive(generation(status))).toBe(false);
    },
  );

  it("matches an active run only to its canonical assistant message", () => {
    expect(isActiveGenerationForMessage(generation("running"), "message-1")).toBe(
      true,
    );
    expect(isActiveGenerationForMessage(generation("running"), "message-2")).toBe(
      false,
    );
  });

  it("counts agent progress as stream activity", () => {
    const event: WsEvent = {
      type: "agent.step",
      data: {
        message_id: "message-1",
        step: 4,
        kind: "step",
        action: "Проверяю сборку",
        path: "",
      },
    };
    expect(streamEventMessageId(event)).toBe("message-1");
  });

  it("ignores project events that do not belong to a message stream", () => {
    const event: WsEvent = {
      type: "runtime.crashed",
      data: { error: "boom" },
    };
    expect(streamEventMessageId(event)).toBeNull();
  });

  it("does not unlock MAX publishing while lifecycle data is loading", () => {
    expect(
      isMaxBuildReady({
        snapshotsLoaded: true,
        generationLoaded: false,
        hasGeneratedSnapshot: true,
        generation: undefined,
      }),
    ).toBe(false);
  });

  it("unlocks MAX publishing only for a terminal successful build", () => {
    expect(
      isMaxBuildReady({
        snapshotsLoaded: true,
        generationLoaded: true,
        hasGeneratedSnapshot: true,
        generation: generation("completed"),
      }),
    ).toBe(true);
    expect(
      isMaxBuildReady({
        snapshotsLoaded: true,
        generationLoaded: true,
        hasGeneratedSnapshot: true,
        generation: generation("failed"),
      }),
    ).toBe(false);
  });
});
