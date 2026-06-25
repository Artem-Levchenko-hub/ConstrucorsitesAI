/**
 * SSE subscription endpoint — the server→client half of the realtime transport.
 * FIXED template file. AI never edits it.
 *
 * GET /api/realtime/<channel>/stream opens a long-lived text/event-stream. The
 * client (EventSource) receives every event published to the channel. Auth +
 * membership are enforced before a single byte streams, so a non-member gets a
 * 403 and never sees the channel's traffic. `Last-Event-ID` (or `?since=`)
 * replays missed events from the hub ring buffer on reconnect.
 */

import { NextRequest } from "next/server";

import { hub, ensureRedis } from "@/lib/realtime/hub";
import {
  assertChannelAccess,
  ChannelForbiddenError,
} from "@/lib/realtime/policy";
import { getCurrentUser } from "@/lib/session";
import type { RealtimeEvent } from "@/lib/realtime/types";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

type Ctx = { params: Promise<{ channel: string }> };

export async function GET(req: NextRequest, ctx: Ctx) {
  const { channel } = await ctx.params;
  const decoded = decodeURIComponent(channel);

  const user = await getCurrentUser();
  if (!user) return new Response("unauthorized", { status: 401 });

  try {
    await assertChannelAccess(decoded, user.id, "read");
  } catch (e) {
    if (e instanceof ChannelForbiddenError) {
      return new Response("forbidden", { status: 403 });
    }
    throw e;
  }

  await ensureRedis();

  const sinceHeader = req.headers.get("last-event-id");
  const sinceQuery = req.nextUrl.searchParams.get("since");
  const sinceId = Number(sinceHeader ?? sinceQuery ?? 0) || 0;

  const encoder = new TextEncoder();
  let unsub: () => void = () => {};
  let heartbeat: ReturnType<typeof setInterval> | undefined;

  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      const write = (chunk: string) => {
        try {
          controller.enqueue(encoder.encode(chunk));
        } catch {
          // Controller closed (client disconnected mid-write) — ignore.
        }
      };
      const send = (event: RealtimeEvent) => {
        // Everything rides the default (unnamed) SSE event with the type inside
        // the JSON, so the client SDK dispatches on `event.type` and never needs
        // an addEventListener per app-invented type. `id:` drives resume.
        write(`id: ${event.id}\ndata: ${JSON.stringify(event)}\n\n`);
      };

      write(`: connected ${decoded}\n\n`); // open the stream immediately

      // Replay anything the client missed while disconnected.
      for (const e of hub.replay(decoded, sinceId)) send(e);

      // Register the live subscription (also records presence under user.id).
      unsub = hub.subscribe(decoded, send, user.id);

      // Hand the new subscriber the current presence snapshot right away — a
      // join event only fires for the FIRST tab, so a second tab needs this.
      send({
        id: 0,
        channel: decoded,
        type: "presence",
        userId: null,
        data: { channel: decoded, members: hub.presence(decoded) },
        ts: Date.now(),
      });

      // Comment heartbeat keeps intermediaries from idling the connection out.
      heartbeat = setInterval(() => write(`: ping\n\n`), 25_000);
    },
    cancel() {
      if (heartbeat) clearInterval(heartbeat);
      unsub();
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
      // Disable proxy buffering (nginx) so events flush in real time.
      "X-Accel-Buffering": "no",
    },
  });
}
