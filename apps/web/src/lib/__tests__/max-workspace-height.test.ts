import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const appLayout = readFileSync(
  resolve(process.cwd(), "src/app/(app)/layout.tsx"),
  "utf8",
);
const workspaceShell = readFileSync(
  resolve(process.cwd(), "src/components/max/MaxWorkspaceShell.tsx"),
  "utf8",
);
const chatPanel = readFileSync(
  resolve(process.cwd(), "src/components/workspace/ChatPanel.tsx"),
  "utf8",
);

describe("MAX workspace viewport contract", () => {
  it("pins the authenticated application shell to one viewport", () => {
    expect(appLayout).toContain(
      'className="flex h-dvh max-h-dvh min-h-0 flex-col overflow-hidden"',
    );
  });

  it("prevents a long project list from expanding the workspace grid row", () => {
    expect(workspaceShell).toContain("grid-rows-[minmax(0,1fr)]");
    expect(workspaceShell).toContain(
      "fixed inset-y-0 left-0 z-50 flex h-dvh max-h-dvh min-h-0",
    );
    expect(workspaceShell).toContain(
      'data-testid="max-navigation-scroll"',
    );
    expect(workspaceShell).toContain(
      "min-h-0 flex-1 overflow-y-auto overscroll-contain",
    );
    expect(workspaceShell).toContain(
      'className="shrink-0 border-t border-[#d8d4cb] p-3"',
    );
  });

  it("keeps the transcript scrollable and the composer inside the visible row", () => {
    expect(workspaceShell).toContain(
      'className="max-studio-chat min-h-0 flex-1 overflow-hidden"',
    );
    expect(chatPanel).toContain("flex h-full min-h-0 flex-col");
    expect(chatPanel).toContain(
      'className="flex-1 min-h-0 overflow-y-auto overflow-x-hidden overscroll-contain scrollbar-elegant"',
    );
    expect(chatPanel).toContain('<div className="shrink-0">');
  });
});
