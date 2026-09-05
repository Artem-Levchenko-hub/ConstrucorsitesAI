import { createHash, timingSafeEqual } from "node:crypto";
import { eq } from "drizzle-orm";
import { NextRequest, NextResponse } from "next/server";

import { db, schema } from "@/lib/db";
import { sendMaxHelp, sendMaxWelcome } from "@/lib/max/bot-api";

const MAX_BODY_BYTES = 256 * 1024;

function secretMatches(provided: string, expected: string): boolean {
  const left = Buffer.from(provided);
  const right = Buffer.from(expected);
  return left.length === right.length && timingSafeEqual(left, right);
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

function appOrigin(request: NextRequest): string | null {
  // Portable public cores receive this controller-owned origin. Their upstream
  // Host is an internal service address; forwarded client headers are untrusted.
  const configured = process.env.OMNIA_PUBLIC_APP_ORIGIN;
  if (configured === undefined) return request.nextUrl.origin;
  try {
    const url = new URL(configured);
    if (
      url.protocol !== "https:" || !url.hostname || url.username || url.password ||
      url.pathname !== "/" || url.search || url.hash
    ) return null;
    return url.origin;
  } catch {
    return null;
  }
}

function userIdFrom(event: Record<string, unknown>): string | null {
  const user = record(event.user);
  const message = record(event.message);
  const sender = record(message.sender);
  const callback = record(event.callback);
  const callbackUser = record(callback.user);
  const value =
    user.user_id ||
    user.id ||
    sender.user_id ||
    sender.id ||
    callbackUser.user_id ||
    callbackUser.id ||
    event.user_id ||
    event.chat_id;
  return value === undefined || value === null ? null : String(value);
}

export async function POST(request: NextRequest) {
  const expected = process.env.MAX_WEBHOOK_SECRET;
  const provided = request.headers.get("x-max-bot-api-secret") || "";
  if (!expected || !secretMatches(provided, expected)) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  const publicOrigin = appOrigin(request);
  if (!publicOrigin) {
    return NextResponse.json({ error: "Public app origin is not configured" }, { status: 503 });
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
      const userId = userIdFrom(event);
      if (userId) {
        await sendMaxWelcome(userId, publicOrigin);
      }
    } else if (eventType === "message_created") {
      const message = record(event.message);
      const body = record(message.body);
      const text = String(body.text || message.text || "").trim().toLocaleLowerCase("ru-RU");
      const userId = userIdFrom(event);
      if (
        userId &&
        ["/start", "старт", "помощь", "/help", "открыть", "приложение"].includes(text)
      ) {
        await sendMaxHelp(userId, publicOrigin);
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
