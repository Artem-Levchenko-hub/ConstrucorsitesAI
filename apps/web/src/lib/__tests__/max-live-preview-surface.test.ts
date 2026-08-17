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
const usageBreakdown = readFileSync(
  resolve(process.cwd(), "src/components/max/MaxUsageBreakdown.tsx"),
  "utf8",
);

describe("MAX live preview surface", () => {
  it("keeps the phone on a transparent stage without a grey framing card", () => {
    expect(livePreview).toContain(
      'className="flex h-full min-h-0 flex-col bg-transparent py-3 sm:py-4"',
    );
    expect(livePreview).not.toContain('bg-[#f5f3ee]');
    expect(livePreview).not.toContain("0_30px_80px");
    expect(livePreview).toContain("0_12px_28px");
  });

  it("does not paint a separate desktop preview column", () => {
    expect(workspaceShell).toContain(
      'className="hidden min-h-0 bg-transparent 2xl:block"',
    );
    expect(workspaceShell).toContain(
      'className="relative flex h-full w-full max-w-[460px] flex-col bg-[#fcfbf7]',
    );
    expect(workspaceShell).toContain(
      'overflow-hidden bg-[#fcfbf7] text-[#171716]',
    );
    expect(workspaceShell).not.toContain(
      '<section className="flex min-h-0 min-w-0 flex-col border-r',
    );
  });

  it("does not surface a stale start error while the runtime is recovering", () => {
    expect(livePreview).toContain(
      "(!runtimeRunning && start.isError ? start.error : null)",
    );
    expect(livePreview).toContain(
      "const showPreviewError = Boolean(previewError) && !preparing",
    );
    expect(livePreview).toContain("{showPreviewError && (");
  });

  it("shows live gateway-ledger spend by generation stage", () => {
    expect(workspaceShell).toContain("<MaxUsageBreakdown projectId={project.id}");
    expect(usageBreakdown).toContain('queryKey: ["max-usage", projectId]');
    expect(usageBreakdown).toContain("refetchInterval: 5_000");
    expect(usageBreakdown).toContain("cache_read_tokens");
    expect(usageBreakdown).toContain("stage.retries");
  });
});
