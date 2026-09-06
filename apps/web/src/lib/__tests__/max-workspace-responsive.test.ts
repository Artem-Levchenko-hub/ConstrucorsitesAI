import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const source = (relative: string) =>
  readFileSync(resolve(process.cwd(), relative), "utf8");
const appLayout = source("src/app/(app)/layout.tsx");
const workspace = source("src/components/max/MaxEditorLayout.tsx");
const styles = source("src/components/max/max-editor.css");
const chatMessage = source("src/components/workspace/ChatMessage.tsx");

describe("MAX workspace responsive contract", () => {
  it("pins the editor to one viewport with internal scrolling", () => {
    expect(appLayout).toContain("max-h-dvh min-h-0");
    expect(styles).toContain("grid-template-rows: minmax(0, 1fr)");
    expect(workspace).toContain("max-studio-chat min-h-0 flex-1 overflow-hidden");
  });

  it("uses two work areas on a laptop and a preview drawer on narrow screens", () => {
    expect(styles).toContain("grid-template-columns: minmax(0, 1fr) minmax(440px, 40%)");
    expect(styles).toContain("@media (max-width: 1023px)");
    expect(styles).toContain(".max-editor-desktop-preview { display: none; }");
    expect(workspace).toContain('data-testid="max-mobile-preview"');
    expect(workspace).toContain("<Dialog.Content");
  });

  it("collapses oversized user prompts instead of filling the workspace", () => {
    expect(chatMessage).toContain("max-h-[240px]");
    expect(chatMessage).toContain("Показать полностью");
    expect(chatMessage).toContain("Свернуть");
  });
});
