import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const chatPanel = readFileSync(
  resolve(process.cwd(), "src/components/workspace/ChatPanel.tsx"),
  "utf8",
);

describe("MAX credential ingress routing", () => {
  it("routes the composer, free-form discovery answer and survey through one guard", () => {
    expect(chatPanel).toContain(
      "const submitWithCredentialIntake = useCallback(",
    );
    expect(chatPanel).toContain(
      "void submitWithCredentialIntake(text, selections)",
    );
    expect(chatPanel).toContain(
      "void submitWithCredentialIntake(choice, [])",
    );
    expect(chatPanel).toContain(
      "const submitted = await submitWithCredentialIntake(\n      text,",
    );
  });

  it("never submits the raw text on a credential branch", () => {
    const secureBranch = chatPanel.slice(
      chatPanel.indexOf("const submitWithCredentialIntake = useCallback("),
      chatPanel.indexOf("const handleSubmit ="),
    );
    const credentialBranch = secureBranch.slice(
      secureBranch.indexOf("credentialSubmitPending.current = true"),
    );
    expect(credentialBranch).toContain(
      "submit(safePrompt, modelId, safeSelections, opts)",
    );
    expect(credentialBranch).not.toContain("submit(text, modelId");
  });

  it("includes selected-element fields and starter handoffs in the guard", () => {
    expect(chatPanel).toContain("Object.values(selection)");
    expect(chatPanel).toContain("const credentialSource = [text, selectionText]");
    expect(chatPanel).toContain(
      "void submitWithCredentialIntake(p.trim(), [], {",
    );
    expect(chatPanel).not.toContain("submit(p.trim(), modelId, [], {");
  });
});
