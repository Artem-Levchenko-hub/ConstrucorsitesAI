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
 * WebSocket base. By default the socket goes to the host that served the page
 * and follows its scheme (https → wss, http → ws), so the same web image works
 * under any domain. `NEXT_PUBLIC_WS_URL` is only for a split-origin setup
 * (local dev: web :3000 → api :8000).
 *
 * `||`, not `??`: a build that defines the variable as "" must fall back to the
 * page host instead of producing a host-less URL. Outside a browser there is no
 * page to follow, so the base is empty — callers only open sockets in effects.
 */
export function wsBaseUrl(): string {
  const configured = (process.env.NEXT_PUBLIC_WS_URL || "").replace(/\/+$/, "");
  if (configured) return configured;
  if (typeof window === "undefined") return "";
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${window.location.host}`;
}

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
  const replayQuery = opts?.runId
    ? `?run_id=${encodeURIComponent(opts.runId)}&after_seq=${Math.max(0, opts.afterSeq ?? 0)}`
    : "";
  const ws = new WebSocket(
    `${wsBaseUrl()}/api/ws/projects/${projectId}${replayQuery}`,
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
