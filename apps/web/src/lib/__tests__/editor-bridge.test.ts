import { afterEach, describe, expect, it, vi } from "vitest";

import {
  createEditorModeSync,
  editorModeMessages,
  previewTargetOrigin,
  stopEditorPickingAfterPick,
  type EditorMode,
} from "@/lib/editor-bridge";

afterEach(() => {
  vi.useRealTimers();
});

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

  it("adds one session and sequence to every compatibility command", () => {
    const messages = editorModeMessages("off", {
      editorSession: "workspace-1",
      seq: 7,
    });
    expect(messages).toHaveLength(3);
    expect(messages.every((message) => message.editorSession === "workspace-1")).toBe(true);
    expect(messages.every((message) => message.seq === 7)).toBe(true);
  });
});

describe("monotonic editor mode sync", () => {
  it.each(["inspect", "style"] satisfies EditorMode[])(
    "never re-enables stale %s retries after switching off",
    (startingMode) => {
      vi.useFakeTimers();
      const messages: Array<{ type: string; mode?: EditorMode; seq?: number }> = [];
      const sync = createEditorModeSync({
        editorSession: "workspace-fast-toggle",
        postMessage: (message) => messages.push(message),
      });

      const first = sync.transition(startingMode);
      vi.advanceTimersByTime(119);
      const off = sync.transition("off");
      vi.advanceTimersByTime(2_000);

      const atomic = messages.filter(
        (message) => message.type === "omnia:editor:set-mode",
      );
      expect(first.seq).toBe(1);
      expect(off.seq).toBe(2);
      expect(atomic.map((message) => message.mode)).toEqual([
        startingMode,
        "off",
        "off",
        "off",
        "off",
      ]);
      expect(messages.slice(3).every((message) => message.seq === off.seq)).toBe(true);
      expect(messages).toHaveLength(15);
      expect(vi.getTimerCount()).toBe(0);
    },
  );

  it("accepts only the current acknowledgement and cancels retries", () => {
    vi.useFakeTimers();
    const messages: Array<{ type: string; mode?: EditorMode }> = [];
    const sync = createEditorModeSync({
      editorSession: "workspace-ack",
      postMessage: (message) => messages.push(message),
    });

    const inspect = sync.transition("inspect");
    const off = sync.transition("off");
    expect(sync.acknowledge(inspect)).toBe(false);
    expect(sync.acknowledge(off)).toBe(true);
    expect(sync.acknowledge(off)).toBe(false);

    vi.advanceTimersByTime(2_000);
    expect(sync.isAcknowledged("off")).toBe(true);
    expect(messages).toHaveLength(6);
    expect(vi.getTimerCount()).toBe(0);
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
    "turns %s off before updating the iframe",
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
});
