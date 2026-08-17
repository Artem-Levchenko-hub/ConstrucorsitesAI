import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const source = (relative: string) =>
  readFileSync(resolve(process.cwd(), relative), "utf8");
const appLayout = source("src/app/(app)/layout.tsx");
const workspaceShell = source("src/components/max/MaxWorkspaceShell.tsx");
const globalStyles = source("src/app/globals.css");

describe("MAX workspace viewport contract", () => {
  it("pins the app shell and editor grid to one viewport", () => {
    expect(appLayout).toContain(
      'className="flex h-dvh max-h-dvh min-h-0 flex-col overflow-hidden"',
    );
    expect(workspaceShell).toContain("grid-rows-[minmax(0,1fr)]");
    expect(workspaceShell).toContain("--max-preview-column");
    expect(workspaceShell).toContain(
      "max-studio-chat min-h-0 flex-1 overflow-hidden",
    );
  });

  it("keeps long navigation inside bounded scroll regions", () => {
    expect(workspaceShell).toContain('data-testid="max-navigation-scroll"');
    expect(workspaceShell).toContain('data-testid="max-projects-scroll"');
    expect(workspaceShell).toContain(
      "max-projects-scroll mt-2 min-h-20 flex-1 space-y-1 overflow-y-auto overscroll-contain",
    );
    expect(globalStyles).toContain(
      ".max-projects-scroll::-webkit-scrollbar-button",
    );
  });
});
