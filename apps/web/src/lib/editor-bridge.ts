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
