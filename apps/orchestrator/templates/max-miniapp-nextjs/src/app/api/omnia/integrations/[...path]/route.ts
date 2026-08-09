import { NextRequest, NextResponse } from "next/server";

const PROJECT_ID = process.env.OMNIA_PROJECT_ID || "";
const PLATFORM_API = (
  process.env.OMNIA_PLATFORM_API_URL || "https://constructor.lead-generator.ru"
).replace(/\/$/, "");
const PREVIEW_CAPABILITY_COOKIE = "omnia-max-preview-capability";

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
  const body = (await request.json().catch(() => ({}))) as {
    initData?: unknown;
    payload?: unknown;
  };
  const initData = typeof body.initData === "string" ? body.initData : "";
  const previewCapability = request.cookies.get(PREVIEW_CAPABILITY_COOKIE)?.value || "";
  if (!initData && !previewCapability) {
    return NextResponse.json(
      { error: { message: "Откройте приложение внутри MAX" } },
      { status: 401 },
    );
  }
  if (!initData && operation !== "ai") {
    return NextResponse.json(
      { error: { message: "Эта интеграция доступна после запуска приложения в MAX" } },
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
  const upstreamHeaders: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (initData) {
    upstreamHeaders["X-MAX-Init-Data"] = initData;
  } else {
    upstreamHeaders["X-Omnia-MAX-Preview-Capability"] = previewCapability;
  }
  const upstream = await fetch(`${PLATFORM_API}${upstreamPath}`, {
    method: readOnly ? "GET" : "POST",
    headers: upstreamHeaders,
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
