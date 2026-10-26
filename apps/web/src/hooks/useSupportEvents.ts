"use client";
import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { getSupport, supportContextOptions } from "@/lib/api/support";
import { wsBaseUrl } from "@/lib/prompt-stream-transport";

export type SupportEvent = { type: "support.changed"; ticket_id: string; version: number; reason: "created" | "message" | "status"; author?: "customer" | "support" };
type Subscription = { ticket_id: string; guest_token: string } | Record<string, never>;
type Callbacks = { ready: () => void; changed: (event: SupportEvent) => void; revalidate?: () => Promise<boolean> };
/** One connection owns deduplication across reconnects. REST remains authoritative. */
export function connectSupportEvents(subscription: Subscription, callbacks: Callbacks): () => void {
  let stopped = false, socket: WebSocket | undefined, retry: ReturnType<typeof setTimeout> | undefined;
  let delay = 1_000;
  const versions = new Map<string, number>();
  function schedule(action: () => void) {
    if (stopped) return;
    retry = setTimeout(action, delay);
    delay = Math.min(delay * 2, 30_000);
  }
  async function revalidate() {
    if (stopped || !callbacks.revalidate) return;
    let allowed = false;
    try { allowed = await callbacks.revalidate(); } catch { /* A failed check grants no access. */ }
    if (stopped) return;
    // A policy close also covers temporary auth-store failures. Only retry the
    // socket after fresh REST authorization; denied checks use bounded backoff.
    schedule(allowed ? connect : () => { void revalidate(); });
  }
  function connect() {
    if (stopped) return;
    try { socket = new WebSocket(`${wsBaseUrl()}/api/ws/support`); } catch { schedule(connect); return; }
    socket.onopen = () => socket?.send(JSON.stringify({ type: "subscribe", ...subscription }));
    socket.onmessage = ({ data }) => {
      let event: SupportEvent | { type: "support.ready" };
      try { event = JSON.parse(String(data)); } catch { return; }
      if (event?.type === "support.ready") { delay = 1_000; callbacks.ready(); return; }
      if (event?.type !== "support.changed" || typeof event.ticket_id !== "string" || !Number.isSafeInteger(event.version)) return;
      if ((versions.get(event.ticket_id) ?? -1) >= event.version) return;
      if (versions.size > 1_000) versions.clear();
      versions.set(event.ticket_id, event.version); callbacks.changed(event);
    };
    socket.onclose = event => {
      if (stopped) return;
      if ([1008, 4401, 4403].includes(event.code)) { void revalidate(); return; }
      schedule(connect);
    };
    socket.onerror = () => socket?.close();
  }
  connect();
  const ping = setInterval(() => { if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: "ping" })); }, 25_000);
  return () => { stopped = true; clearInterval(ping); clearTimeout(retry); if (socket) { socket.onclose = null; socket.onmessage = null; socket.close(); } };
}

export function useSupportEvents(identity: string | null, admin: boolean, guest?: { id: string; token: string }) {
  const client = useQueryClient();
  const guestId = guest?.id, guestToken = guest?.token;
  useEffect(() => {
    if (!identity && !guestId) return;
    const refresh = () => { void client.invalidateQueries({ queryKey: ["support"] }); };
    const close = connectSupportEvents(identity ? {} : { ticket_id: guestId!, guest_token: guestToken! }, {
      ready: refresh,
      changed: event => {
        refresh();
        if (admin && (event.reason === "created" || (event.reason === "message" && event.author === "customer"))) {
          toast.message(event.reason === "created" ? "Новое обращение в поддержку" : "Новый ответ пользователя", {
            id: `support-${event.ticket_id}-${event.version}`,
            action: { label: "Открыть", onClick: () => { window.location.assign(`/admin/max?tab=support&ticket=${encodeURIComponent(event.ticket_id)}`); } },
          });
        }
      },
      revalidate: async () => {
        client.removeQueries({ queryKey: ["support"] });
        const context = await client.fetchQuery({ ...supportContextOptions, staleTime: 0 });
        if (context.user_id !== identity || context.is_admin !== admin) return false;
        if (!identity) {
          // A guest context alone cannot confirm the per-ticket capability.
          await getSupport(guestId!, false, guestToken, AbortSignal.timeout(10_000));
        }
        return true;
      },
    });
    const fallback = setInterval(refresh, 30_000);
    window.addEventListener("online", refresh);
    return () => { close(); clearInterval(fallback); window.removeEventListener("online", refresh); };
  }, [client, identity, admin, guestId, guestToken]);
}
