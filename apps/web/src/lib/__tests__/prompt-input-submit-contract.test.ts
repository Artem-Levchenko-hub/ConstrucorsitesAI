import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const promptInput = readFileSync(
  resolve(process.cwd(), "src/components/workspace/PromptInput.tsx"),
  "utf8",
);

describe("Prompt input submit contract", () => {
  it("waits for async submit result before clearing the textarea", () => {
    expect(promptInput).toContain("const submitted = await onSubmit(finalText, wire);");
    expect(promptInput).toContain("if (submitted) {");
    expect(promptInput).not.toContain("onSubmit(finalText, wire);\n      setValue(\"\");");
  });
});
