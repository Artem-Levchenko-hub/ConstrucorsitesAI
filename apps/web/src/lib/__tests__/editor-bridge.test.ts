import { describe, expect, it } from "vitest";

import {
  editorModeMessages,
  previewTargetOrigin,
  stopEditorPickingAfterPick,
  type EditorMode,
} from "@/lib/editor-bridge";

type LegacyState = {
  enabled: boolean;
  styleMode: boolean;
};

function applyLegacy(mode: EditorMode, initial: LegacyState): LegacyState {
  return editorModeMessages(mode).reduce<LegacyState>((state, message) => {
    switch (message.type) {
      case "omnia:inspect:enable":
        return { ...state, enabled: true };
      case "omnia:inspect:disable":
        return { ...state, enabled: false };
      case "omnia:style:enable":
        return { enabled: true, styleMode: true };
      case "omnia:style:disable":
        return { enabled: false, styleMode: false };
      default:
        // Pre-4c94846 inspectors ignore the atomic command.
        return state;
    }
  }, initial);
}

describe("editor bridge compatibility", () => {
  it("leaves inspect enabled after switching from legacy style mode", () => {
    expect(
      applyLegacy("inspect", { enabled: true, styleMode: true }),
    ).toEqual({ enabled: true, styleMode: false });
    expect(editorModeMessages("inspect").map((message) => message.type)).toEqual([
      "omnia:editor:set-mode",
      "omnia:style:disable",
      "omnia:inspect:enable",
    ]);
  });

  it("leaves style enabled after switching from legacy inspect mode", () => {
    expect(
      applyLegacy("style", { enabled: true, styleMode: false }),
    ).toEqual({ enabled: true, styleMode: true });
    expect(editorModeMessages("style").map((message) => message.type)).toEqual([
      "omnia:editor:set-mode",
      "omnia:inspect:disable",
      "omnia:style:enable",
    ]);
  });

  it("turns both legacy modes off and always starts with the atomic command", () => {
    expect(applyLegacy("off", { enabled: true, styleMode: true })).toEqual({
      enabled: false,
      styleMode: false,
    });
    for (const mode of ["inspect", "style", "off"] satisfies EditorMode[]) {
      expect(editorModeMessages(mode)[0]).toEqual({
        type: "omnia:editor:set-mode",
        mode,
      });
    }
  });
});

describe("previewTargetOrigin", () => {
  it("pins cross-origin messages to the iframe origin", () => {
    expect(
      previewTargetOrigin(
        "https://old-project-dev.preview.example/dashboard?inspect=1#x",
        "https://constructor.example",
      ),
    ).toBe("https://old-project-dev.preview.example");
  });

  it("resolves relative previews and rejects non-web schemes", () => {
    expect(
      previewTargetOrigin("/p/demo?inspect=1", "https://constructor.example"),
    ).toBe("https://constructor.example");
    expect(
      previewTargetOrigin("javascript:alert(1)", "https://constructor.example"),
    ).toBeNull();
  });
});

describe("single-shot editor picking", () => {
  it.each(["inspect", "style"] satisfies EditorMode[])(
    "turns %s off in the store before sending the atomic iframe update",
    (mode) => {
      const order: string[] = [];
      const stopped = stopEditorPickingAfterPick(mode, {
        setInspectMode: (on) => order.push(`inspect:${String(on)}`),
        stopStylePicking: () => order.push("style:false"),
        postMessage: (message) => order.push(`post:${message.type}`),
      });

      expect(stopped).toBe(true);
      expect(order[0]).toBe(mode === "style" ? "style:false" : "inspect:false");
      expect(order.slice(1)).toEqual([
        "post:omnia:editor:set-mode",
        "post:omnia:inspect:disable",
        "post:omnia:style:disable",
      ]);
    },
  );

  it("ignores a stale pick after the mode is already off", () => {
    const order: string[] = [];
    expect(
      stopEditorPickingAfterPick("off", {
        setInspectMode: () => order.push("inspect"),
        stopStylePicking: () => order.push("style"),
        postMessage: () => order.push("post"),
      }),
    ).toBe(false);
    expect(order).toEqual([]);
  });
});
