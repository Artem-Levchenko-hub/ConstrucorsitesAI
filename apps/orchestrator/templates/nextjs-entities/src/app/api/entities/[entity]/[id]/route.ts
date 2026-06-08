/**
 * Single-record endpoint — GET / PUT / DELETE one row of an entity by id.
 * The engine matches on (id, entity, owner) in one check, so you can never
 * read or mutate another entity's row or another user's row (no IDOR).
 * Fixed template file — AI never edits it.
 */

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

import { NextRequest, NextResponse } from "next/server";

import { getCurrentUser } from "@/lib/session";
import { loadEntity } from "@/lib/entities/registry";
import {
  EngineError,
  deleteRecord,
  getRecord,
  updateRecord,
} from "@/lib/entities/engine";
import { run } from "@/lib/entities/http";

type Ctx = { params: Promise<{ entity: string; id: string }> };

export async function GET(_req: NextRequest, ctx: Ctx) {
  const { entity, id } = await ctx.params;
  return run(async () => {
    const def = await loadEntity(entity);
    if (!def) throw new EngineError(404, `unknown entity '${entity}'`);
    const user = await getCurrentUser();
    const data = await getRecord({ def, user, id });
    return NextResponse.json({ data });
  });
}

export async function PUT(req: NextRequest, ctx: Ctx) {
  const { entity, id } = await ctx.params;
  return run(async () => {
    const def = await loadEntity(entity);
    if (!def) throw new EngineError(404, `unknown entity '${entity}'`);
    const user = await getCurrentUser();
    const body = await req.json().catch(() => ({}));
    const data = await updateRecord({ def, user, id, body });
    return NextResponse.json({ data });
  });
}

// PATCH is an alias for PUT — both do a partial merge.
export const PATCH = PUT;

export async function DELETE(_req: NextRequest, ctx: Ctx) {
  const { entity, id } = await ctx.params;
  return run(async () => {
    const def = await loadEntity(entity);
    if (!def) throw new EngineError(404, `unknown entity '${entity}'`);
    const user = await getCurrentUser();
    const data = await deleteRecord({ def, user, id });
    return NextResponse.json({ data });
  });
}
