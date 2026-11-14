import { createHash, createHmac } from "node:crypto";
import { NextRequest, NextResponse } from "next/server";

import { getMaxUser } from "@/lib/max/session";
import { ConsentBridgeError, getConsentContext, getConsentPolicy } from "@/lib/omnia/consent";

const PROJECT_ID = process.env.OMNIA_PROJECT_ID || "";
const PLATFORM_API = (
  process.env.OMNIA_PLATFORM_API_URL || "https://yleum.ru"
).replace(/\/$/, "");

type Context = { params: Promise<{ path: string[] }> };

const upstreamOperations = {
  status: ["integrations", "GET"],
  payments: ["payments", "POST"],
  "payment-status": ["payments/status", "POST"],
  leads: ["leads", "POST"],
  "lead-list": ["leads", "GET"],
  "lead-status": ["leads/status", "POST"],
  "lead-receipt": ["leads/receipt", "POST"],
  catalog: ["catalog", "GET"],
  orders: ["orders", "POST"],
  "order-list": ["orders", "GET"],
  "order-status": ["orders/status", "POST"],
  "order-details": ["orders/details", "POST"],
  "order-checkout": ["orders/checkout", "POST"],
  "order-checkout-status": ["orders/checkout/status", "POST"],
  "order-checkout-receipt": ["orders/checkout/receipt", "POST"],
  ai: ["ai", "POST"],
} as const;
const managedOrderId = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function checkoutPayload(payload: unknown, create: boolean): Record<string, string> | null {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) return null;
  const fields = payload as Record<string, unknown>;
  const { order_id, return_url } = fields;
  if (Object.keys(fields).some(key => key !== "order_id" && !(create && key === "return_url")) ||
      typeof order_id !== "string" || !managedOrderId.test(order_id) ||
      (return_url !== undefined && (typeof return_url !== "string" || return_url.length > 512))) return null;
  return return_url === undefined ? { order_id } : { order_id, return_url };
}

export async function POST(request: NextRequest, context: Context) {
  if (!PROJECT_ID) {
    return NextResponse.json(
      { error: { message: "Integration Hub ещё не настроен" } },
      { status: 503 },
    );
  }
  const { path } = await context.params;
  const operation = path.join("/");
  const target = Object.hasOwn(upstreamOperations, operation)
    ? upstreamOperations[operation as keyof typeof upstreamOperations]
    : null;
  if (!target && operation !== "consent-policy" && operation !== "consent-state") {
    return NextResponse.json(
      { error: { message: "Unknown capability" } },
      { status: 404 },
    );
  }
  const parsed: unknown = await request.json().catch(() => null);
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    return NextResponse.json(
      { error: { message: "Некорректный запрос интеграции" } },
      { status: 400 },
    );
  }
  const body = parsed as {
    initData?: unknown;
    payload?: unknown;
    expectedActor?: unknown;
  };
  // Only the immutable core imports the signed-cookie session implementation.
  // The portable product session adapter must never mint these assertions.
  const user = await getMaxUser();
  if (!target) {
    // Unlike legacy integration operations, consent never accepts raw initData
    // from product code and never converts a business request into a grant.
    if (!user) return NextResponse.json({ error: { message: "Откройте приложение внутри MAX" } }, { status: 401 });
    try {
      return NextResponse.json(operation === "consent-policy"
        ? await getConsentPolicy(user.id) : await getConsentContext(user.id));
    } catch (error) {
      return NextResponse.json({ error: { message: "Не удалось проверить согласие" } },
        { status: error instanceof ConsentBridgeError ? error.status : 503 });
    }
  }
  const checkout = operation === "order-checkout" || operation === "order-checkout-status" ||
    operation === "order-checkout-receipt";
  if (checkout && (!user || !/^[1-9][0-9]{0,19}$/.test(user.id))) {
    return NextResponse.json({ error: { code: "unauthorized", message: "Откройте приложение внутри MAX" } }, { status: 401 });
  }
  if (checkout && body.expectedActor !== user?.id) {
    return NextResponse.json({ error: { code: "actor_changed", message: "Учётная запись MAX изменилась" } }, { status: 409 });
  }
  const payload = checkout ? checkoutPayload(body.payload, operation === "order-checkout") : body.payload || {};
  if (payload === null) {
    return NextResponse.json({ error: { code: "invalid_request", message: "Некорректные данные заказа" } }, { status: 400 });
  }
  const rawInitData = typeof body.initData === "string" ? body.initData : "";
  if ((user && !/^[1-9][0-9]{0,19}$/.test(user.id)) || (!user && !rawInitData)) {
    return NextResponse.json(
      { error: { message: "Откройте приложение внутри MAX" } },
      { status: 401 },
    );
  }
  const [upstreamOperation, method] = target;
  const upstreamPath = `/api/runtime/projects/${PROJECT_ID}/${upstreamOperation}`;
  const readOnly = method === "GET";
  const upstreamBody = readOnly ? "" : JSON.stringify(payload);
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (user) {
    const token = process.env.MAX_BOT_TOKEN;
    if (!token) {
      return NextResponse.json(
        { error: { message: "Integration Hub ещё не настроен" } },
        { status: 503 },
      );
    }
    const issued = Math.floor(Date.now() / 1000);
    const encoded = Buffer.from(JSON.stringify({
      project_id: PROJECT_ID,
      max_user_id: user.id,
      iat: issued,
      exp: issued + 60,
      method,
      path: upstreamPath,
      body_sha256: createHash("sha256").update(upstreamBody).digest("hex"),
    })).toString("base64url");
    const key = createHmac("sha256", token)
      .update("omnia:integration-assertion:v1").digest();
    const signed = `v1.${encoded}`;
    headers["X-Omnia-Integration-Assertion"] = `${signed}.${
      createHmac("sha256", key).update(signed).digest("hex")
    }`;
  } else {
    headers["X-MAX-Init-Data"] = rawInitData;
  }
  try {
    const upstream = await fetch(`${PLATFORM_API}${upstreamPath}`, {
      method,
      headers,
      body: readOnly ? undefined : upstreamBody,
      cache: "no-store",
      ...(checkout ? { redirect: "error" as const } : {}),
      signal: AbortSignal.timeout(60_000),
    });
    return new NextResponse(await upstream.text(), {
      status: upstream.status,
      headers: {
        "Content-Type": upstream.headers.get("content-type") || "application/json",
        ...(checkout ? { "Cache-Control": "private, no-store" } : {}),
      },
    });
  } catch {
    return NextResponse.json(
      { error: { message: "Сервис интеграций временно недоступен. Повторите запрос позже." } },
      { status: 503 },
    );
  }
}
