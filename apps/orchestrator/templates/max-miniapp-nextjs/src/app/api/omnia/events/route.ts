import { NextResponse } from "next/server";
import { z } from "zod";

import { db, schema } from "@/lib/db";
import { getMaxUser } from "@/lib/max/session";

const Event = z.object({
  eventName: z.string().min(1).max(64).regex(/^[a-z0-9_-]+$/),
  properties: z.record(z.unknown()).default({}),
});

const MAX_EVENT_PAYLOAD_BYTES = 131_072;

export async function POST(request: Request) {
  const user = await getMaxUser();
  if (!user) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  let input: z.infer<typeof Event>;
  try {
    input = Event.parse(await request.json());
  } catch {
    return NextResponse.json({ error: "Invalid event" }, { status: 400 });
  }
  const payloadBytes = new TextEncoder().encode(JSON.stringify(input.properties)).length;
  if (payloadBytes > MAX_EVENT_PAYLOAD_BYTES) {
    return NextResponse.json({ error: "Payload too large" }, { status: 413 });
  }
  await db.insert(schema.maxAnalyticsEvents).values({
    maxUserId: user.id,
    eventName: input.eventName,
    properties: input.properties,
  });
  return new NextResponse(null, { status: 204 });
}
