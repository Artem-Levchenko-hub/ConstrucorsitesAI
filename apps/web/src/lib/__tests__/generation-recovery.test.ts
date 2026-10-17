import { describe, expect, it } from "vitest";
import { agentTranscriptTitle } from "@/lib/agent-transcript";
import type { GenerationRun } from "@/lib/api/types";
import { acceptedPreviewState, generationRecoveryLabel, parseGenerationRecovery,
  RECOVERY_UNCONFIRMED_LABEL } from "@/lib/generation-recovery";

const waiting = { version: 1, phase: "waiting_dependency", last_verified_action: null,
  reason: "dependency_unavailable", next_check_at: "2026-10-17T16:00:00Z", attempt_seq: 3 };
const title = agentTranscriptTitle;

describe("durable recovery display", () => {
  it("shows a saved dependency wait instead of active build after reload", () => {
    expect(title(true, "running", false, waiting)).toBe("Работа сохранена, ожидаю восстановления");
  });
  it("asks for a decision only for explicit needs_input", () => {
    expect(title(true, "running", false, { ...waiting, phase: "needs_input" })).toBe("Нужно ваше решение");
  });
  it("keeps confirmed cancellation ahead of an older wait", () => {
    expect(title(false, "cancelled", false, waiting)).toBe("Сборка отменена");
  });
  it("keeps a stop request pending instead of showing a stale waiting label", () => {
    expect(title(true, "cancel_requested", false, waiting)).toBe("Останавливаю работу");
  });
  it("does not label an unaccepted completed build as ready", () => {
    expect(title(false, "completed", false, waiting, false)).toBe("Проверка результата не подтверждена");
  });
});

describe("untrusted public recovery data", () => {
  it.each([
    null, [], {}, { ...waiting, version: true }, { ...waiting, version: 3 },
    { ...waiting, phase: "unexpected diagnostic" }, { ...waiting, phase: "toString" },
    { ...waiting, attempt_seq: -1 }, { ...waiting, attempt_seq: 1.2 },
    { ...waiting, attempt_seq: Number.MAX_SAFE_INTEGER + 1 },
    { ...waiting, next_check_at: "2026-10-17T16:00:00" },
    { ...waiting, next_check_at: "tomorrow" }, { ...waiting, extra: "private data" },
    { ...waiting, reason: {} }, { ...waiting, last_verified_action: [] },
  ])("preserves a neutral label for unsupported or malformed state %#", value => {
    expect(parseGenerationRecovery(value)).toBeNull();
    expect(generationRecoveryLabel(value)).toBe(RECOVERY_UNCONFIRMED_LABEL);
  });
  it("keeps an honest wait while discarding arbitrary reason and action text", () => {
    const parsed = parseGenerationRecovery({ ...waiting, reason: "private diagnostic",
      last_verified_action: "a model says everything is ready" });
    expect(parsed?.reason).toBeNull();
    expect(parsed?.last_verified_action).toBeNull();
    expect(generationRecoveryLabel(parsed)).toBe("Работа сохранена, ожидаю восстановления");
  });
});

const completed: GenerationRun = {
  id: "r", project_id: "p", assistant_message_id: "m", status: "completed",
  response_mode: "build", created_at: "2026-10-17T15:00:00Z",
  started_at: "2026-10-17T15:00:00Z", finished_at: "2026-10-17T15:10:00Z",
};
const accepted: GenerationRun = { ...completed, acceptance: {
  run_id: "r", assistant_message_id: "m", snapshot_id: "s", accepted_at: null,
  current_request_complete: true,
} };
const preview = (currentRun: GenerationRun | null, acceptedSnapshotId: string | null = "s") =>
  acceptedPreviewState({ currentRun, acceptedSnapshotId, acceptedAt: null });

describe("accepted version and current request", () => {
  it("keeps the previous version visible without treating a green build as acceptance", () => {
    expect(preview(completed)).toEqual({ kind: "accepted", snapshotId: "s", acceptedAt: null,
      pendingRunId: "r", isCurrentRequestComplete: false });
  });
  it("does not call a seed or absent snapshot a ready application", () => {
    expect(preview(completed, null).kind).toBe("preparing");
    expect(preview(completed, null).isCurrentRequestComplete).toBe(false);
  });
  it("accepts only exact server acceptance for a terminal current run", () => {
    expect(preview(accepted).isCurrentRequestComplete).toBe(true);
    expect(preview(accepted).pendingRunId).toBeNull();
  });
  it.each([
    { ...accepted, status: "running" as const }, { ...accepted, finished_at: null },
    { ...accepted, recovery_unavailable: true },
    { ...accepted, acceptance: { ...accepted.acceptance!, run_id: "older" } },
    { ...accepted, acceptance: { ...accepted.acceptance!, assistant_message_id: "foreign" } },
    { ...accepted, acceptance: { ...accepted.acceptance!, snapshot_id: "older" } },
    { ...accepted, acceptance: { ...accepted.acceptance!, current_request_complete: false } },
  ])("does not complete the current request from stale or incomplete acceptance %#", run => {
    expect(preview(run).isCurrentRequestComplete).toBe(false);
  });
  it("does not invent a new application for a published turn without a snapshot", () => {
    const result = preview({ ...accepted, acceptance: { ...accepted.acceptance!, snapshot_id: null } }, null);
    expect(result.kind).toBe("preparing");
    expect(result.isCurrentRequestComplete).toBe(true);
  });
});
