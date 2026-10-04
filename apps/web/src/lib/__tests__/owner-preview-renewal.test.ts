import { describe, expect, it, vi } from "vitest";
import { installOwnerPreviewFetch } from "../../../../orchestrator/templates/max-miniapp-nextjs/src/lib/max/owner-preview-renewal";
import { installPreviewSessionRenewal } from "../max-preview-session-renewal";
const origin = "https://qa-dev.dev2.yleum.ru";
const session = () => Response.json({ mode: "preview", user: { id: "preview" } });
function child(native: ReturnType<typeof vi.fn>) {
  const listeners = new Set<(event: MessageEvent) => void>();
  const parent = { postMessage: vi.fn((message, _origin?: string) => queueMicrotask(() => listeners.forEach(fn => fn({ source: parent, origin: "https://yleum.ru", data: {
    type: "omnia:preview-session:result", nonce: message.nonce, url: `${origin}/api/omnia/preview-session?expires=1&signature=test`,
  } } as unknown as MessageEvent)))) };
  const boundary = { fetch: native, location: { href: `${origin}/`, origin }, parent, crypto,
    addEventListener: (_: string, fn: (e: MessageEvent) => void) => listeners.add(fn),
    removeEventListener: (_: string, fn: (e: MessageEvent) => void) => listeners.delete(fn), setTimeout, clearTimeout,
  } as unknown as Window;
  return { boundary, parent, listeners };
}
describe("owner preview expiry without business replay", () => {
  it("renews once before concurrent writes, preserving body, revision and operation key", async () => {
    let expired = true;
    const native = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === "/api/max/session") return expired ? new Response(null, { status: 401 }) : session();
      if (String(input).includes("preview-session?")) { expired = false; return session(); }
      return Response.json({ saved: true });
    });
    const { boundary, parent } = child(native);
    const stop = installOwnerPreviewFetch(boundary);
    const init = { method: "PATCH", body: '{"title":"draft","due_date":null}', headers: { "If-Match": '"revision-7"', "Idempotency-Key": "own-op" } };
    const result = await Promise.all([boundary.fetch("/api/tasks/one", init), boundary.fetch("/api/tasks/two", init)]);
    expect(result.map(r => r.status)).toEqual([200, 200]);
    expect(native.mock.calls.filter(c => String(c[0]).includes("preview-session?"))).toHaveLength(1);
    expect(native.mock.calls.filter(c => String(c[0]) === "/api/tasks/one")).toEqual([["/api/tasks/one", init]]);
    expect(parent.postMessage.mock.calls.filter(c => c[1] === "https://yleum.ru")).toHaveLength(1);
    stop(); expect(boundary.fetch).toBe(native);
  });
  it("never replays a PATCH whose response is 401", async () => {
    let probes = 0;
    const native = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === "/api/max/session") return ++probes === 2 ? new Response(null, { status: 401 }) : session();
      if (String(input).includes("preview-session?")) return session();
      return new Response(null, { status: 401 });
    });
    const { boundary } = child(native); const stop = installOwnerPreviewFetch(boundary);
    expect((await boundary.fetch("/api/tasks/one", { method: "PATCH", body: "draft" })).status).toBe(401);
    expect(native.mock.calls.filter(c => String(c[0]) === "/api/tasks/one")).toHaveLength(1); stop();
  });
  it("does not renew provider-policy 401 with a still valid preview actor", async () => {
    const native = vi.fn(async (input: RequestInfo | URL) => String(input) === "/api/max/session" ? session() : new Response(null, { status: 401 }));
    const { boundary, parent } = child(native); const stop = installOwnerPreviewFetch(boundary);
    await boundary.fetch("/api/omnia/integrations/catalog"); expect(parent.postMessage).not.toHaveBeenCalled(); stop();
  });
  it("blocks writes after actor change and leaves external requests untouched", async () => {
    const native = vi.fn(async () => Response.json({ user: { id: "123" } }));
    const { boundary } = child(native); const stop = installOwnerPreviewFetch(boundary);
    expect((await boundary.fetch("/api/tasks/one", { method: "PATCH" })).status).toBe(401);
    expect(native).toHaveBeenCalledTimes(1);
    await boundary.fetch("https://other.invalid/api/tasks", { method: "POST" }); expect(native).toHaveBeenCalledTimes(2); stop();
  });
  it("does not dispatch a pending write after unmount", async () => {
    let finish!: (r: Response) => void;
    const native = vi.fn(() => new Promise<Response>(resolve => { finish = resolve; }));
    const { boundary } = child(native); const stop = installOwnerPreviewFetch(boundary);
    const pending = boundary.fetch("/api/tasks/one", { method: "PATCH" }); stop(); finish(session());
    expect((await pending).status).toBe(401); expect(native).toHaveBeenCalledTimes(1);
  });
});
function editor() {
  const listeners = new Set<(event: MessageEvent) => void>();
  return { listeners, boundary: {
    addEventListener: (_: string, fn: (e: MessageEvent) => void) => listeners.add(fn),
    removeEventListener: (_: string, fn: (e: MessageEvent) => void) => listeners.delete(fn),
  } as unknown as Window };
}
describe("editor capability boundary", () => {
  it("rejects foreign sources/origins and a frame replaced while minting", async () => {
    let frame = { postMessage: vi.fn() } as unknown as Window; const original = frame;
    let resolve!: (r: { url: string }) => void;
    const mint = vi.fn(() => new Promise<{ url: string }>(r => { resolve = r; }));
    const { listeners, boundary } = editor();
    const stop = installPreviewSessionRenewal({ window: boundary, origin, frame: () => frame, mint });
    const data = { type: "omnia:preview-session:renew", nonce: "a".repeat(32) };
    for (const e of [{ source: {}, origin }, { source: original, origin: "https://evil.invalid" }]) listeners.forEach(fn => fn({ ...e, data } as MessageEvent));
    expect(mint).not.toHaveBeenCalled();
    listeners.forEach(fn => fn({ source: original, origin, data } as MessageEvent)); expect(mint).toHaveBeenCalledTimes(1);
    frame = {} as Window; resolve({ url: `${origin}/api/omnia/preview-session?signature=secret` });
    await Promise.resolve(); await Promise.resolve(); expect(original.postMessage).not.toHaveBeenCalled(); stop();
  });
  it("replies only to the current frame exact origin, without navigating it", async () => {
    const frame = { postMessage: vi.fn() } as unknown as Window;
    const { listeners, boundary } = editor(); const url = `${origin}/api/omnia/preview-session?signature=secret`;
    const stop = installPreviewSessionRenewal({ window: boundary, origin, frame: () => frame, mint: async () => ({ url }) });
    const nonce = "b".repeat(32);
    listeners.forEach(fn => fn({ source: frame, origin, data: { type: "omnia:preview-session:renew", nonce } } as MessageEvent));
    await Promise.resolve(); await Promise.resolve();
    expect(frame.postMessage).toHaveBeenCalledWith({ type: "omnia:preview-session:result", nonce, url }, origin); stop();
  });
});

