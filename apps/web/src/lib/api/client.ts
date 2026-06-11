import type { ApiErrorBody, ApiErrorCode } from "./types";

const baseUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/**
 * Typed error mirroring docs/01-api-contract.md error envelope. Throw site is
 * `apiFetch`; consumers `instanceof ApiError` to discriminate from network errors.
 */
export class ApiError extends Error {
  readonly code: ApiErrorCode;
  readonly status: number;
  readonly details?: Record<string, unknown>;

  constructor(status: number, body: ApiErrorBody["error"]) {
    super(body.message);
    this.name = "ApiError";
    this.code = body.code;
    this.status = status;
    this.details = body.details;
  }
}

type RequestInitWithJson = Omit<RequestInit, "body"> & {
  /** Object to JSON.stringify; sets Content-Type: application/json. */
  json?: unknown;
  /**
   * If set, the request is aborted after this many ms. Combines with any
   * caller-supplied `signal` via AbortSignal.any() so caller can still
   * cancel manually. Throws an `ApiError(0, timeout)` so callers don't
   * see the raw DOMException.
   */
  timeoutMs?: number;
};

/**
 * Narrow API over fetch: takes a path and an init, returns parsed JSON or throws
 * `ApiError` on a non-2xx response. Hides baseURL, credentials cookie, JSON
 * serialization, error envelope parsing, the 204 quirk, and (when `timeoutMs`
 * is set) raw fetch hangs.
 */
export async function apiFetch<T>(
  path: string,
  init: RequestInitWithJson = {},
): Promise<T> {
  const { json, headers, timeoutMs, signal, ...rest } = init;

  // Compose caller's signal with a timeout signal so EITHER aborts the
  // fetch. Without this, a server that accepts the TCP connection but
  // never writes the response leaves fetch() hanging forever — and the
  // user sees a stuck "AI читает контекст" spinner.
  const signals: AbortSignal[] = [];
  if (signal) signals.push(signal);
  if (timeoutMs !== undefined) signals.push(AbortSignal.timeout(timeoutMs));
  const composedSignal =
    signals.length === 0
      ? undefined
      : signals.length === 1
        ? signals[0]
        : AbortSignal.any(signals);

  let response: Response;
  try {
    response = await fetch(`${baseUrl}${path}`, {
      ...rest,
      credentials: "include",
      headers: {
        ...(json !== undefined ? { "Content-Type": "application/json" } : {}),
        ...headers,
      },
      body: json !== undefined ? JSON.stringify(json) : undefined,
      signal: composedSignal,
    });
  } catch (e) {
    // Timeout abort manifests as DOMException name=TimeoutError; caller
    // abort as name=AbortError. Either way the user wanted "did the
    // request go through?" — surface a typed ApiError so the chat hook's
    // try/catch can show a real toast instead of a generic crash.
    if (e instanceof DOMException && e.name === "TimeoutError") {
      throw new ApiError(0, {
        code: "internal_error",
        message: `Request timed out after ${timeoutMs}ms`,
      });
    }
    if (e instanceof DOMException && e.name === "AbortError") {
      throw new ApiError(0, {
        code: "internal_error",
        message: "Request aborted",
      });
    }
    // Network failure (DNS, offline, TLS) — let the chat hook describe
    // it via its catch path; just normalise the type so callers can
    // `instanceof ApiError`.
    throw new ApiError(0, {
      code: "internal_error",
      message: e instanceof Error ? e.message : "Network error",
    });
  }

  if (response.status === 204) {
    return undefined as T;
  }

  const contentType = response.headers.get("content-type") ?? "";
  const payload: unknown = contentType.includes("application/json")
    ? await response.json()
    : await response.text();

  if (!response.ok) {
    if (
      typeof payload === "object" &&
      payload !== null &&
      "error" in payload
    ) {
      throw new ApiError(response.status, (payload as ApiErrorBody).error);
    }
    throw new ApiError(response.status, {
      code: "internal_error",
      message: typeof payload === "string" ? payload : `HTTP ${response.status}`,
    });
  }

  return payload as T;
}

/**
 * POST a raw binary Blob (audio, image) and parse the JSON/`ApiError` response —
 * the same baseURL/credentials/error-envelope contract as `apiFetch`, but for a
 * Blob body `apiFetch`'s JSON-only `body` type can't express. Used by voice
 * dictation (`/api/transcribe`). Content-Type comes from the blob itself.
 */
export async function postBlob<T>(
  path: string,
  blob: Blob,
  { timeoutMs }: { timeoutMs?: number } = {},
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${baseUrl}${path}`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": blob.type || "application/octet-stream" },
      body: blob,
      signal: timeoutMs !== undefined ? AbortSignal.timeout(timeoutMs) : undefined,
    });
  } catch (e) {
    if (e instanceof DOMException && e.name === "TimeoutError") {
      throw new ApiError(0, {
        code: "internal_error",
        message: `Request timed out after ${timeoutMs}ms`,
      });
    }
    throw new ApiError(0, {
      code: "internal_error",
      message: e instanceof Error ? e.message : "Network error",
    });
  }

  const contentType = response.headers.get("content-type") ?? "";
  const payload: unknown = contentType.includes("application/json")
    ? await response.json()
    : await response.text();

  if (!response.ok) {
    if (typeof payload === "object" && payload !== null && "error" in payload) {
      throw new ApiError(response.status, (payload as ApiErrorBody).error);
    }
    throw new ApiError(response.status, {
      code: "internal_error",
      message: typeof payload === "string" ? payload : `HTTP ${response.status}`,
    });
  }

  return payload as T;
}
