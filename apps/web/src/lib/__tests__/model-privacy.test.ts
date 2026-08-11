import { describe, expect, it } from "vitest";

import { hidePrivateModelNames } from "../model-privacy";

describe("hidePrivateModelNames", () => {
  it.each([
    "Основа готова — запускаю Sonnet 5",
    "Claude Sonnet 5 начинает сборку",
    "claude-sonnet-5",
    "anthropic/claude-sonnet-5",
  ])("hides the active provider label from persisted UI text: %s", (value) => {
    const visible = hidePrivateModelNames(value);

    expect(visible).not.toMatch(/sonnet|anthropic|claude/i);
    expect(visible).toContain("AI");
  });
});
