import { timingSafeEqual } from "node:crypto";

import { NextRequest, NextResponse } from "next/server";

const PROJECT_ID = process.env.OMNIA_PROJECT_ID || "";
const PLATFORM_API = (
  process.env.OMNIA_PLATFORM_API_URL || "https://constructor.lead-generator.ru"
).replace(/\/$/, "");
const PREVIEW_CAPABILITY_COOKIE = "omnia-max-preview-capability";
const PROOF_KEY_RE = /^[a-f0-9]{64}$/;
const PROOF_AUTHORIZATION_RE = /^v1\.[A-Za-z0-9_-]{32,512}$/;
const PROOF_KEY_BOUND_HEADER = "x-omnia-proof-key-bound";
const PROOF_INFRASTRUCTURE_HEADER = "X-Omnia-Proof-Infrastructure";
const PROOF_OWNER_DEPENDENCY_HEADER = "X-Omnia-Proof-Owner-Dependency";
const PROOF_REQUIRED_PROVIDERS: Record<string, readonly string[]> = {
  catalog: ["iiko", "moysklad"],
  leads: ["bitrix24", "amocrm"],
  payments: ["yookassa"],
  "payment-status": ["yookassa"],
};

type Context = { params: Promise<{ path: string[] }> };

function proofKeyIsBound(validation: Response, proofKey: string): boolean {
  const boundKey = validation.headers.get(PROOF_KEY_BOUND_HEADER) || "";
  const expected = Buffer.from(proofKey, "utf8");
  const actual = Buffer.from(boundKey, "utf8");
  return expected.length === actual.length && timingSafeEqual(expected, actual);
}

function proofInfrastructureUnavailable(): NextResponse {
  return NextResponse.json(
    {
      error: {
        code: "proof_infrastructure_unavailable",
        message: "Не удалось подтвердить инфраструктуру интеграции для проверки.",
      },
    },
    {
      status: 503,
      headers: {
        "Cache-Control": "no-store",
        [PROOF_INFRASTRUCTURE_HEADER]: "unavailable",
      },
    },
  );
}

function externalVerificationUnavailable(operation: string): NextResponse {
  return NextResponse.json(
    {
      error: {
        code: "external_verification_unavailable",
        message: "Для внешней операции нет безопасного provider test-mode.",
        details: { operation },
      },
    },
    {
      status: 424,
      headers: {
        "Cache-Control": "no-store",
        [PROOF_OWNER_DEPENDENCY_HEADER]: "required",
      },
    },
  );
}

function proofProviderRequired(
  operation: string,
  providers: readonly string[],
): NextResponse | null {
  const requiredProviders = PROOF_REQUIRED_PROVIDERS[operation] || [];
  if (
    requiredProviders.length === 0 ||
    requiredProviders.some((provider) => providers.includes(provider))
  ) {
    return null;
  }
  return NextResponse.json(
    {
      error: {
        code: "integration_required",
        message: "Для этой операции требуется активная интеграция.",
        details: { operation, required_providers: requiredProviders },
      },
    },
    {
      status: 409,
      headers: {
        "Cache-Control": "no-store",
        [PROOF_OWNER_DEPENDENCY_HEADER]: "required",
      },
    },
  );
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
  const proofKey = request.headers.get("x-omnia-proof-key") || "";
  const proofAuthorization = request.headers.get("x-omnia-proof-authorization") || "";
  const proofRequested =
    !initData &&
    previewCapability.length > 0 &&
    (proofKey.length > 0 || proofAuthorization.length > 0);
  if (proofRequested && (!PROOF_KEY_RE.test(proofKey) || !PROOF_AUTHORIZATION_RE.test(proofAuthorization))) {
    return NextResponse.json(
      {
        error: {
          code: "proof_authorization_invalid",
          message: "Защищённое подтверждение проверки недействительно.",
        },
      },
      { status: 403, headers: { "Cache-Control": "no-store" } },
    );
  }
  if (proofRequested) {
    let validation: Response;
    try {
      validation = await fetch(
        `${PLATFORM_API}/api/runtime/projects/${PROJECT_ID}/integrations`,
        {
          method: "GET",
          headers: {
            "X-Omnia-MAX-Preview-Capability": previewCapability,
            "X-Omnia-Proof-Key": proofKey,
            "X-Omnia-Proof-Authorization": proofAuthorization,
          },
          cache: "no-store",
        },
      );
    } catch {
      return proofInfrastructureUnavailable();
    }
    if (validation.status >= 500) {
      return proofInfrastructureUnavailable();
    }
    if (!validation.ok) {
      return new NextResponse(await validation.text(), {
        status: validation.status,
        headers: {
          "Content-Type": validation.headers.get("content-type") || "application/json",
        },
      });
    }
    const statusPayload: unknown = await validation.json().catch(() => null);
    const providers =
      statusPayload &&
      typeof statusPayload === "object" &&
      Array.isArray((statusPayload as { providers?: unknown }).providers)
        ? (statusPayload as { providers: unknown[] }).providers.filter(
            (provider): provider is string => typeof provider === "string",
          )
        : null;
    if (!providers || !proofKeyIsBound(validation, proofKey)) {
      return proofInfrastructureUnavailable();
    }
    const providerCheck = proofProviderRequired(operation, providers);
    if (providerCheck) return providerCheck;
    // Proof may exercise only real read-only provider adapters. Paid or mutating
    // operations require an owner-configured provider test-mode; never fake them.
    if (!["status", "catalog"].includes(operation)) {
      return externalVerificationUnavailable(operation);
    }
    if (operation === "status") return NextResponse.json(statusPayload);
    let catalog: Response;
    try {
      catalog = await fetch(`${PLATFORM_API}/api/runtime/projects/${PROJECT_ID}/catalog`, {
        method: "GET",
        headers: { "X-Omnia-MAX-Preview-Capability": previewCapability },
        cache: "no-store",
      });
    } catch {
      return proofInfrastructureUnavailable();
    }
    if (catalog.status >= 500) return proofInfrastructureUnavailable();
    return new NextResponse(await catalog.text(), {
      status: catalog.status,
      headers: {
        "Cache-Control": "no-store",
        "Content-Type": catalog.headers.get("content-type") || "application/json",
      },
    });
  }
  const previewAllowed = ["ai", "status", "catalog"].includes(operation);
  if (!initData && !previewAllowed) {
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
