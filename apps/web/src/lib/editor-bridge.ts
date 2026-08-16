export type EditorMode = "inspect" | "style" | "off";

export type EditorBridgeMessage = {
  type: string;
  mode?: EditorMode;
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
