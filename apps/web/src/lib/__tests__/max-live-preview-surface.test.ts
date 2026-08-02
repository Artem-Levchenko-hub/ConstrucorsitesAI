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
const stylePanel = readFileSync(
  resolve(process.cwd(), "src/components/workspace/StylePanel.tsx"),
  "utf8",
);

describe("MAX live preview surface", () => {
  it("keeps the phone on a transparent stage without a grey framing card", () => {
    expect(livePreview).toContain(
      'className="relative flex h-full min-h-0 flex-col bg-transparent py-3 sm:py-4"',
    );
    expect(livePreview).toContain('data-testid="max-live-device-stage"');
    expect(livePreview).not.toContain("0_30px_80px");
    expect(livePreview).toContain("0_12px_28px");
    expect(livePreview).toContain(
      "absolute inset-0 size-full object-cover object-top transition-opacity",
    );
  });

  it("celebrates a completed MAX build inside the live preview", () => {
    expect(livePreview).toContain("<JoyBurst projectId={project.id}");
    expect(livePreview).toContain("Готово — приложение ожило");
  });

  it("keeps precise editing behind one progressive-disclosure control", () => {
    expect(livePreview).toContain('data-testid="max-edit-menu-trigger"');
    expect(livePreview).toContain('data-testid="max-edit-with-ai"');
    expect(livePreview).toContain('data-testid="max-edit-manually"');
    expect(livePreview).toContain("Изменить с ИИ");
    expect(livePreview).toContain("Настроить вручную");
    expect(livePreview).toContain('title={active ? `Режим: ${label}` : "Править элементы"}');
    expect(livePreview).toContain("relative grid size-11");
    expect(livePreview).toContain(
      'className="grid size-11 place-items-center rounded-full',
    );
    expect(livePreview).toContain("tabular-nums");
    expect(livePreview).not.toContain("Точечная правка");
    expect(livePreview).not.toContain("Текст, фото, структура и логика");
    expect(livePreview).not.toContain("Цвет и видимость — без расхода ИИ");
    expect(livePreview).toContain('<h2 className="text-xs font-semibold">Превью</h2>');
    expect(livePreview).not.toContain("Mobile WebView");
    expect(livePreview).toContain("editorModeMessages(activeEditorMode)");
    expect(livePreview).toContain(
      "loadedPreviewUrl !== displayPreviewUrl",
    );
    expect(livePreview).toContain("previewTargetOrigin(");
    expect(livePreview).toContain("<StylePanel");
    expect(livePreview).toContain("projectId={project.id}");
    expect(livePreview).toContain("sourceEditing={false}");
    expect(livePreview).toContain("fontEditing={false}");
    expect(livePreview).toContain("tokenEditing={false}");
    expect(livePreview).toContain("post={postToAllProjectPreviews}");
    expect(livePreview).toContain('frame.dataset.maxPreviewReady = "true"');
    expect(livePreview).toContain("replayPendingStyles()");
    expect(livePreview).toContain("closedStylePanel");
    expect(livePreview).toContain("selectionIdPrefix");
    expect(livePreview).toContain(
      "frame.contentWindow.postMessage(message, targetOrigin)",
    );
    expect(livePreview).not.toContain('postMessage(message, "*")');
    expect(stylePanel).toContain(
      "sourceEditing && selected.editableText",
    );
    expect(stylePanel).toContain(
      "fontEditing && selected.editableText",
    );
    expect(stylePanel).toContain("tokenEditing && (");
    expect(workspaceShell).toContain(
      "<MaxEditorProjectScope key={project.id} projectId={project.id}>",
    );
    expect(workspaceShell).toContain("scopeToProject(projectId)");
    expect(workspaceShell).toContain("releaseProjectScope(projectId)");
    expect(workspaceShell).toContain("inspectorScope !== projectId");
    expect(workspaceShell).toContain("styleScope !== projectId");
    expect(workspaceShell).toContain(
      "onClose={() => setPreviewOpen(false)}",
    );
    expect(workspaceShell).not.toContain("Превью приложения</p>");
  });

  it("separates the desktop preview without painting a framing card", () => {
    expect(workspaceShell).toContain("--max-preview-column");
    expect(workspaceShell).toContain(
      "transition-[transform,opacity,border-color] duration-300",
    );
    expect(workspaceShell).toContain("motion-reduce:duration-0");
    expect(workspaceShell).toContain(
      'data-testid="max-desktop-preview-column"',
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
