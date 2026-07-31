import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const livePreview = readFileSync(
  resolve(process.cwd(), "src/components/max/MaxLivePreview.tsx"),
  "utf8",
);
const workspaceShell = readFileSync(
  resolve(process.cwd(), "src/components/max/MaxWorkspaceShell.tsx"),
  "utf8",
);

describe("MAX live preview surface", () => {
  it("keeps the phone on a transparent stage without a grey framing card", () => {
    expect(livePreview).toContain(
      'className="flex h-full min-h-0 flex-col bg-transparent py-3 sm:py-4"',
    );
    expect(livePreview).not.toContain('bg-[#f5f3ee]');
  });

  it("uses the workspace surface behind desktop and mobile phone previews", () => {
    expect(workspaceShell).toContain(
      'className="hidden min-h-0 bg-[#fcfbf7] xl:block"',
    );
    expect(workspaceShell).toContain(
      'className="relative flex h-full w-full max-w-[460px] flex-col bg-[#fcfbf7]',
    );
    expect(workspaceShell).not.toContain(
      '<section className="flex min-h-0 min-w-0 flex-col border-r',
    );
  });
});
