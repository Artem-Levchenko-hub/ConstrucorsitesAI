import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const source = (relative: string) =>
  readFileSync(resolve(process.cwd(), relative), "utf8");
const chatPanel = source("src/components/workspace/ChatPanel.tsx");
const promptInput = source("src/components/workspace/PromptInput.tsx");
const promptStream = source("src/hooks/usePromptStream.ts");

describe("MAX credential ingress routing", () => {
  it("routes every MAX composer path through one credential guard", () => {
    expect(chatPanel).toContain("const submitWithCredentialIntake = useCallback(");
    expect(chatPanel).toContain("submitWithCredentialIntake(text, selections)");
    expect(chatPanel).toContain("submitWithCredentialIntake(prompt, [])");
    expect(chatPanel).toContain("submitWithCredentialIntake(choice, [])");
    expect(chatPanel).toContain('submitWithCredentialIntake("Постройте сейчас", [])');
    expect(chatPanel).toContain("submitWithCredentialIntake(p.trim(), [], {");
  });

  it("connects first and sends only the redacted prompt to generation", () => {
    const secureBranch = chatPanel.slice(
      chatPanel.indexOf("credentialSubmitPending.current = true"),
      chatPanel.indexOf("const handleSubmit ="),
    );
    expect(secureBranch).toContain("await connectAppIntegration(projectId");
    expect(secureBranch).toContain("await submit(safePrompt, modelId");
    expect(secureBranch).not.toContain("submit(text, modelId");
  });

  it("keeps the key in the composer when verification fails", () => {
    expect(promptInput).toContain("const submitted = await onSubmit");
    expect(promptInput).toContain("if (submitted) {");
    expect(promptStream).toContain("Promise<boolean>");
    expect(promptStream).toContain("return false;");
    expect(promptStream).toContain("return true;");
  });
});
