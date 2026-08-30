import { and, desc, eq, lt, or } from "drizzle-orm";
import { NextResponse } from "next/server";
import { z } from "zod";

import { db, schema } from "@/lib/db";
import { getMaxUser } from "@/lib/max/session";

const DEFAULT_ACTION_LIMIT = 250;
const MAX_ACTION_LIMIT = 1000;
const MAX_ACTION_PAYLOAD_BYTES = 262_144;

const Action = z.object({
  actionType: z.string().min(1).max(64).regex(/^[a-z0-9_-]+$/),
  payload: z.record(z.unknown()).default({}),
});

const ActionQuery = z.object({
  limit: z.coerce.number().int().min(1).max(MAX_ACTION_LIMIT).default(DEFAULT_ACTION_LIMIT),
  cursor: z.string().trim().min(1).optional(),
});

const ActionCursor = z.object({
  createdAt: z.string().datetime(),
  id: z.string().uuid(),
});

function encodeCursor(action: { createdAt: Date; id: string }): string {
  return `${action.createdAt.toISOString()}::${action.id}`;
}

function decodeCursor(raw: string | null): { createdAt: Date; id: string } | null {
  if (!raw) return null;
  const [createdAt, id] = raw.split("::", 2);
  const parsed = ActionCursor.safeParse({ createdAt, id });
  if (!parsed.success) return null;
  return { createdAt: new Date(parsed.data.createdAt), id: parsed.data.id };
}

export async function GET(request: Request) {
  const user = await getMaxUser();
  if (!user) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  const query = ActionQuery.safeParse(
    Object.fromEntries(new URL(request.url).searchParams.entries()),
  );
  if (!query.success) {
    return NextResponse.json({ error: "Invalid action query" }, { status: 400 });
  }
  const cursor = decodeCursor(query.data.cursor || null);
  if (query.data.cursor && !cursor) {
    return NextResponse.json({ error: "Invalid action cursor" }, { status: 400 });
  }
  const filters = [eq(schema.maxBusinessActions.maxUserId, user.id)];
  if (cursor) {
    filters.push(
      or(
        lt(schema.maxBusinessActions.createdAt, cursor.createdAt),
        and(
          eq(schema.maxBusinessActions.createdAt, cursor.createdAt),
          lt(schema.maxBusinessActions.id, cursor.id),
        ),
      )!,
    );
  }
  const rows = await db
    .select()
    .from(schema.maxBusinessActions)
    .where(and(...filters))
    .orderBy(desc(schema.maxBusinessActions.createdAt), desc(schema.maxBusinessActions.id))
    .limit(query.data.limit + 1);
  const hasMore = rows.length > query.data.limit;
  const actions = hasMore ? rows.slice(0, query.data.limit) : rows;
  const nextCursor =
    hasMore && actions.length > 0 ? encodeCursor(actions[actions.length - 1]) : null;
  return NextResponse.json({ actions, nextCursor });
}

export async function POST(request: Request) {
  const user = await getMaxUser();
  if (!user) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  let input: z.infer<typeof Action>;
  try {
    input = Action.parse(await request.json());
  } catch {
    return NextResponse.json({ error: "Invalid action" }, { status: 400 });
  }
  const payloadBytes = new TextEncoder().encode(JSON.stringify(input.payload)).length;
  if (payloadBytes > MAX_ACTION_PAYLOAD_BYTES) {
    return NextResponse.json({ error: "Payload too large" }, { status: 413 });
  }
  const [action] = await db
    .insert(schema.maxBusinessActions)
    .values({ maxUserId: user.id, actionType: input.actionType, payload: input.payload })
    .returning();
  await db.insert(schema.maxAuditLog).values({
    maxUserId: user.id,
    action: `created:${input.actionType}`,
    details: { actionId: action.id },
  });
  return NextResponse.json({ action }, { status: 201 });
}
