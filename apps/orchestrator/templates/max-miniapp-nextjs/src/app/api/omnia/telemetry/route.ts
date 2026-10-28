import { NextResponse } from "next/server";

import { getMaxUser } from "@/lib/max/session";
import { maxActivityKind, type MaxActivityType } from "@/lib/omnia/activity";
import { analyticsEventId, forwardMaxAnalytics } from "@/lib/omnia/analytics";

/** Authenticated telemetry also works with a product-owned database schema. */
export async function POST(request: Request) {
  const user = await getMaxUser();
  if (!user) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  // Owner previews never become customer activity, even with a valid cookie.
  if (user.id === "preview" || !process.env.OMNIA_PUBLIC_APP_ORIGIN) {
    return new NextResponse(null, { status: 204 });
  }
  if (!/^[1-9][0-9]{0,19}$/.test(user.id)) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  let input: { eventType: MaxActivityType; eventId: string };
  try {
    const text = await request.text();
    if (new TextEncoder().encode(text).length > 1024) {
      return NextResponse.json({ error: "Payload too large" }, { status: 413 });
    }
    const body = JSON.parse(text);
    if (!body || typeof body !== "object" || Array.isArray(body) ||
        Object.keys(body).some(key => key !== "eventType" && key !== "eventId") ||
        !maxActivityKind(body.eventType) || typeof body.eventId !== "string" ||
        !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(body.eventId)) {
      throw new Error("Invalid activity");
    }
    input = body;
  } catch {
    return NextResponse.json({ error: "Invalid activity" }, { status: 400 });
  }
  // Same actor/receipt namespace as local events; cross-path replay cannot count twice.
  const eventId = analyticsEventId(`event:${user.id}:${input.eventId.toLowerCase()}`);
  // Client reports are not proof of a committed order/booking. Only the durable
  // action route may increment the confirmed-actions counter.
  const delivered = await forwardMaxAnalytics(user.id, eventId, "event", input.eventType);
  if (!delivered) {
    return NextResponse.json({ error: "Activity delivery unavailable" }, { status: 503 });
  }
  return new NextResponse(null, { status: 204 });
}
