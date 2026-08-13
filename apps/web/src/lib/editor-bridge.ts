export type EditorMode = "inspect" | "style" | "off";

export type EditorBridgeMessage = {
  type: string;
  mode?: EditorMode;
};

type StopEditorPickingHandlers = {
  setInspectMode: (on: boolean) => void;
  stopStylePicking: () => void;
  postMessage: (message: EditorBridgeMessage) => void;
};

/** Resolve the exact iframe origin used for postMessage; null means do not send. */
export function previewTargetOrigin(
  iframeSrc: string,
  baseOrigin: string,
): string | null {
  try {
    const url = new URL(iframeSrc, baseOrigin);
    return url.protocol === "http:" || url.protocol === "https:"
      ? url.origin
      : null;
  } catch {
    return null;
  }
}

/**
 * Build one deterministic mode transition for both inspector generations.
 *
 * Existing live projects can still carry the pre-atomic inspector, which only
 * understands inspect/style enable/disable messages. New inspectors understand
 * `editor:set-mode` too. Sending both protocols is safe as long as the legacy
 * pair is ordered so the requested enable is always last:
 *
 * - inspect: disable style, then enable inspect;
 * - style: disable inspect, then enable style;
 * - off: disable both.
 *
 * This keeps old containers editable without bringing back the former
 * Manual -> AI race between independent React effects.
 */
export function editorModeMessages(mode: EditorMode): EditorBridgeMessage[] {
  const atomic: EditorBridgeMessage = {
    type: "omnia:editor:set-mode",
    mode,
  };

  if (mode === "inspect") {
    return [
      atomic,
      { type: "omnia:style:disable" },
      { type: "omnia:inspect:enable" },
    ];
  }
  if (mode === "style") {
    return [
      atomic,
      { type: "omnia:inspect:disable" },
      { type: "omnia:style:enable" },
    ];
  }
  return [
    atomic,
    { type: "omnia:inspect:disable" },
    { type: "omnia:style:disable" },
  ];
}

/**
 * Finish a successful element pick without clearing the picked element.
 *
 * Store state is switched off before the iframe command is sent, so a React
 * rerender cannot briefly re-arm interception after the user starts interacting
 * with the generated app again. `off` means the pick was stale and is ignored.
 */
export function stopEditorPickingAfterPick(
  mode: EditorMode,
  handlers: StopEditorPickingHandlers,
): boolean {
  if (mode === "off") return false;

  if (mode === "style") handlers.stopStylePicking();
  else handlers.setInspectMode(false);

  editorModeMessages("off").forEach(handlers.postMessage);
  return true;
}
