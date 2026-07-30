import { desc, eq } from "drizzle-orm";
import { NextResponse } from "next/server";
import { z } from "zod";

import { db, schema } from "@/lib/db";
import { getMaxUser } from "@/lib/max/session";

const Consent = z.object({
  consentType: z.string().min(1).max(64).regex(/^[a-z0-9_-]+$/),
  granted: z.boolean(),
  policyVersion: z.string().min(1).max(32),
});

export async function GET() {
  const user = await getMaxUser();
  if (!user) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  const consents = await db
    .select()
    .from(schema.maxConsents)
    .where(eq(schema.maxConsents.maxUserId, user.id))
    .orderBy(desc(schema.maxConsents.createdAt))
    .limit(100);
  return NextResponse.json({ consents });
}

export async function POST(request: Request) {
  const user = await getMaxUser();
  if (!user) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  let input: z.infer<typeof Consent>;
  try {
    input = Consent.parse(await request.json());
  } catch {
    return NextResponse.json({ error: "Invalid consent" }, { status: 400 });
  }
  const [consent] = await db
    .insert(schema.maxConsents)
    .values({ maxUserId: user.id, ...input })
    .returning();
  return NextResponse.json({ consent }, { status: 201 });
}
