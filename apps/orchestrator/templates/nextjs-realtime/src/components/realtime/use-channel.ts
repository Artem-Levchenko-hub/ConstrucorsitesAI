"use client";

/**
 * useChannel — the React hook a generated UI uses to render a live channel.
 * FIXED template file. Returns the running message list, the presence roster,
 * the connection status, and a `send` function. Handles subscribe/teardown and
 * de-dupes by event id so a replay-on-reconnect never doubles a message.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { openChannel, type ChannelHandle, type ChannelStatus } from "@/lib/realtime/client";
import type { PresenceState, RealtimeEvent } from "@/lib/realtime/types";

export type UseChannelOpts = {
  /** Server-rendered history to seed the list (from /api/channels/<id>/messages). */
  initial?: RealtimeEvent[];
  /** Fired for EVERY event (including non-message types like `typing`). */
  onEvent?: (event: RealtimeEvent) => void;
};

/** Stable de-dupe key: persisted messages by their row uuid (survives the
 *  history→live overlap and process restarts), everything else by hub id. */
function keyOf(event: RealtimeEvent): string {
  const rowId = (event.data as { id?: string } | null)?.id;
  return event.type === "message" && rowId ? `m:${rowId}` : `h:${event.id}`;
}

export function useChannel(channel: string, opts?: UseChannelOpts) {
  const initial = opts?.initial ?? [];
  const [messages, setMessages] = useState<RealtimeEvent[]>(
    initial.filter((e) => e.type === "message"),
  );
  const [presence, setPresence] = useState<PresenceState[]>([]);
  const [status, setStatus] = useState<ChannelStatus>("connecting");
  const handle = useRef<ChannelHandle | null>(null);
  const seen = useRef<Set<string>>(new Set(initial.map(keyOf)));
  const onEventRef = useRef(opts?.onEvent);
  onEventRef.current = opts?.onEvent;

  useEffect(() => {
    // Start the live cursor at 0: hub ids are in-memory, so DB history ids are
    // not comparable. The ring replay + uuid de-dupe make the union correct.
    const h = openChannel(channel, {
      since: 0,
      onStatus: setStatus,
      onPresence: setPresence,
      onEvent: (event) => {
        onEventRef.current?.(event);
        if (event.type !== "message") return;
        const k = keyOf(event);
        if (seen.current.has(k)) return;
        seen.current.add(k);
        setMessages((prev) => [...prev, event]);
      },
    });
    handle.current = h;
    return () => h.close();
    // Re-open only when the channel changes; `initial` is a mount-time seed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [channel]);

  const send = useCallback(
    (type: string, data: unknown): Promise<void> =>
      handle.current?.send(type, data) ?? Promise.resolve(),
    [],
  );

  return { messages, presence, status, send };
}
