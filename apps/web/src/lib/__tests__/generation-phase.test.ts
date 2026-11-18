import { describe, expect, it } from "vitest";
import type { AgentStep, GenerationRecoveryView } from "@/lib/api/types";
import { generationEventStep } from "@/lib/generation-event-step";
import { projectGenerationPhase } from "@/lib/generation-phase";

const runId = "run";
const step = (extra: Partial<AgentStep> = {}): AgentStep => ({
  runId, step: 1, kind: "step", action: "Пишу экран", path: "", tool: "write_file", ...extra,
});
const phaseStep = (phase: string, ok: boolean | null = null) => generationEventStep({
  event_id: `event-${phase}`, run_id: runId, seq: 1, type: "generation.phase", data: { phase, ok },
})!;
const project = (steps: AgentStep[], extra: Partial<Parameters<typeof projectGenerationPhase>[0]> = {}) =>
  projectGenerationPhase({ steps, runId, status: "running", streaming: true, ...extra });
const recovery = (phase: GenerationRecoveryView["phase"]): GenerationRecoveryView => ({
  version: 1, phase, last_verified_action: null, reason: null, next_check_at: null, attempt_seq: 0,
});

describe("semantic generation phase from existing observations", () => {
  it.each([
    [[], "workspace"],
    [[phaseStep("prepare")], "workspace"],
    [[step({ kind: "heartbeat", tool: "prompt_preparation", ok: null })], "prompt"],
    [[step()], "creating"],
    [[phaseStep("final_build")], "checking"],
    [[phaseStep("runtime_probe")], "checking"],
    [[phaseStep("snapshot")], "preview"],
    [[phaseStep("promote")], "preview"],
  ] as const)("projects observed steps into %s / %s without inventing progress", (steps, id) => {
    expect(project([...steps]).id).toBe(id);
    expect(project([...steps]).active).toBe(true);
    expect(project([...steps]).milestones).toEqual([]);
  });

  it("continues quietly after optional prompt fallback without marking preparation complete", () => {
    const failed = step({ kind: "heartbeat", tool: "prompt_preparation", ok: false,
      action: "Продолжаю по исходному запросу" });
    const view = project([failed]);
    expect(view.id).toBe("creating");
    expect(view.explanation).toContain("исходному запросу");
    expect(view.milestones).not.toContain("Промпт подготовлен");
    expect(failed.ok).toBe(false);
  });

  it("shows only confirmed own milestones and keeps failed/skipped/foreign checks out", () => {
    const ready = step({ kind: "heartbeat", tool: "prompt_preparation", ok: true });
    const build = step({ tool: "build", ok: true });
    expect(project([phaseStep("prepare", true), ready, build]).milestones).toEqual([
      "Среда подготовлена", "Промпт подготовлен", "Сборка проверена",
    ]);
    expect(project([ready, build, { ...build, ok: false }]).milestones).toEqual(["Промпт подготовлен"]);
    expect(project([{ ...ready, skipped: true }, { ...build, runId: "foreign" }]).milestones).toEqual([]);
    expect(project([ready, { ...ready, ok: false }]).milestones).not.toContain("Промпт подготовлен");
  });

  it("recognizes actual UUID platform_core records as workspace preparation, not a completed environment", () => {
    const core = step({ eventId: "11111111-1111-1111-1111-111111111111", tool: "platform_core",
      action: "Подготавливаю защищённое ядро MAX", ok: true });
    expect(project([core]).id).toBe("workspace");
    expect(project([core]).milestones).not.toContain("Среда подготовлена");
    const persistedBuild = step({ kind: "heartbeat", eventId: "22222222-2222-2222-2222-222222222222",
      tool: undefined, action: "Собираю приложение · завершено", ok: true });
    expect(project([persistedBuild]).id).toBe("checking");
    expect(project([persistedBuild]).milestones).toContain("Сборка проверена");
  });

  it("revokes a stale build milestone after edits or an unconfirmed later build", () => {
    const build = step({ tool: "build", ok: true });
    const edit = step({ tool: "edit_file", ok: true });
    expect(project([build, edit]).milestones).not.toContain("Сборка проверена");
    expect(project([build, step({ tool: "write_file", ok: null })]).milestones).not.toContain("Сборка проверена");
    expect(project([build, { ...build, ok: null }]).milestones).not.toContain("Сборка проверена");
    expect(project([build, edit, build]).milestones).toContain("Сборка проверена");
    expect(project([build, { ...edit, runId: "foreign" }]).milestones).toContain("Сборка проверена");
  });

  it.each(["bash", "shell", "command"])("revokes checked source after any non-skipped %s until a fresh build", tool => {
    const build = step({ tool: "build", ok: true });
    const command = step({ tool, ok: true, action: "Выполняю команду" });
    expect(project([build, command]).milestones).not.toContain("Сборка проверена");
    expect(project([build, command, build]).milestones).toContain("Сборка проверена");
    expect(project([command], { recovery: { ...recovery("repairing"), last_verified_action: "build_verified" } })
      .milestones).not.toContain("Сборка проверена");
    expect(project([build, { ...command, skipped: true }]).milestones).toContain("Сборка проверена");
    expect(project([build, { ...command, runId: "foreign" }]).milestones).toContain("Сборка проверена");
    for (const observation of [
      { ...command, ok: false },
      { ...command, ok: null },
      { ...command, ok: undefined, toolLifecycle: "started" as const },
    ]) {
      expect(project([build, observation]).milestones).not.toContain("Сборка проверена");
      expect(project([build, observation, build]).milestones).toContain("Сборка проверена");
      expect(project([build, { ...observation, skipped: true }]).milestones).toContain("Сборка проверена");
      expect(project([build, { ...observation, runId: "foreign" }]).milestones).toContain("Сборка проверена");
    }
    expect(project([build, { ...command, tool: undefined, action: tool }]).milestones).not.toContain("Сборка проверена");
  });

  it.each([false, null, undefined])("keeps unaccepted done (%s) in checking instead of announcing preview", ok => {
    const attempted = step({ tool: "done", action: "done", ok });
    expect(project([attempted]).id).toBe("checking");
    expect(project([attempted]).title).not.toContain("превью");
    expect(project([{ ...attempted, tool: undefined }]).id).toBe("checking");
    expect(project([step({ tool: "done", action: "done", ok: true })]).id).toBe("preview");
    expect(project([step({ tool: "done", action: "done", ok: true, skipped: true })]).id).not.toBe("preview");
  });

  it("does not use agent done, successful tools or completed-without-receipt as ready", () => {
    expect(project([step({ tool: "done", action: "done", ok: true })]).id).not.toBe("ready");
    expect(project([], { status: "completed", streaming: false }).id).toBe("unconfirmed");
    expect(project([], { currentRequestComplete: true }).id).not.toBe("ready");
    expect(project([], { status: "completed", currentRequestComplete: true }).id).toBe("ready");
    expect(project([], { status: "completed", currentRequestComplete: true, historical: true }).id).toBe("history");
    expect(project([], { historical: true }).active).toBe(false);
  });

  it.each([
    ["queued_for_capacity", "queued"], ["cancel_requested", "stopping"],
    ["cancelled", "stopped"], ["failed", "paused"],
  ] as const)("keeps %s truthful and static", (status, id) => {
    const view = project([step()], { status });
    expect(view.id).toBe(id);
    expect(view.active).toBe(false);
  });

  it.each([
    ["repairing", "recovering", true], ["verifying", "checking", true],
    ["waiting_dependency", "waiting", false], ["reconciling", "unconfirmed", false],
    ["needs_input", "paused", false],
  ] as const)("uses validated recovery %s", (phase, id, active) => {
    const view = project([step()], { recovery: recovery(phase) });
    expect(view.id).toBe(id);
    expect(view.active).toBe(active);
  });

  it("keeps malformed recovery and legacy capacity waiting conservative", () => {
    expect(project([step()], { recovery: { phase: "working", private: "not a valid receipt" } }).id).toBe("unconfirmed");
    expect(project([step({ tool: "capacity_wait" })]).id).toBe("queued");
    expect(project([step({ kind: "stalled", action: "Меняю подход" })]).id).toBe("creating");
  });
});
