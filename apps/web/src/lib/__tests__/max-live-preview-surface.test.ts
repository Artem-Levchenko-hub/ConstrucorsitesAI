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
const editorStyles = readFileSync(resolve(process.cwd(), "src/components/max/max-editor.css"), "utf8");
const editorLayout = readFileSync(resolve(process.cwd(), "src/components/max/MaxEditorLayout.tsx"), "utf8");
const usageBreakdown = readFileSync(
  resolve(process.cwd(), "src/components/max/MaxUsageBreakdown.tsx"),
  "utf8",
);
const previewFrame = readFileSync(
  resolve(process.cwd(), "src/components/workspace/PreviewFrame.tsx"),
  "utf8",
);
const streamingPreviewFrame = readFileSync(
  resolve(process.cwd(), "src/components/workspace/StreamingPreviewFrame.tsx"),
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

  it("separates the preview with a white workspace, not an extra rounded card", () => {
    expect(editorLayout).toContain('className="max-editor-desktop-preview"');
    expect(editorStyles).toContain(".max-editor-desktop-preview { min-height: 0; overflow: hidden; border-left: 1px solid #e2e7ef; background: #fff; }");
    expect(editorStyles).toContain("[data-max-editor]");
  });

  it("preserves white generated-app canvases inside the dark product chrome", () => {
    expect(livePreview).toContain(
      'className="relative bg-white" style={{ width: SCREEN_WIDTH, height: SCREEN_HEIGHT }}',
    );
    expect(livePreview).toContain(
      'className="absolute inset-0 size-full border-0 bg-white"',
    );
    expect(previewFrame.match(/className="h-full bg-white border-0 mx-auto shadow-xl"/g)).toHaveLength(2);
    expect(streamingPreviewFrame).toContain(
      'className="h-full bg-white border-0 mx-auto shadow-xl"',
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
