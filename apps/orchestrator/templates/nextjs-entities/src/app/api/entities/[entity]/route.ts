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
    const body = await req.json().catch(() => ({}));
    const data = await createRecord({ def, user, body });
    return NextResponse.json({ data }, { status: 201 });
  });
}
