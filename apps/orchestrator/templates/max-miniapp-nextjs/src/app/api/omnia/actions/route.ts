import { desc, eq } from "drizzle-orm";
import { NextResponse } from "next/server";
import { z } from "zod";

import { db, schema } from "@/lib/db";
import { getMaxUser } from "@/lib/max/session";

const Action = z.object({
  actionType: z.string().min(1).max(64).regex(/^[a-z0-9_-]+$/),
  payload: z.record(z.unknown()).default({}),
});

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
