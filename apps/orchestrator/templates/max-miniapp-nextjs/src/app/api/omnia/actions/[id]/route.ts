import { createHash } from "node:crypto";

import { and, eq, sql } from "drizzle-orm";
import { NextResponse } from "next/server";
import { z } from "zod";

import { schema, withMaxUser } from "@/lib/db";
import { getMaxUser } from "@/lib/max/session";

const MAX_ACTION_PAYLOAD_BYTES = 262_144;

const ActionId = z.string().uuid();
const ActionPatch = z
  .object({
    actionType: z.string().min(1).max(64).regex(/^[a-z0-9_-]+$/).optional(),
    status: z.string().min(1).max(64).regex(/^[a-z0-9_-]+$/).optional(),
    payload: z.record(z.unknown()).optional(),
  })
  .strict()
  .refine((value) => Object.keys(value).length > 0);

type Context = { params: Promise<{ id: string }> };

async function scopedId(context: Context): Promise<string | null> {
  const parsed = ActionId.safeParse((await context.params).id);
  return parsed.success ? parsed.data : null;
}

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

function notFound() {
  return NextResponse.json({ error: "Not found" }, { status: 404 });
}

export async function GET(_request: Request, context: Context) {
  const user = await getMaxUser();
  if (!user) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  const id = await scopedId(context);
  if (!id) return notFound();
  const [action] = await withMaxUser(user.id, (tx) =>
    tx
      .select()
      .from(schema.maxBusinessActions)
      .where(
        and(
          eq(schema.maxBusinessActions.id, id),
          eq(schema.maxBusinessActions.maxUserId, user.id),
        ),
      )
      .limit(1),
  );
  return action ? NextResponse.json({ action: { ...action, revision: actionRevision(action) } },
    { headers: { ETag: actionRevision(action), "Cache-Control": "no-store" } }) : notFound();
}

export async function PATCH(request: Request, context: Context) {
  const user = await getMaxUser();
  if (!user) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  const id = await scopedId(context);
  if (!id) return notFound();
  let input: z.infer<typeof ActionPatch>;
  try {
    input = ActionPatch.parse(await request.json());
  } catch {
    return NextResponse.json({ error: "Invalid action" }, { status: 400 });
  }
  if (
    input.payload !== undefined &&
    new TextEncoder().encode(JSON.stringify(input.payload)).length > MAX_ACTION_PAYLOAD_BYTES
  ) {
    return NextResponse.json({ error: "Payload too large" }, { status: 413 });
  }
  const expected = request.headers.get("If-Match");
  const result = await withMaxUser(user.id, async (tx) => {
    const [current] = await tx.select().from(schema.maxBusinessActions).where(and(
      eq(schema.maxBusinessActions.id, id), eq(schema.maxBusinessActions.maxUserId, user.id),
    )).limit(1).for("update");
    if (!current) return { status: 404, body: { error: "Not found" } };
    if (!expected) return { status: 428, body: { error: "Expected action revision required", code: "action_revision_required" } };
    if (!/^"[0-9a-f]{64}"$/.test(expected)) return { status: 400, body: { error: "Invalid action revision" } };
    if (expected !== actionRevision(current)) {
      return { status: 412, body: { error: "Action changed; reload before editing", code: "action_revision_conflict" } };
    }
    const [action] = await tx.update(schema.maxBusinessActions).set({ ...input,
      updatedAt: new Date(Math.max(Date.now(), current.updatedAt.getTime() + 1)),
    }).where(and(eq(schema.maxBusinessActions.id, id), eq(schema.maxBusinessActions.maxUserId, user.id))).returning();
    return { status: 200, body: { action: { ...action, revision: actionRevision(action) } } };
  });
  return NextResponse.json(result.body, { status: result.status,
    headers: { "Cache-Control": "no-store", ...("action" in result.body && result.body.action ? { ETag: result.body.action.revision } : {}) } });
}

export async function DELETE(_request: Request, context: Context) {
  const user = await getMaxUser();
  if (!user) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  const id = await scopedId(context);
  if (!id) return notFound();
  const deleted = await withMaxUser(user.id, async (tx) => {
    const [audit] = await tx
      .select({ id: schema.maxAuditLog.id, details: schema.maxAuditLog.details })
      .from(schema.maxAuditLog)
      .where(
        and(
          eq(schema.maxAuditLog.maxUserId, user.id),
          sql`${schema.maxAuditLog.details}->>'actionId' = ${id}`,
        ),
      )
      .limit(1);
    if (typeof audit?.details?.operationKey === "string") {
      await tx.execute(sql`SELECT pg_advisory_xact_lock(hashtext('omnia:action-write'), hashtext(${user.id + ":" + audit.details.operationKey}))`);
    }
    const [action] = await tx
      .delete(schema.maxBusinessActions)
      .where(
        and(
          eq(schema.maxBusinessActions.id, id),
          eq(schema.maxBusinessActions.maxUserId, user.id),
        ),
      )
      .returning({
        id: schema.maxBusinessActions.id,
        actionType: schema.maxBusinessActions.actionType,
      });
    if (!action) return null;
    await tx
      .delete(schema.maxAuditLog)
      .where(
        and(
          eq(schema.maxAuditLog.maxUserId, user.id),
          sql`${schema.maxAuditLog.details}->>'actionId' = ${id}`,
        ),
      );
    // Retain a payload-free receipt so a delayed retry cannot resurrect a deleted row.
    // Health fixtures erase their receipt too, permitting their temporary parent cleanup.
    if (typeof audit?.details?.operationKey === "string" && audit.details.healthProbe !== true) {
      await tx.insert(schema.maxAuditLog).values({ maxUserId: user.id, action: "deleted:operation",
        details: { actionId: id, operationKey: audit.details.operationKey,
          requestDigest: audit.details.requestDigest, deleted: true },
      });
    }
    let probeUserDeleted = false;
    if (
      audit?.details?.healthProbe === true &&
      audit?.details?.probeUserCreated === true
    ) {
      const [removed] = await tx
        .delete(schema.maxUsers)
        .where(
          and(
            eq(schema.maxUsers.maxUserId, user.id),
            sql`NOT EXISTS (
              SELECT 1 FROM ${schema.maxBusinessActions}
              WHERE ${schema.maxBusinessActions.maxUserId} = ${user.id}
            )`,
            sql`NOT EXISTS (
              SELECT 1 FROM ${schema.maxConsents}
              WHERE ${schema.maxConsents.maxUserId} = ${user.id}
            )`,
            sql`NOT EXISTS (
              SELECT 1 FROM ${schema.maxAnalyticsEvents}
              WHERE ${schema.maxAnalyticsEvents.maxUserId} = ${user.id}
            )`,
            sql`NOT EXISTS (
              SELECT 1 FROM ${schema.maxBotOutbox}
              WHERE ${schema.maxBotOutbox.maxUserId} = ${user.id}
            )`,
            sql`NOT EXISTS (
              SELECT 1 FROM ${schema.maxAuditLog}
              WHERE ${schema.maxAuditLog.maxUserId} = ${user.id}
            )`,
          ),
        )
        .returning({ maxUserId: schema.maxUsers.maxUserId });
      probeUserDeleted = removed?.maxUserId === user.id;
    }
    return { ...action, probeUserDeleted };
  });
  return deleted
    ? NextResponse.json({
        deleted: true,
        id: deleted.id,
        probeUserDeleted: deleted.probeUserDeleted,
      })
    : notFound();
}
