import { createHash, createHmac } from "node:crypto";
import { NextRequest, NextResponse } from "next/server";

import { getMaxUser } from "@/lib/max/session";

const PROJECT_ID = process.env.OMNIA_PROJECT_ID || "";
const PLATFORM_API = (
  process.env.OMNIA_PLATFORM_API_URL || "https://constructor.lead-generator.ru"
).replace(/\/$/, "");

type Context = { params: Promise<{ path: string[] }> };

export async function POST(request: NextRequest, context: Context) {
  if (!PROJECT_ID) {
    return NextResponse.json(
      { error: { message: "Integration Hub ещё не настроен" } },
      { status: 503 },
    );
  }
  const { path } = await context.params;
  const operation = path.join("/");
  if (
    !["status", "payments", "payment-status", "leads", "catalog", "ai"].includes(
      operation,
    )
  ) {
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
  };
  // Only the immutable core imports the signed-cookie session implementation.
  // The portable product session adapter must never mint these assertions.
  const user = await getMaxUser();
  const rawInitData = typeof body.initData === "string" ? body.initData : "";
  if ((user && !/^[1-9][0-9]{0,19}$/.test(user.id)) || (!user && !rawInitData)) {
    return NextResponse.json(
      { error: { message: "Откройте приложение внутри MAX" } },
      { status: 401 },
    );
  }
  const readOnly = operation === "status" || operation === "catalog";
  const upstreamPath =
    operation === "status"
      ? `/api/runtime/projects/${PROJECT_ID}/integrations`
      : operation === "catalog"
        ? `/api/runtime/projects/${PROJECT_ID}/catalog`
      : operation === "ai"
        ? `/api/runtime/projects/${PROJECT_ID}/ai`
      : operation === "payment-status"
        ? `/api/runtime/projects/${PROJECT_ID}/payments/status`
      : `/api/runtime/projects/${PROJECT_ID}/${operation}`;
  const method = readOnly ? "GET" : "POST";
  const upstreamBody = readOnly ? "" : JSON.stringify(body.payload || {});
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
      signal: AbortSignal.timeout(60_000),
    });
    return new NextResponse(await upstream.text(), {
      status: upstream.status,
      headers: {
        "Content-Type": upstream.headers.get("content-type") || "application/json",
      },
    });
  } catch {
    return NextResponse.json(
      { error: { message: "Сервис интеграций временно недоступен. Повторите запрос позже." } },
      { status: 503 },
    );
  }
}
