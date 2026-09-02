import { describe, expect, it } from "vitest";

import {
  CAPACITY_WAITING_COPY,
  agentTranscriptTitle,
} from "@/lib/agent-transcript";

describe("agentTranscriptTitle", () => {
  it("never labels a failed or rolled-back run as ready", () => {
    expect(agentTranscriptTitle(false, "failed")).toBe("Сборка не завершена");
    expect(agentTranscriptTitle(false, undefined, true)).toBe("Сборка не завершена");
  });

  it("keeps running and completed labels distinct", () => {
    expect(agentTranscriptTitle(true, "running")).toBe("Собираю приложение");
    expect(agentTranscriptTitle(false, "completed")).toBe("Изменения готовы");
  });

  it("shows capacity waiting as recoverable active work", () => {
    expect(agentTranscriptTitle(true, "queued_for_capacity")).toBe(
      "Ожидаю ресурсы сервера",
    );
    expect(CAPACITY_WAITING_COPY.detail).toBe(
      "Проект сохранён и запустится автоматически, как только освободится мощность.",
    );
  });
});
