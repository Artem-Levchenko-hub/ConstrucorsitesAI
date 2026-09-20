import { and, eq, sql } from "drizzle-orm";
import { NextResponse } from "next/server";
import { z } from "zod";

import { db, schema } from "@/lib/db";
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

function notFound() {
  return NextResponse.json({ error: "Not found" }, { status: 404 });
}

export async function GET(_request: Request, context: Context) {
  const user = await getMaxUser();
  if (!user) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  const id = await scopedId(context);
  if (!id) return notFound();
  const [action] = await db
    .select()
    .from(schema.maxBusinessActions)
    .where(
      and(
        eq(schema.maxBusinessActions.id, id),
        eq(schema.maxBusinessActions.maxUserId, user.id),
      ),
    )
    .limit(1);
  return action ? NextResponse.json({ action }) : notFound();
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
  const [action] = await db
    .update(schema.maxBusinessActions)
    .set({ ...input, updatedAt: new Date() })
    .where(
      and(
        eq(schema.maxBusinessActions.id, id),
        eq(schema.maxBusinessActions.maxUserId, user.id),
      ),
    )
    .returning();
  return action ? NextResponse.json({ action }) : notFound();
}

export async function DELETE(_request: Request, context: Context) {
  const user = await getMaxUser();
  if (!user) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  const id = await scopedId(context);
  if (!id) return notFound();
  const deleted = await db.transaction(async (tx) => {
    const [audit] = await tx
      .select({ details: schema.maxAuditLog.details })
      .from(schema.maxAuditLog)
      .where(
        and(
          eq(schema.maxAuditLog.maxUserId, user.id),
          sql`${schema.maxAuditLog.details}->>'actionId' = ${id}`,
        ),
      )
      .limit(1);
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
    let probeUserDeleted = false;
    if (
      action.actionType.startsWith("omnia_health_") &&
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
