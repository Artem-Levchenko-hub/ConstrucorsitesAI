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
const globalStyles = readFileSync(
  resolve(process.cwd(), "src/app/globals.css"),
  "utf8",
);

describe("MAX workspace viewport contract", () => {
  it("pins the app shell and editor grid to one viewport", () => {
    expect(appLayout).toContain(
      'className="flex h-dvh max-h-dvh min-h-0 flex-col overflow-hidden"',
    );
    expect(workspaceShell).toContain("grid-rows-[minmax(0,1fr)]");
    expect(workspaceShell).toContain("--max-preview-column");
  });

  it("keeps long project lists inside their own adaptive scroll region", () => {
    expect(workspaceShell).toContain('data-testid="max-navigation-scroll"');
    expect(workspaceShell).toContain('data-testid="max-projects-scroll"');
    expect(workspaceShell).toContain(
      "max-projects-scroll mt-2 min-h-20 flex-1 space-y-1 overflow-y-auto overscroll-contain",
    );
    expect(globalStyles).toContain(".max-projects-scroll::-webkit-scrollbar-button");
  });
});
