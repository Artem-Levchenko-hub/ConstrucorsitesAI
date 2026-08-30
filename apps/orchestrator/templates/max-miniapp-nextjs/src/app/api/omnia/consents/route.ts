import { and, desc, eq, lt, or } from "drizzle-orm";
import { NextResponse } from "next/server";
import { z } from "zod";

import { db, schema } from "@/lib/db";
import { getMaxUser } from "@/lib/max/session";

const Consent = z.object({
  consentType: z.string().min(1).max(64).regex(/^[a-z0-9_-]+$/),
  granted: z.boolean(),
  policyVersion: z.string().min(1).max(32),
});

const DEFAULT_CONSENT_LIMIT = 250;
const MAX_CONSENT_LIMIT = 1000;

const ConsentQuery = z.object({
  limit: z.coerce.number().int().min(1).max(MAX_CONSENT_LIMIT).default(DEFAULT_CONSENT_LIMIT),
  cursor: z.string().trim().min(1).optional(),
});

const ConsentCursor = z.object({
  createdAt: z.string().datetime(),
  id: z.string().uuid(),
});

function encodeCursor(consent: { createdAt: Date; id: string }): string {
  return `${consent.createdAt.toISOString()}::${consent.id}`;
}

function decodeCursor(raw: string | null): { createdAt: Date; id: string } | null {
  if (!raw) return null;
  const [createdAt, id] = raw.split("::", 2);
  const parsed = ConsentCursor.safeParse({ createdAt, id });
  if (!parsed.success) return null;
  return { createdAt: new Date(parsed.data.createdAt), id: parsed.data.id };
}

export async function GET(request: Request) {
  const user = await getMaxUser();
  if (!user) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  const query = ConsentQuery.safeParse(
    Object.fromEntries(new URL(request.url).searchParams.entries()),
  );
  if (!query.success) {
    return NextResponse.json({ error: "Invalid consent query" }, { status: 400 });
  }
  const cursor = decodeCursor(query.data.cursor || null);
  if (query.data.cursor && !cursor) {
    return NextResponse.json({ error: "Invalid consent cursor" }, { status: 400 });
  }
  const filters = [eq(schema.maxConsents.maxUserId, user.id)];
  if (cursor) {
    filters.push(
      or(
        lt(schema.maxConsents.createdAt, cursor.createdAt),
        and(eq(schema.maxConsents.createdAt, cursor.createdAt), lt(schema.maxConsents.id, cursor.id)),
      )!,
    );
  }
  const rows = await db
    .select()
    .from(schema.maxConsents)
    .where(and(...filters))
    .orderBy(desc(schema.maxConsents.createdAt), desc(schema.maxConsents.id))
    .limit(query.data.limit + 1);
  const hasMore = rows.length > query.data.limit;
  const consents = hasMore ? rows.slice(0, query.data.limit) : rows;
  const nextCursor =
    hasMore && consents.length > 0 ? encodeCursor(consents[consents.length - 1]) : null;
  return NextResponse.json({ consents, nextCursor });
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
