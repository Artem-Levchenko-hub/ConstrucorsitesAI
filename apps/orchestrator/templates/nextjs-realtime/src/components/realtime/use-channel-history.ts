"use client";

/**
 * useChannelHistory — FIXED realtime primitive. Loads a channel's persisted
 * history the ONE correct way and hands back a `RealtimeEvent[]` ready to seed
 * `useChannel({ initial })`.
 *
 * It exists to kill the #1 client bug on this stack: every API response is
 * enveloped as `{ data: [...] }`, so reading the raw JSON — or fetching
 * `/api/channels/undefined/messages` after a bad id-unwrap — yields the wrong or
 * forbidden channel and a 403 / empty room. This helper always unwraps `.data`
 * and refuses an empty/`"undefined"` id, so a generated room cannot reintroduce
 * the bug. Use it; do not hand-roll the fetch.
 */

import { useEffect, useState } from "react";

import type { RealtimeEvent } from "@/lib/realtime/types";

export function useChannelHistory(channelId: string): {
  /** History events, or `null` while loading (render a skeleton on null). */
  initial: RealtimeEvent[] | null;
  /** True when the channel id was invalid or the fetch failed (room is empty). */
  error: boolean;
} {
  const [initial, setInitial] = useState<RealtimeEvent[] | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let alive = true;
    setInitial(null);
    setError(false);
    // Guard the #1 bug: never fetch history for a missing/undefined id.
    if (!channelId || channelId === "undefined") {
      setInitial([]);
      setError(true);
      return;
    }
    fetch(`/api/channels/${channelId}/messages`, { credentials: "include" })
      .then(async (res) => {
        const body = (await res.json().catch(() => ({}))) as {
          data?: RealtimeEvent[];
        };
        if (!alive) return;
        if (res.ok) {
          setInitial(body.data ?? []); // ← unwrap the envelope, always `.data`
        } else {
          setInitial([]);
          setError(true);
        }
      })
      .catch(() => {
        if (!alive) return;
        setInitial([]);
        setError(true);
      });
    return () => {
      alive = false;
    };
  }, [channelId]);

  return { initial, error };
}
