import { createHash, timingSafeEqual } from "node:crypto";
import { eq } from "drizzle-orm";
import { NextRequest, NextResponse } from "next/server";

import { db, schema } from "@/lib/db";
import { sendMaxWelcome } from "@/lib/max/bot-api";

const MAX_BODY_BYTES = 256 * 1024;

function secretMatches(provided: string, expected: string): boolean {
  const left = Buffer.from(provided);
  const right = Buffer.from(expected);
  return left.length === right.length && timingSafeEqual(left, right);
}

export async function POST(request: NextRequest) {
  const expected = process.env.MAX_WEBHOOK_SECRET;
  const provided = request.headers.get("x-max-bot-api-secret") || "";
  if (!expected || !secretMatches(provided, expected)) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  const declaredLength = Number(request.headers.get("content-length") || "0");
  if (declaredLength > MAX_BODY_BYTES) {
    return NextResponse.json({ error: "Payload too large" }, { status: 413 });
  }
  const raw = await request.text();
  if (Buffer.byteLength(raw) > MAX_BODY_BYTES) {
    return NextResponse.json({ error: "Payload too large" }, { status: 413 });
  }
  let event: Record<string, unknown>;
  try {
    event = JSON.parse(raw) as Record<string, unknown>;
  } catch {
    return NextResponse.json({ error: "Invalid JSON" }, { status: 400 });
  }
  const eventType = String(event.update_type || event.type || "unknown");
  const eventKey = String(
    event.update_id ||
      event.event_id ||
      createHash("sha256").update(raw).digest("hex"),
  );
  const inserted = await db
    .insert(schema.maxWebhookEvents)
    .values({ eventKey, eventType })
    .onConflictDoNothing()
    .returning({ id: schema.maxWebhookEvents.id });
  if (!inserted.length) return NextResponse.json({ ok: true, duplicate: true });

  try {
    if (eventType === "bot_started") {
      const user = (event.user || {}) as Record<string, unknown>;
      const userId = user.user_id || user.id || event.user_id || event.chat_id;
      if (userId) {
        await sendMaxWelcome(String(userId), request.nextUrl.origin);
      }
    }
  } catch {
    await db
      .delete(schema.maxWebhookEvents)
      .where(eq(schema.maxWebhookEvents.eventKey, eventKey));
    return NextResponse.json({ error: "Temporary failure" }, { status: 503 });
  }
  return NextResponse.json({ ok: true });
}