describe("request lifetime and capability rejection", () => {
  it("preserves a Request body and init overrides without consuming it in preflight", async () => {
    const native = vi.fn(async (input: RequestInfo | URL) => String(input) === "/api/max/session" ? session() : Response.json({ ok: true }));
    const { boundary } = child(native); const stop = installOwnerPreviewFetch(boundary);
    const request = new Request(`${origin}/api/tasks/one`, { method: "PATCH", body: "original", headers: { "If-Match": "old" } });
    const init = { headers: { "If-Match": "new" }, body: "override" };
    await boundary.fetch(request, init);
    expect(request.bodyUsed).toBe(false);
    expect(native.mock.calls[1]).toEqual([request, init]); stop();
  });
  it("honors an abort while other callers complete their shared preflight", async () => {
    let finish!: (r: Response) => void;
    const native = vi.fn((input: RequestInfo | URL) => String(input) === "/api/max/session" ? new Promise<Response>(resolve => { finish = resolve; }) : Promise.resolve(Response.json({ ok: true })));
    const { boundary } = child(native); const stop = installOwnerPreviewFetch(boundary);
    const controller = new AbortController();
    const aborted = boundary.fetch("/api/tasks/one", { method: "PATCH", signal: controller.signal });
    const live = boundary.fetch("/api/tasks/two", { method: "PATCH" });
    controller.abort(); finish(session());
    await expect(aborted).rejects.toMatchObject({ name: "AbortError" }); expect((await live).status).toBe(200);
    expect(native.mock.calls.filter(c => String(c[0]) === "/api/tasks/one")).toHaveLength(0); stop();
  });
  it("does not dispatch with malformed or unavailable session evidence", async () => {
    for (const response of [new Response(null, { status: 503 }), Response.json({ mode: "preview", user: { id: "123" } })]) {
      const native = vi.fn(async () => response); const { boundary, parent } = child(native);
      const stop = installOwnerPreviewFetch(boundary);
      expect((await boundary.fetch("/api/tasks/one", { method: "POST" })).status).toBe(401);
      expect(native).toHaveBeenCalledTimes(1); expect(parent.postMessage).not.toHaveBeenCalled(); stop();
    }
  });
  it("rejects forged replies until the matching parent/origin/nonce reply arrives", async () => {
    let expired = true;
    const native = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === "/api/max/session") return expired ? new Response(null, { status: 401 }) : session();
      if (String(input).includes("preview-session?")) { expired = false; return session(); }
      return Response.json({ ok: true });
    });
    const { boundary, parent, listeners } = child(native);
    parent.postMessage.mockImplementation(() => undefined);
    const stop = installOwnerPreviewFetch(boundary); const pending = boundary.fetch("/api/tasks/one", { method: "PATCH" });
    await vi.waitFor(() => expect(parent.postMessage).toHaveBeenCalled());
    const nonce = parent.postMessage.mock.calls[0][0].nonce;
    const data = { type: "omnia:preview-session:result", nonce, url: `${origin}/api/omnia/preview-session?signature=ok` };
    for (const event of [{ source: {}, origin: "https://yleum.ru", data }, { source: parent, origin: "https://evil.invalid", data }, { source: parent, origin: "https://yleum.ru", data: { ...data, nonce: "wrong" } }]) listeners.forEach(fn => fn(event as unknown as MessageEvent));
    expect(native).toHaveBeenCalledTimes(1);
    listeners.forEach(fn => fn({ source: parent, origin: "https://yleum.ru", data } as unknown as MessageEvent));
    expect((await pending).status).toBe(200); stop();
  });
});

