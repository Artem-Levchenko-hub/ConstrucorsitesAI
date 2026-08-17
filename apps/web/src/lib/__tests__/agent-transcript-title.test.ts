import { describe, expect, it } from "vitest";

import { agentTranscriptTitle } from "@/lib/agent-transcript";

describe("agentTranscriptTitle", () => {
  it("never labels a failed or rolled-back run as ready", () => {
    expect(agentTranscriptTitle(false, "failed")).toBe("Сборка не завершена");
    expect(agentTranscriptTitle(false, undefined, true)).toBe("Сборка не завершена");
  });

  it("keeps running and completed labels distinct", () => {
    expect(agentTranscriptTitle(true, "running")).toBe("Собираю приложение");
    expect(agentTranscriptTitle(false, "completed")).toBe("Изменения готовы");
  });
});
