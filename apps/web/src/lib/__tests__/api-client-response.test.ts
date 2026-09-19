import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, apiFetch, postBlob } from "@/lib/api/client";

/**
 * Characterisation of how the two request helpers read a response. Frozen BEFORE
 * `apiFetch` and `postBlob` shared one reader, unchanged AFTER: JSON vs text by
 * content type, the `{error}` envelope, the generic fallback, the 204 quirk.
 */
const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
const text = (status: number, body: string) =>
  new Response(body, { status, headers: { "content-type": "text/plain" } });

const callers = {
  apiFetch: () => apiFetch<unknown>("/api/x"),
  postBlob: () => postBlob<unknown>("/api/x", new Blob(["a"], { type: "audio/webm" })),
};

function respondWith(response: Response) {
  const fetchMock = vi.fn().mockResolvedValue(response);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

async function failure(run: () => Promise<unknown>): Promise<ApiError> {
  try {
    await run();
  } catch (error) {
    expect(error).toBeInstanceOf(ApiError);
    return error as ApiError;
  }
  throw new Error("expected the request to fail");
}

afterEach(() => vi.unstubAllGlobals());

describe.each(Object.entries(callers))("%s response reading", (_name, run) => {
  it("returns parsed JSON for a JSON success", async () => {
    respondWith(json(200, { id: "p1", nested: { ok: true } }));
    await expect(run()).resolves.toEqual({ id: "p1", nested: { ok: true } });
  });

  it("returns the raw text for a non-JSON success", async () => {
    respondWith(text(200, "plain body"));
    await expect(run()).resolves.toBe("plain body");
  });

  it("throws the API error envelope with code, status and details", async () => {
    respondWith(
      json(409, { error: { code: "conflict", message: "slug already exists", details: { a: 1 } } }),
    );
    const error = await failure(run);
    expect([error.status, error.code, error.message, error.details]).toEqual([
      409,
      "conflict",
      "slug already exists",
      { a: 1 },
    ]);
  });

  it("falls back to internal_error with the text body", async () => {
    respondWith(text(502, "Bad Gateway"));
    const error = await failure(run);
    expect([error.status, error.code, error.message]).toEqual([502, "internal_error", "Bad Gateway"]);
  });

  it("falls back to the status line when a JSON error has no envelope", async () => {
    respondWith(json(500, { detail: "boom" }));
    const error = await failure(run);
    expect([error.status, error.code, error.message]).toEqual([500, "internal_error", "HTTP 500"]);
  });

  it("reads a body without a content type as text", async () => {
    respondWith(new Response("no type", { status: 200 }));
    await expect(run()).resolves.toBe("no type");
  });

  it("returns an {error}-shaped body of a 2xx as data", async () => {
    respondWith(json(200, { error: { code: "conflict", message: "not an error here" } }));
    await expect(run()).resolves.toEqual({
      error: { code: "conflict", message: "not an error here" },
    });
  });

  it("uses a JSON string error body as the message, and HTTP <status> for null", async () => {
    respondWith(json(500, "upstream said no"));
    expect((await failure(run)).message).toBe("upstream said no");
    respondWith(json(503, null));
    const error = await failure(run);
    expect([error.status, error.code, error.message]).toEqual([503, "internal_error", "HTTP 503"]);
  });

  it("lets a malformed JSON body surface as a SyntaxError, not an ApiError", async () => {
    respondWith(
      new Response("<html>gateway</html>", {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    await expect(run()).rejects.toBeInstanceOf(SyntaxError);
  });

  it("maps a timeout and a network failure to ApiError status 0", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new DOMException("late", "TimeoutError")));
    const timeout = await failure(run);
    expect([timeout.status, timeout.code]).toEqual([0, "internal_error"]);
    expect(timeout.message).toContain("timed out");

    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    const offline = await failure(run);
    expect([offline.status, offline.message]).toEqual([0, "Failed to fetch"]);
  });
});

describe("what differs between the two helpers", () => {
  it("apiFetch treats 204 as undefined and sends JSON with credentials", async () => {
    const fetchMock = respondWith(new Response(null, { status: 204 }));
    await expect(apiFetch("/api/x", { method: "DELETE", json: { a: 1 } })).resolves.toBeUndefined();
    const [, init] = fetchMock.mock.calls[0];
    expect(init.credentials).toBe("include");
    expect(init.body).toBe('{"a":1}');
    expect(new Headers(init.headers).get("content-type")).toBe("application/json");
  });

  it("apiFetch reports a caller abort as 'Request aborted'", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new DOMException("stop", "AbortError")));
    const error = await failure(() => apiFetch("/api/x"));
    expect([error.status, error.message]).toEqual([0, "Request aborted"]);
  });

  it("postBlob has no 204 shortcut: an empty 204 reads as an empty string", async () => {
    respondWith(new Response(null, { status: 204 }));
    await expect(postBlob("/api/x", new Blob(["a"]))).resolves.toBe("");
  });

  it("postBlob posts the blob with its own content type", async () => {
    const fetchMock = respondWith(json(200, { text: "привет" }));
    const blob = new Blob(["a"], { type: "audio/webm" });
    await expect(postBlob("/api/transcribe", blob)).resolves.toEqual({ text: "привет" });
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toMatch(/\/api\/transcribe$/);
    expect([init.method, init.credentials]).toEqual(["POST", "include"]);
    expect(init.body).toBe(blob);
    expect(new Headers(init.headers).get("content-type")).toBe("audio/webm");
  });
});
