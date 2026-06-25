/**
 * Browser realtime client — the only thing UI code imports to talk to a channel.
 * FIXED template file. Runs in the browser only (uses EventSource + fetch).
 *
 * `openChannel` subscribes over SSE with automatic reconnect + replay-on-resume
 * (the server replays missed events via the `?since=` cursor). `send` POSTs an
 * event and, if the network is down, queues it and flushes on reconnect so a
 * message typed while briefly offline is not lost.
 */

import type {
  PresencePayload,
  PresenceState,
  RealtimeEvent,
} from "@/lib/realtime/types";

export type ChannelStatus = "connecting" | "open" | "closed";

export type OpenOpts = {
  onEvent?: (event: RealtimeEvent) => void;
  onPresence?: (members: PresenceState[]) => void;
  onStatus?: (status: ChannelStatus) => void;
  /** Resume cursor: only deliver events with id greater than this. */
  since?: number;
};

export type ChannelHandle = {
  send: (type: string, data: unknown) => Promise<void>;
  close: () => void;
};

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export function openChannel(channel: string, opts: OpenOpts): ChannelHandle {
  const enc = encodeURIComponent(channel);
  let es: EventSource | null = null;
  let closed = false;
  let lastId = opts.since ?? 0;
  let backoff = 1000;
  const queue: { type: string; data: unknown }[] = [];

  const flush = async (): Promise<void> => {
    while (queue.length && !closed) {
      const item = queue[0];
      try {
        const res = await fetch(`/api/realtime/${enc}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(item),
          credentials: "include",
        });
        if (res.status === 429) {
          await sleep(1000); // rate limited — back off and retry the same item
          continue;
        }
        // 2xx → delivered; 4xx (bad/forbidden) → unrecoverable, drop it so the
        // queue can't wedge. Either way the head item is done.
        queue.shift();
      } catch {
        return; // offline — keep the queue, retry on the next reconnect
      }
    }
  };

  const handle = (ev: MessageEvent) => {
    let event: RealtimeEvent;
    try {
      event = JSON.parse(ev.data) as RealtimeEvent;
    } catch {
      return;
    }
    if (event.id > lastId) lastId = event.id;
    if (event.type === "presence") {
      opts.onPresence?.((event.data as PresencePayload).members ?? []);
      return;
    }
    opts.onEvent?.(event);
  };

  const connect = () => {
    if (closed) return;
    opts.onStatus?.("connecting");
    const qs = lastId ? `?since=${lastId}` : "";
    es = new EventSource(`/api/realtime/${enc}/stream${qs}`, {
      withCredentials: true,
    });
    es.onopen = () => {
      backoff = 1000;
      opts.onStatus?.("open");
      void flush();
    };
    es.onmessage = handle;
    es.onerror = () => {
      es?.close();
      if (closed) return;
      opts.onStatus?.("closed");
      setTimeout(connect, backoff);
      backoff = Math.min(backoff * 2, 15_000);
    };
  };

  connect();

  return {
    send: async (type, data) => {
      queue.push({ type, data });
      await flush();
    },
    close: () => {
      closed = true;
      es?.close();
      opts.onStatus?.("closed");
    },
  };
}
