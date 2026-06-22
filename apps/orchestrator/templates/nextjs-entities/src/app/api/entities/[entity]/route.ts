/**
 * Collection endpoint for one entity — GET (list/filter/sort/paginate) and
 * POST (create). The entity name comes from the path; its schema + access
 * policy are loaded fresh from entities/<Name>.json. All auth, owner-scoping
 * and validation happen in the engine. Fixed template file — AI never edits it.
 */

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

import { NextRequest, NextResponse } from "next/server";

import { getCurrentUser } from "@/lib/session";
import { loadEntity } from "@/lib/entities/registry";
import { EngineError, createRecord, listRecords } from "@/lib/entities/engine";
import { run } from "@/lib/entities/http";

type Ctx = { params: Promise<{ entity: string }> };

// Best-effort anti-spam for ANONYMOUS `submit` intake (public order/booking/lead).
// Caps anonymous creates per IP per window — in-memory, per-container (resets on
// restart). Authenticated submits and every non-submit entity are unaffected.
const SUBMIT_RL = new Map<string, { n: number; resetAt: number }>();
const SUBMIT_RL_MAX = 30;
const SUBMIT_RL_WINDOW_MS = 10 * 60_000;
function throttleAnonSubmit(req: NextRequest) {
  const ip = req.headers.get("x-forwarded-for")?.split(",")[0]?.trim() || "anon";
  const now = Date.now();
  const e = SUBMIT_RL.get(ip);
  if (!e || e.resetAt < now) {
    SUBMIT_RL.set(ip, { n: 1, resetAt: now + SUBMIT_RL_WINDOW_MS });
    return;
  }
  e.n += 1;
  if (e.n > SUBMIT_RL_MAX)
    throw new EngineError(429, "слишком много заявок, попробуйте позже");
}

export async function GET(req: NextRequest, ctx: Ctx) {
  const { entity } = await ctx.params;
  return run(async () => {
    const def = await loadEntity(entity);
    if (!def) throw new EngineError(404, `unknown entity '${entity}'`);
    const user = await getCurrentUser();
    const data = await listRecords({
      def,
      user,
      params: req.nextUrl.searchParams,
    });
    return NextResponse.json({ data });
  });
}

export async function POST(req: NextRequest, ctx: Ctx) {
  const { entity } = await ctx.params;
  return run(async () => {
    const def = await loadEntity(entity);
    if (!def) throw new EngineError(404, `unknown entity '${entity}'`);
    const user = await getCurrentUser();
    if (def.access === "submit" && !user) throttleAnonSubmit(req);
    const body = await req.json().catch(() => ({}));
    const data = await createRecord({ def, user, body });
    return NextResponse.json({ data }, { status: 201 });
  });
}
