import { createHash } from "node:crypto";

import { and, desc, eq, lt, or, sql } from "drizzle-orm";
import { NextResponse } from "next/server";
import { z } from "zod";

import { schema, withMaxUser } from "@/lib/db";
import { forwardMaxAnalytics } from "@/lib/omnia/analytics";
import { getMaxUser } from "@/lib/max/session";

const DEFAULT_ACTION_LIMIT = 250;
const MAX_ACTION_LIMIT = 1000;
const MAX_ACTION_PAYLOAD_BYTES = 262_144;

const Action = z.object({
  actionType: z.string().min(1).max(64).regex(/^[a-z0-9_-]+$/),
  payload: z.record(z.unknown()).default({}),
  operationKey: z.string().uuid().optional(),
});

const ActionQuery = z.object({
  limit: z.coerce.number().int().min(1).max(MAX_ACTION_LIMIT).default(DEFAULT_ACTION_LIMIT),
  cursor: z.string().trim().min(1).optional(),
  actionType: z.string().min(1).max(64).regex(/^[a-z0-9_-]+$/).optional(),
});

const ActionCursor = z.object({
  createdAt: z.string().datetime(),
  id: z.string().uuid(),
});

function canonicalAction(value: unknown): unknown {
  if (value instanceof Date) return value.toISOString();
  if (Array.isArray(value)) return value.map(canonicalAction);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0)
      .map(([key, item]) => [key, canonicalAction(item)]));
  }
  return value;
}

function actionRevision(action: Record<string, unknown>): string {
  return `"${createHash("sha256").update(JSON.stringify(canonicalAction(action))).digest("hex")}"`;
}

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
  if (query.data.actionType) {
    filters.push(eq(schema.maxBusinessActions.actionType, query.data.actionType));
  }
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
  const rows = await withMaxUser(user.id, (tx) =>
    tx
      .select()
      .from(schema.maxBusinessActions)
      .where(and(...filters))
      .orderBy(desc(schema.maxBusinessActions.createdAt), desc(schema.maxBusinessActions.id))
      .limit(query.data.limit + 1),
  );
  const hasMore = rows.length > query.data.limit;
  const actions = hasMore ? rows.slice(0, query.data.limit) : rows;
  const nextCursor =
    hasMore && actions.length > 0 ? encodeCursor(actions[actions.length - 1]) : null;
  return NextResponse.json({ actions: actions.map(action => ({ ...action, revision: actionRevision(action) })), nextCursor },
    { headers: { "Cache-Control": "no-store" } });
}

export async function POST(request: Request) {
  const user = await getMaxUser();
  if (!user) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  const expectedActor = request.headers.get("X-Omnia-Actor");
  if (expectedActor !== null && expectedActor !== user.id) {
    return NextResponse.json({ error: "MAX account changed; retry with the current account",
      code: "action_actor_changed" }, { status: 409 });
  }
  let input: z.infer<typeof Action>;
  try {
    input = Action.parse(await request.json());
  } catch {
    return NextResponse.json({ error: "Invalid action" }, { status: 400 });
  }
  if (!input.operationKey) {
    return NextResponse.json({ error: "Operation identity required", code: "action_operation_required" }, { status: 428 });
  }
  const requestDigest = createHash("sha256").update(JSON.stringify(canonicalAction({
    actionType: input.actionType, payload: input.payload,
  }))).digest("hex");
  const payloadBytes = new TextEncoder().encode(JSON.stringify(input.payload)).length;
  if (payloadBytes > MAX_ACTION_PAYLOAD_BYTES) {
    return NextResponse.json({ error: "Payload too large" }, { status: 413 });
  }
  const result = await withMaxUser(user.id, async (tx) => {
    await tx.execute(sql`SELECT pg_advisory_xact_lock(hashtext('omnia:action-write'), hashtext(${user.id + ":" + input.operationKey}))`);
    const [receipt] = await tx.select().from(schema.maxAuditLog).where(and(
      eq(schema.maxAuditLog.maxUserId, user.id),
      sql`${schema.maxAuditLog.details}->>'operationKey' = ${input.operationKey}`,
    )).limit(1);
    if (receipt) {
      if (receipt.details.requestDigest !== requestDigest) {
        return { status: 409, body: { error: "Operation identity was reused", code: "action_operation_conflict" } };
      }
      const [action] = await tx.select().from(schema.maxBusinessActions).where(and(
        eq(schema.maxBusinessActions.maxUserId, user.id),
        eq(schema.maxBusinessActions.id, String(receipt.details.actionId)),
      )).limit(1);
      if (!action || receipt.details.deleted === true) {
        return { status: 410, body: { error: "Operation was deleted", code: "action_operation_deleted" } };
      }
      return { status: 201, body: { action: { ...action, revision: actionRevision(action) },
        probeUserCreated: receipt.details.probeUserCreated === true, replayed: true } };
    }
    // A valid server-signed actor is sufficient authority to materialize its
    // FK parent. Real MAX login already creates this row; activation probes use
    // the same signed-session contract without inventing an external login.
    const createdUsers = await tx
      .insert(schema.maxUsers)
      .values({ maxUserId: user.id, firstName: "" })
      .onConflictDoNothing({ target: schema.maxUsers.maxUserId })
      .returning({ maxUserId: schema.maxUsers.maxUserId });
    const [created] = await tx
      .insert(schema.maxBusinessActions)
      .values({ maxUserId: user.id, actionType: input.actionType, payload: input.payload })
      .returning();
    await tx.insert(schema.maxAuditLog).values({
      maxUserId: user.id,
      action: `created:${input.actionType}`,
      details: {
        actionId: created.id,
        operationKey: input.operationKey,
        requestDigest,
        probeUserCreated: createdUsers.length === 1,
        healthProbe: input.actionType.startsWith("omnia_health_"),
      },
    });
    return { status: 201, body: { action: { ...created, revision: actionRevision(created) }, probeUserCreated: createdUsers.length === 1 } };
  });
  if (result.status === 201 && "action" in result.body && result.body.action &&
      !input.actionType.startsWith("omnia_health_")) {
    await forwardMaxAnalytics(user.id, result.body.action.id, "action");
  }
  return NextResponse.json(result.body, { status: result.status,
    headers: { "Cache-Control": "no-store", ...("action" in result.body && result.body.action ? { ETag: result.body.action.revision } : {}) } });
}