describe("configured cabinet and cancellation overrides", () => {
  it("uses only the server-confirmed configured editor origin", async () => {
    let expired = true;
    const native = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === "/api/max/session") return expired ? new Response(null, { status: 401 }) : session();
      if (String(input).includes("preview-session?")) { expired = false; return session(); }
      return Response.json({ ok: true });
    });
    const { boundary, parent, listeners } = child(native);
    parent.postMessage.mockImplementation(message => queueMicrotask(() => listeners.forEach(fn => fn({ source: parent, origin: "https://custom-cabinet.example", data: {
      type: "omnia:preview-session:result", nonce: message.nonce, url: `${origin}/api/omnia/preview-session?signature=ok`,
    } } as unknown as MessageEvent))));
    const stop = installOwnerPreviewFetch(boundary, ["https://custom-cabinet.example"]);
    expect((await boundary.fetch("/api/tasks/one", { method: "PATCH" })).status).toBe(200);
    expect(parent.postMessage.mock.calls.map(c => c[1])).toEqual(["https://custom-cabinet.example"]); stop();
  });
  it("honors an explicit null signal override on an aborted Request", async () => {
    const native = vi.fn(async (input: RequestInfo | URL) => String(input) === "/api/max/session" ? session() : Response.json({ ok: true }));
    const { boundary } = child(native); const stop = installOwnerPreviewFetch(boundary);
    const controller = new AbortController(); controller.abort();
    const request = new Request(`${origin}/api/tasks/one`, { method: "PATCH", body: "draft", signal: controller.signal });
    expect((await boundary.fetch(request, { signal: null })).status).toBe(200); stop();
  });
  it("returns the dispatched 401 even if the renewal bridge throws", async () => {
    let probes = 0;
    const native = vi.fn(async (input: RequestInfo | URL) => String(input) === "/api/max/session" ? ++probes === 1 ? session() : new Response(null, { status: 401 }) : new Response("original", { status: 401 }));
    const { boundary, parent } = child(native); parent.postMessage.mockImplementation(() => { throw new Error("bridge unavailable"); });
    const stop = installOwnerPreviewFetch(boundary);
    const result = await boundary.fetch("/api/tasks/one", { method: "PATCH" });
    expect(result.status).toBe(401); expect(await result.text()).toBe("original");
    expect(native.mock.calls.filter(c => String(c[0]) === "/api/tasks/one")).toHaveLength(1); stop();
  });
});
