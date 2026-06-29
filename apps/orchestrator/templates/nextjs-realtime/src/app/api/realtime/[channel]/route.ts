/**
 * Publish endpoint — the client→server half of the realtime transport. FIXED.
 *
 * POST /api/realtime/<channel> publishes one event to a channel. Auth +
 * membership (write) are enforced first. A `message` event is PERSISTED to the
 * `messages` table (so history survives reload) and the stored row is what gets
 * fanned out; any other event type is ephemeral (typing, reactions, cursors).
 * `presence` is engine-managed and rejected here.
 */

import { NextRequest, NextResponse } from "next/server";

import { db } from "@/lib/db";
import { messages } from "@/lib/db/schema";
import { hub } from "@/lib/realtime/hub";
import {
  assertChannelAccess,
  ChannelForbiddenError,
  parseChannel,
} from "@/lib/realtime/policy";
import { allowPublish } from "@/lib/realtime/ratelimit";
import { getCurrentUser } from "@/lib/session";
import type { PublishBody } from "@/lib/realtime/types";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

type Ctx = { params: Promise<{ channel: string }> };
const MAX_MESSAGE_CHARS = 4000;

export async function POST(req: NextRequest, ctx: Ctx) {
  const { channel } = await ctx.params;
  const decoded = decodeURIComponent(channel);

  const user = await getCurrentUser();
  if (!user) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }

  try {
    await assertChannelAccess(decoded, user.id, "write");
  } catch (e) {
    if (e instanceof ChannelForbiddenError) {
      // Loud diagnostic so `read_logs` shows the exact reason a message "vanished"
      // (the agent's screenshot/typecheck can't see a 403 on a user POST).
      console.warn(
        `[realtime] DENY publish 403 — user=${user.id} is NOT a member of ${decoded}`,
      );
      return NextResponse.json({ error: "forbidden" }, { status: 403 });
    }
    throw e;
  }

  const body = (await req.json().catch(() => ({}))) as Partial<PublishBody>;
  const type =
    typeof body.type === "string" && body.type.trim() ? body.type : "message";
  if (type === "presence") {
    return NextResponse.json(
      { error: "presence is engine-managed" },
      { status: 400 },
    );
  }

  // Rate-limit AFTER the kind is known, with SEPARATE budgets: a chatty
  // ephemeral stream (typing/reactions) must never 429 a real message — that
  // shared-bucket starvation was the live "messages feel stuck" self-DoS.
  const kind = type === "message" ? "message" : "ephemeral";
  if (!allowPublish(user.id, decoded, kind)) {
    console.warn(
      `[realtime] 429 rate-limited ${kind} on ${decoded} by ${user.id}`,
    );
    return NextResponse.json({ error: "rate limited" }, { status: 429 });
  }

  let data: unknown = body.data ?? null;

  if (type === "message") {
    // Forgiving wire contract: a generated client may send the text as
    // `data.text` (canonical), `data.body` (the DB COLUMN name — a very common
    // confusion the writer model makes), or a bare string `data`. Accept all three
    // and store into the `body` column. Without this, a client/server field
    // mismatch silently 400s every message ("empty message") and chat looks dead.
    const _d: unknown = body.data;
    const raw =
      typeof _d === "string"
        ? _d
        : _d && typeof (_d as { text?: unknown }).text === "string"
          ? (_d as { text: string }).text
          : _d && typeof (_d as { body?: unknown }).body === "string"
            ? (_d as { body: string }).body
            : "";
    const text = raw.trim().slice(0, MAX_MESSAGE_CHARS);
    if (!text) {
      // Loud diagnostic: name the keys the client actually sent so `read_logs`
      // reveals a client/server field mismatch instantly (the exact failure that
      // made a generated chat look permanently dead).
      const keys =
        _d && typeof _d === "object"
          ? Object.keys(_d as object).join(",")
          : typeof _d;
      console.warn(
        `[realtime] REJECT 400 empty-message on ${decoded} by ${user.id} — ` +
          `client sent data keys=[${keys}]; expected a non-empty string in ` +
          `data.text | data.body | a bare string`,
      );
      return NextResponse.json({ error: "empty message" }, { status: 400 });
    }
    const { id: channelId } = parseChannel(decoded);
    const [row] = await db
      .insert(messages)
      .values({ channelId, userId: user.id, type, body: text })
      .returning();
    data = row;
  }

  const event = hub.publish({ channel: decoded, type, userId: user.id, data });
  return NextResponse.json({ event });
}
