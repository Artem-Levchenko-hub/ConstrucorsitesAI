import { NextRequest, NextResponse } from "next/server";

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
    !["status", "payments", "payment-status", "leads", "catalog"].includes(
      operation,
    )
  ) {
    return NextResponse.json(
      { error: { message: "Unknown capability" } },
      { status: 404 },
    );
  }
  const body = (await request.json().catch(() => ({}))) as {
    initData?: unknown;
    payload?: unknown;
  };
  if (typeof body.initData !== "string") {
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
      : operation === "payment-status"
        ? `/api/runtime/projects/${PROJECT_ID}/payments/status`
      : `/api/runtime/projects/${PROJECT_ID}/${operation}`;
  const upstream = await fetch(`${PLATFORM_API}${upstreamPath}`, {
    method: readOnly ? "GET" : "POST",
    headers: {
      "Content-Type": "application/json",
      "X-MAX-Init-Data": body.initData,
    },
    body: readOnly ? undefined : JSON.stringify(body.payload || {}),
    cache: "no-store",
  });
  return new NextResponse(await upstream.text(), {
    status: upstream.status,
    headers: {
      "Content-Type":
        upstream.headers.get("content-type") || "application/json",
    },
  });
}
