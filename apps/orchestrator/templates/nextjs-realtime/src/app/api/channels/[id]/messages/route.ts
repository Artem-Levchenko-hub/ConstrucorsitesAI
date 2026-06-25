/**
 * GET /api/channels/<id>/messages — recent persisted history for a channel.
 * Membership-gated: a non-member gets 403 and never sees the thread. FIXED.
 */

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

import { NextResponse } from "next/server";

import { getHistory } from "@/lib/channels";
import {
  assertChannelAccess,
  ChannelForbiddenError,
} from "@/lib/realtime/policy";
import { getCurrentUser } from "@/lib/session";

type Ctx = { params: Promise<{ id: string }> };

export async function GET(_req: Request, ctx: Ctx) {
  const { id } = await ctx.params;
  const user = await getCurrentUser();
  if (!user) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }
  try {
    await assertChannelAccess(`conversation:${id}`, user.id, "read");
  } catch (e) {
    if (e instanceof ChannelForbiddenError) {
      return NextResponse.json({ error: "forbidden" }, { status: 403 });
    }
    throw e;
  }
  const data = await getHistory(id);
  return NextResponse.json({ data });
}
