export type EditorMode = "inspect" | "style" | "off";

export type EditorBridgeMessage = {
  type: string;
  mode?: EditorMode;
  editorSession?: string;
  seq?: number;
};

export type EditorModeTransition = {
  editorSession: string;
  seq: number;
  mode: EditorMode;
};

export type EditorModeAck = {
  mode?: unknown;
  editorSession?: unknown;
  seq?: unknown;
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
export function editorModeMessages(
  mode: EditorMode,
  transition?: Pick<EditorModeTransition, "editorSession" | "seq">,
): EditorBridgeMessage[] {
  const envelope = transition
    ? { editorSession: transition.editorSession, seq: transition.seq }
    : {};
  const atomic: EditorBridgeMessage = {
    type: "omnia:editor:set-mode",
    mode,
    ...envelope,
  };

  if (mode === "inspect") {
    return [
      atomic,
      { type: "omnia:style:disable", ...envelope },
      { type: "omnia:inspect:enable", ...envelope },
    ];
  }
  if (mode === "style") {
    return [
      atomic,
      { type: "omnia:inspect:disable", ...envelope },
      { type: "omnia:style:enable", ...envelope },
    ];
  }
  return [
    atomic,
    { type: "omnia:inspect:disable", ...envelope },
    { type: "omnia:style:disable", ...envelope },
  ];
}

type EditorModeSyncOptions = {
  editorSession: string;
  postMessage?: (message: EditorBridgeMessage) => void;
  retryDelays?: readonly number[];
  setTimer?: (callback: () => void, delay: number) => ReturnType<typeof setTimeout>;
  clearTimer?: (timer: ReturnType<typeof setTimeout>) => void;
};

/**
 * Own the parent side of the editor mode protocol.
 *
 * Every transition gets a monotonically increasing sequence number. Retried
 * callbacks capture that exact transition and are cancelled before a newer one
 * is created, so an old inspect/style retry can never re-arm interception after
 * the user has returned to ordinary viewing.
 */
export function createEditorModeSync({
  editorSession,
  postMessage = () => undefined,
  retryDelays = [120, 450, 1_100],
  setTimer = setTimeout,
  clearTimer = clearTimeout,
}: EditorModeSyncOptions) {
  let seq = 0;
  let current: EditorModeTransition | null = null;
  let acknowledgedSeq = -1;
  let sendMessage = postMessage;
  const timers = new Set<ReturnType<typeof setTimeout>>();

  function cancelPending() {
    timers.forEach(clearTimer);
    timers.clear();
  }

  function send(transition: EditorModeTransition) {
    if (current?.seq !== transition.seq) return;
    editorModeMessages(transition.mode, transition).forEach(sendMessage);
  }

  function transition(mode: EditorMode): EditorModeTransition {
    cancelPending();
    const next = { editorSession, seq: ++seq, mode };
    current = next;
    acknowledgedSeq = -1;
    send(next);
    retryDelays.forEach((delay) => {
      const timer = setTimer(() => {
        timers.delete(timer);
        send(next);
      }, delay);
      timers.add(timer);
    });
    return next;
  }

  function resend() {
    if (current) send(current);
  }

  function acknowledge(data: EditorModeAck): boolean {
    if (!current || data.mode !== current.mode) return false;

    const sequenced =
      typeof data.editorSession === "string" &&
      typeof data.seq === "number";
    if (
      sequenced &&
      (data.editorSession !== current.editorSession || data.seq !== current.seq)
    ) {
      return false;
    }
    // Version 5 inspectors have an atomic mode ACK without session/seq. Accept
    // it only when its mode equals the current request; stale opposite-mode ACKs
    // are therefore harmless while managed projects move to version 6.
    if (acknowledgedSeq === current.seq) return false;
    acknowledgedSeq = current.seq;
    cancelPending();
    return true;
  }

  function isAcknowledged(mode = current?.mode) {
    return Boolean(current && mode === current.mode && acknowledgedSeq === current.seq);
  }

  function dispose() {
    cancelPending();
    current = null;
  }

  return {
    transition,
    resend,
    acknowledge,
    cancelPending,
    dispose,
    isAcknowledged,
    setPostMessage: (next: (message: EditorBridgeMessage) => void) => {
      sendMessage = next;
    },
    getCurrent: () => current,
  };
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
