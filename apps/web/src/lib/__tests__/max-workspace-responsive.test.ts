import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const source = (relative: string) =>
  readFileSync(resolve(process.cwd(), relative), "utf8");
const appLayout = source("src/app/(app)/layout.tsx");
const workspace = source("src/components/max/MaxWorkspaceShell.tsx");
const chatMessage = source("src/components/workspace/ChatMessage.tsx");

describe("MAX workspace responsive contract", () => {
  it("pins the editor to one viewport with internal scrolling", () => {
    expect(appLayout).toContain("max-h-dvh min-h-0");
    expect(workspace).toContain("grid-rows-[minmax(0,1fr)]");
    expect(workspace).toContain("max-studio-chat min-h-0 flex-1 overflow-hidden");
  });

  it("keeps the phone preview in a drawer below the 2xl breakpoint", () => {
    expect(workspace).toContain("2xl:grid-cols-");
    expect(workspace).toContain("2xl:hidden");
    expect(workspace).toContain("hidden min-h-0 bg-transparent 2xl:block");
  });

  it("collapses oversized user prompts instead of filling the workspace", () => {
    expect(chatMessage).toContain("max-h-[240px]");
    expect(chatMessage).toContain("Показать полностью");
    expect(chatMessage).toContain("Свернуть");
  });
});
