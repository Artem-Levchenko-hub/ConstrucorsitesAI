import type { WsEvent } from "@/lib/api/types";

/**
 * Opens a real WebSocket to /api/ws/projects/:id and routes server events
 * through `apply`. Returns a cancel function that closes the socket.
 *
 * The session JWT cookie travels automatically on the WS handshake (browsers
 * include cookies). No extra auth wiring needed here.
 */
type StreamHandle = {
  /** Intentional close — suppresses the auto-reconnect (onclose is nulled). */
  cancel: () => void;
  /** Send a client→server control frame (e.g. `{type:"resync"}`). No-op if closed. */
  send: (msg: unknown) => void;
};

/**
 * Opens a real WebSocket to /api/ws/projects/:id and routes server events
 * through `apply`. Returns a handle to close the socket and to send control
 * frames (resync). The session JWT cookie travels automatically on the WS
 * handshake (browsers include cookies). No extra auth wiring needed here.
 *
 * `onOpen`/`onClose` let the caller drive bounded auto-reconnect: an
 * UNEXPECTED drop (nginx, flaky network) while a generation is still in flight
 * should reconnect; an intentional close (cancel) must not — so `cancel` nulls
 * `onclose` before closing.
 */
export function openRealStream(
  projectId: string,
  apply: (event: WsEvent) => void,
  opts?: {
    onOpen?: () => void;
    onClose?: () => void;
    runId?: string | null;
    afterSeq?: number;
  },
): StreamHandle {
  const wsBase =
    process.env.NEXT_PUBLIC_WS_URL ??
    (typeof window !== "undefined"
      ? `wss://${window.location.host}`
      : "wss://constructor.lead-generator.ru");
  const replayQuery = opts?.runId
    ? `?run_id=${encodeURIComponent(opts.runId)}&after_seq=${Math.max(0, opts.afterSeq ?? 0)}`
    : "";
  const ws = new WebSocket(
    `${wsBase}/api/ws/projects/${projectId}${replayQuery}`,
  );

  // Keep-alive ping — many proxies (and our nginx) close idle WS at 60s.
  const pingInt = setInterval(() => {
    if (ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "ping" }));
    }
  }, 25_000);

  ws.onopen = () => opts?.onOpen?.();
  ws.onmessage = (ev) => {
    try {
      apply(JSON.parse(ev.data) as WsEvent);
    } catch {
      // ignore malformed frames
    }
  };
  ws.onclose = () => {
    clearInterval(pingInt);
    opts?.onClose?.();
  };

  const send = (msg: unknown) => {
    if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg));
  };

  const cancel = () => {
    clearInterval(pingInt);
    // Null onclose so an intentional close never triggers the reconnect path.
    ws.onclose = null;
    if (
      ws.readyState === WebSocket.OPEN ||
      ws.readyState === WebSocket.CONNECTING
    ) {
      ws.close();
    }
  };

  return { cancel, send };
}
