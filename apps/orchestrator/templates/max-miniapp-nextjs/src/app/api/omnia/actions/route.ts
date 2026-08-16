import { and, desc, eq } from "drizzle-orm";
import { NextResponse } from "next/server";
import { z } from "zod";

import { db, schema } from "@/lib/db";
import { getMaxUser } from "@/lib/max/session";

const Action = z.object({
  actionType: z.string().min(1).max(64).regex(/^[a-z0-9_-]+$/),
  payload: z.record(z.unknown()).default({}),
});
const ProofIdempotencyKey = z.string().regex(/^[a-f0-9]{64}$/);

function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    return `{${Object.keys(record)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${canonicalJson(record[key])}`)
      .join(",")}}`;
  }
  return JSON.stringify(value) ?? "null";
}

export async function GET() {
  const user = await getMaxUser();
  if (!user) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  const actions = await db
    .select()
    .from(schema.maxBusinessActions)
    .where(eq(schema.maxBusinessActions.maxUserId, user.id))
    .orderBy(desc(schema.maxBusinessActions.createdAt))
    .limit(100);
  return NextResponse.json({ actions });
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
  if (JSON.stringify(input.payload).length > 16_384) {
    return NextResponse.json({ error: "Payload too large" }, { status: 413 });
  }
  const proofHeader = request.headers.get("x-omnia-proof-key");
  const proofKey = proofHeader === null ? null : ProofIdempotencyKey.safeParse(proofHeader);
  if (proofKey !== null && !proofKey.success) {
    return NextResponse.json({ error: "Invalid proof idempotency key" }, { status: 400 });
  }
  const idempotencyKey = proofKey === null ? null : proofKey.data;
  const inserted = await db
    .insert(schema.maxBusinessActions)
    .values({
      maxUserId: user.id,
      actionType: input.actionType,
      idempotencyKey,
      payload: input.payload,
    })
    .onConflictDoNothing({
      target: [schema.maxBusinessActions.maxUserId, schema.maxBusinessActions.idempotencyKey],
    })
    .returning();
  if (inserted[0]) {
    await db.insert(schema.maxAuditLog).values({
      maxUserId: user.id,
      action: `created:${input.actionType}`,
      details: { actionId: inserted[0].id },
    });
    return NextResponse.json({ action: inserted[0] }, { status: 201 });
  }
  if (idempotencyKey === null) {
    return NextResponse.json({ error: "Action was not created" }, { status: 409 });
  }
  const [action] = await db
    .select()
    .from(schema.maxBusinessActions)
    .where(
      and(
        eq(schema.maxBusinessActions.maxUserId, user.id),
        eq(schema.maxBusinessActions.idempotencyKey, idempotencyKey),
      ),
    )
    .limit(1);
  if (!action) {
    return NextResponse.json({ error: "Idempotent action lookup failed" }, { status: 409 });
  }
  if (
    action.actionType !== input.actionType ||
    canonicalJson(action.payload) !== canonicalJson(input.payload)
  ) {
    return NextResponse.json({ error: "Idempotency key payload conflict" }, { status: 409 });
  }
  return NextResponse.json({ action, deduplicated: true }, { status: 200 });
}
