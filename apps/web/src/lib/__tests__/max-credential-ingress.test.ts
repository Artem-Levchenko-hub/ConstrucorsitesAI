import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const source = (relative: string) =>
  readFileSync(resolve(process.cwd(), relative), "utf8");
const chatPanel = source("src/components/workspace/ChatPanel.tsx");
const maxStudio = source("src/components/max/MaxStudio.tsx");
const promptInput = source("src/components/workspace/PromptInput.tsx");
const promptStream = source("src/hooks/usePromptStream.ts");

describe("MAX credential ingress routing", () => {
  it("routes composer, discovery, survey and starter through one guard", () => {
    expect(chatPanel).toContain("const submitWithCredentialIntake = useCallback(");
    expect(chatPanel).toContain("submitWithCredentialIntake(text, selections)");
    expect(chatPanel).toContain("void submitWithCredentialIntake(choice, [])");
    expect(chatPanel).toContain("await submitWithCredentialIntake(text, [], {");
    expect(chatPanel).toContain("void submitWithCredentialIntake(p.trim(), [], {");
  });

  it("never sends raw credential text through the prompt endpoint", () => {
    const secureBranch = chatPanel.slice(
      chatPanel.indexOf("credentialSubmitPending.current = true"),
      chatPanel.indexOf("const handleSubmit ="),
    );
    expect(secureBranch).toContain(
      "return submit(safePrompt, modelId, safeSelections, opts)",
    );
    expect(secureBranch).not.toContain("submit(text, modelId");
  });

  it("clears the composer only after the prompt POST is accepted", () => {
    expect(promptInput).toContain("const submitted = await onSubmit");
    expect(promptInput).toContain("if (submitted) {");
    expect(promptStream).toContain("return false;");
    expect(promptStream).toContain("return true;");
  });

  it("retains the starter handoff until secure intake succeeds", () => {
    expect(chatPanel).not.toContain(
      "if (p) window.sessionStorage.removeItem(key);",
    );
    expect(chatPanel).toContain("if (submitted) {");
    expect(chatPanel).toContain(
      "window.sessionStorage.removeItem(starterStorageKey)",
    );
  });

  it("encrypts a credential before persistence and rejects secret URLs", () => {
    expect(maxStudio).toContain("const rawPrompt = buildMaxProjectPrompt");
    expect(maxStudio).toContain("await connectAppIntegration(project.id");
    expect(maxStudio).toContain("summary: safeIdea");
    expect(maxStudio).toContain("prompt = safePrompt");
    expect(chatPanel).toContain("if (urlPrompt && containsChatSecret(urlPrompt))");
    expect(chatPanel).toContain("Ключ из ссылки не принят");
  });
});
