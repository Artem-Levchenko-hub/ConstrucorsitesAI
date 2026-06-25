/**
 * POST /api/channels/<id>/members — add a member to a channel by email.
 * Only an existing member may invite (membership-gated). FIXED template file.
 */

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

import { NextResponse } from "next/server";

import { addMemberByEmail } from "@/lib/channels";
import {
  assertChannelAccess,
  ChannelForbiddenError,
} from "@/lib/realtime/policy";
import { getCurrentUser } from "@/lib/session";

type Ctx = { params: Promise<{ id: string }> };

export async function POST(req: Request, ctx: Ctx) {
  const { id } = await ctx.params;
  const user = await getCurrentUser();
  if (!user) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }
  // Only a current member may add others.
  try {
    await assertChannelAccess(`conversation:${id}`, user.id, "write");
  } catch (e) {
    if (e instanceof ChannelForbiddenError) {
      return NextResponse.json({ error: "forbidden" }, { status: 403 });
    }
    throw e;
  }
  const body = (await req.json().catch(() => ({}))) as { email?: string };
  const email = String(body.email ?? "").trim().toLowerCase();
  if (!email) {
    return NextResponse.json({ error: "email required" }, { status: 400 });
  }
  const addedUserId = await addMemberByEmail(id, email);
  if (!addedUserId) {
    return NextResponse.json(
      { error: "Пользователь с таким email не найден" },
      { status: 404 },
    );
  }
  return NextResponse.json({ data: { ok: true, userId: addedUserId } });
}
