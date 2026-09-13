import type { SecureRecordStore } from "./store";

type Actor = { id: string } | null;
const MAX_BODY = 262_144;
const COLLECTION = /^[a-z][a-z0-9_-]{0,63}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function json(value: unknown, status = 200): Response {
  return Response.json(value, { status, headers: { "Cache-Control": "no-store" } });
}

class RequestFailure extends Error {
  status: number;
  constructor(status: number) { super("Invalid secure data request"); this.status = status; }
}

async function body(request: Request): Promise<Record<string, unknown>> {
  if (request.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase() !== "application/json") {
    throw new RequestFailure(415);
  }
  const reader = request.body?.getReader();
  if (!reader) throw new RequestFailure(400);
  let length = 0;
  const chunks: Uint8Array[] = [];
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      length += value.byteLength;
      if (length > MAX_BODY) {
        await reader.cancel();
        throw new RequestFailure(413);
      }
      chunks.push(value);
    }
  } finally { reader.releaseLock(); }
  try {
    const bytes = new Uint8Array(length);
    let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
    const value: unknown = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
    if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error();
    return value as Record<string, unknown>;
  } catch { throw new RequestFailure(400); }
}

/** Caller supplies trusted MAX authentication; never accepts an actor from the request. */
export function createSecureDataHandler(
  authenticate: () => Promise<Actor>,
  openStore: () => SecureRecordStore | Promise<SecureRecordStore>,
  options: { trustedGateway?: boolean } = {},
) {
  return async (request: Request, path: string[]): Promise<Response> => {
    try {
      const actor = await authenticate();
      if (!actor?.id) return json({ error: "UNAUTHORIZED" }, 401);
      const [collection, id] = path;
      if (path.length < 1 || path.length > 2 || !COLLECTION.test(collection) || (id && !UUID.test(id))) {
        return json({ error: "INVALID_INPUT" }, 400);
      }
      const method = request.method;
      if (!(id ? ["GET", "PUT", "DELETE"] : ["GET", "POST"]).includes(method)) {
        return json({ error: "METHOD_NOT_ALLOWED" }, 405);
      }
      if (method !== "GET") {
        const origin = request.headers.get("origin");
        if (request.headers.get("sec-fetch-site") === "cross-site") return json({ error: "FORBIDDEN" }, 403);
        if (options.trustedGateway) {
          // The gateway strips caller X-Omnia-* and supplies the external origin.
          // Its upstream Host is the internal container address, not the browser origin.
          const expected = request.headers.get("x-omnia-request-origin");
          if (!expected?.startsWith("https://") || !origin || origin !== expected) {
            return json({ error: "FORBIDDEN" }, 403);
          }
        } else if (origin) {
          let originHost = "";
          try { originHost = new URL(origin).host; } catch { /* Reject opaque origins. */ }
          const host = request.headers.get("host") || new URL(request.url).host;
          if (!originHost || originHost !== host) return json({ error: "FORBIDDEN" }, 403);
        }
      }
      let input: Record<string, unknown> = {};
      if (method !== "GET") {
        input = await body(request);
        const allowed = method === "POST" ? ["payload"] : method === "PUT" ? ["payload", "revision"] : ["revision"];
        if (Object.keys(input).some(key => !allowed.includes(key))) throw new RequestFailure(400);
        if (method !== "DELETE" && (!input.payload || typeof input.payload !== "object" || Array.isArray(input.payload))) {
          throw new RequestFailure(400);
        }
        if (method !== "POST" && (!Number.isSafeInteger(input.revision) || Number(input.revision) < 1)) {
          throw new RequestFailure(400);
        }
      }
      const query = new URL(request.url).searchParams;
      if ([...query.keys()].some(key => !["limit", "cursor"].includes(key)) ||
          query.getAll("limit").length > 1 || query.getAll("cursor").length > 1) throw new RequestFailure(400);
      const limit = query.has("limit") ? Number(query.get("limit")) : 50;
      if (!Number.isSafeInteger(limit) || limit < 1 || limit > 100 || (query.get("cursor")?.length ?? 0) > 512) {
        throw new RequestFailure(400);
      }
      let store: SecureRecordStore;
      try { store = await openStore(); } catch { return json({ error: "KEY_UNAVAILABLE" }, 503); }
      if (method === "GET") {
        return json(id ? { record: await store.get(actor.id, collection, id) }
          : await store.list(actor.id, collection, { limit, cursor: query.get("cursor") ?? undefined }));
      }
      if (method === "POST") {
        return json({ record: await store.create(actor.id, collection, input.payload as Parameters<SecureRecordStore["create"]>[2]) }, 201);
      }
      if (method === "PUT") {
        return json({ record: await store.update(actor.id, collection, id, input.payload as Parameters<SecureRecordStore["update"]>[3], Number(input.revision)) });
      }
      await store.delete(actor.id, collection, id, Number(input.revision));
      return new Response(null, { status: 204, headers: { "Cache-Control": "no-store" } });
    } catch (error) {
      if (error instanceof RequestFailure) return json({ error: "INVALID_INPUT" }, error.status);
      const code = error && typeof error === "object" && "code" in error ? String(error.code) : "";
      const status: Record<string, number> = { INVALID_INPUT: 400, NOT_FOUND: 404, REVISION_CONFLICT: 409, KEY_UNAVAILABLE: 503 };
      return json({ error: status[code] ? code : "DATA_UNAVAILABLE" }, status[code] || 503);
    }
  };
}
