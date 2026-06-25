/**
 * GET  /api/channels      — channels the signed-in user belongs to.
 * POST /api/channels      — create a conversation (creator becomes admin member).
 * FIXED template file.
 */

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

import { NextResponse } from "next/server";

import { createChannel, listUserChannels } from "@/lib/channels";
import { getCurrentUser } from "@/lib/session";

export async function GET() {
  const user = await getCurrentUser();
  if (!user) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }
  const data = await listUserChannels(user.id);
  return NextResponse.json({ data });
}

export async function POST(req: Request) {
  const user = await getCurrentUser();
  if (!user) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }
  const body = (await req.json().catch(() => ({}))) as { title?: string };
  const title = String(body.title ?? "").slice(0, 120);
  const channel = await createChannel(user.id, title);
  return NextResponse.json({ data: channel });
}
