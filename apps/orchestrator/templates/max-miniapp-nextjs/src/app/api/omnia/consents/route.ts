import { and, desc, eq, lt, ne, or } from "drizzle-orm";
import { NextResponse } from "next/server";
import { z } from "zod";

import { schema, withMaxUser } from "@/lib/db";
import { getMaxUser } from "@/lib/max/session";
import { ConsentBridgeError, getConsentContext, recordConsent } from "@/lib/omnia/consent";

const Consent = z.object({
  consentType: z.string().min(1).max(64).regex(/^[a-z0-9_-]+$/),
  granted: z.boolean(),
  policyVersion: z.string().min(1).max(32),
  eventId: z.string().uuid().optional(),
  expectedRevision: z.number().int().nonnegative().max(Number.MAX_SAFE_INTEGER).optional(),
  policyHash: z.string().regex(/^[a-f0-9]{64}$/i).optional(),
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
  let platform;
  try { platform = await getConsentContext(user.id); }
  catch (error) { return consentError(error); }
  if (new URL(request.url).searchParams.get("platform") === "1") {
    return NextResponse.json({ ...platform, actor: user.id });
  }
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
  if (platform.policy.enabled) filters.push(ne(schema.maxConsents.consentType, "personal_data"));
  if (cursor) {
    filters.push(
      or(
        lt(schema.maxConsents.createdAt, cursor.createdAt),
        and(eq(schema.maxConsents.createdAt, cursor.createdAt), lt(schema.maxConsents.id, cursor.id)),
      )!,
    );
  }
  const rows = await withMaxUser(user.id, (tx) =>
    tx
      .select()
      .from(schema.maxConsents)
      .where(and(...filters))
      .orderBy(desc(schema.maxConsents.createdAt), desc(schema.maxConsents.id))
      .limit(query.data.limit + 1),
  );
  const hasMore = rows.length > query.data.limit;
  const consents = hasMore ? rows.slice(0, query.data.limit) : rows;
  const nextCursor =
    hasMore && consents.length > 0 ? encodeCursor(consents[consents.length - 1]) : null;
  return NextResponse.json({ consents, nextCursor, ...platform, actor: user.id });
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
  const expectedActor = request.headers.get("X-Omnia-Actor");
  if (expectedActor && expectedActor !== user.id) {
    return NextResponse.json({ error: "Consent actor changed" }, { status: 409 });
  }
  try {
    const platform = await getConsentContext(user.id);
    const result = platform.policy.enabled && input.consentType === "personal_data"
      ? await recordConsent(user.id, input, platform) : null;
    const [consent] = result ? [] : await withMaxUser(user.id, (tx) =>
      tx.insert(schema.maxConsents).values({
        maxUserId: user.id, consentType: input.consentType, granted: input.granted,
        policyVersion: input.policyVersion,
      }).onConflictDoNothing().returning(),
    );
    // The platform journal is the sole ordered audit for enabled personal-data
    // consent. A local mirror cannot preserve ordering across concurrent revokes.
    return NextResponse.json({ consent: consent ?? null, policy: platform.policy, state: result?.state ?? platform.state }, { status: 201 });
  } catch (error) { return consentError(error); }
}

function consentError(error: unknown) {
  return NextResponse.json({ error: { code: error instanceof ConsentBridgeError ? error.code : "consent_service_unavailable",
    message: error instanceof ConsentBridgeError ? error.message : "Не удалось сохранить согласие. Повторите запрос позже." } },
  { status: error instanceof ConsentBridgeError ? error.status : 503 });
}
