import { describe, expect, it } from "vitest";

import {
  CAPACITY_WAITING_COPY,
  agentTranscriptTitle,
  isPromptPreparationPending,
} from "@/lib/agent-transcript";
import type { AgentStep } from "@/lib/api/types";

describe("agentTranscriptTitle", () => {
  it("never labels a failed or rolled-back run as ready", () => {
    expect(agentTranscriptTitle(false, "failed")).toBe("Подготовка на паузе");
    expect(agentTranscriptTitle(false, undefined, true)).toBe("Подготовка на паузе");
  });

  it("keeps running and completed labels distinct", () => {
    expect(agentTranscriptTitle(true, "running")).toBe("Собираю приложение");
    expect(agentTranscriptTitle(false, "completed")).toBe("Проверка результата не подтверждена");
    expect(agentTranscriptTitle(false, "completed", true, null, true)).toBe("Изменения готовы");
    expect(agentTranscriptTitle(false)).toBe("Ход работы");
  });

  it("shows capacity waiting as recoverable active work", () => {
    expect(agentTranscriptTitle(true, "queued_for_capacity")).toBe(
      "Ожидаю ресурсы сервера",
    );
    expect(CAPACITY_WAITING_COPY.detail).toBe(
      "Проект сохранён и запустится автоматически, как только освободится мощность.",
    );
  });

  it("names the actual preparation state while preserving terminal and recovery precedence", () => {
    const working = { version: 1, phase: "working", last_verified_action: null,
      reason: null, next_check_at: null, attempt_seq: 1 };
    expect(agentTranscriptTitle(true, "running", false, null, false, false, true)).toBe("Подготавливаю промпт");
    expect(agentTranscriptTitle(true, "running", false, working, false, false, true)).toBe("Подготавливаю промпт");
    expect(agentTranscriptTitle(true, "running", false, { ...working, phase: "waiting_dependency" }, false, false, true))
      .toBe("Работа сохранена, ожидаю восстановления");
    expect(agentTranscriptTitle(true, "running", false, {}, false, false, true))
      .toBe("Статус сохранённой работы пока не подтверждён");
    expect(agentTranscriptTitle(true, "queued_for_capacity", false, null, false, false, true))
      .toBe(CAPACITY_WAITING_COPY.title);
    expect(agentTranscriptTitle(true, "cancel_requested", false, null, false, false, true)).toBe("Останавливаю работу");
    expect(agentTranscriptTitle(false, "cancelled", false, null, false, false, true)).toBe("Работа остановлена");
    expect(agentTranscriptTitle(false, "failed", false, null, false, false, true)).toBe("Подготовка на паузе");
    expect(agentTranscriptTitle(false, "completed", false, null, true, false, true)).toBe("Изменения готовы");
  });

  it("ends preparation on either its result or the next actual agent step", () => {
    const pending: AgentStep = { kind: "heartbeat", step: 0, tool: "prompt_preparation",
      action: "Подготавливаю промпт", path: "", ok: null };
    expect(isPromptPreparationPending(pending)).toBe(true);
    for (const step of [undefined, { ...pending, ok: true }, { ...pending, ok: false },
      { ...pending, skipped: true }, { ...pending, tool: "build" }]) {
      expect(isPromptPreparationPending(step)).toBe(false);
    }
  });
});
