import { createHash } from "node:crypto";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({ values: new Map<string, string>(), set: vi.fn() }));
vi.mock("server-only", () => ({}));
vi.mock("next/headers", () => ({
  cookies: async () => ({
    get: (name: string) => state.values.has(name) ? { name, value: state.values.get(name) } : undefined,
    getAll: () => [...state.values].map(([name, value]) => ({ name, value })),
    set: state.set,
  }),
  headers: async () => new Headers(),
}));
vi.mock("next/navigation", () => ({
  redirect: (target: string) => { throw new Error(`redirect:${target}`); },
}));

const ticket = `ticket-${"x".repeat(48)}`;
const cookieName = `__Host-yleum-oauth-ticket-${createHash("sha256").update(ticket).digest("hex")}`;
const proof = "p".repeat(43);
const pending = {
  provider: "vk", label: "VK ID", email: "owner@example.com",
  next: "/max", legal_document_version: "2026-07-30",
};

beforeEach(() => {
  vi.resetModules();
  state.values.clear();
  state.set.mockReset();
  vi.stubEnv("NEXT_PUBLIC_USE_MOCKS", "false");
  vi.stubEnv("INTERNAL_API_URL", "http://api.internal:8000");
  vi.stubEnv("PUBLIC_ORIGIN", "https://yleum.ru");
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(pending), {
    headers: { "Content-Type": "application/json" },
  })));
});
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

describe("OAuth browser binding forwarding", () => {
  it("does not ask for pending identity when this browser lacks its ticket binding", async () => {
    state.values.set("omnia_session", "unrelated-session");
    const { getOAuthPending } = await import("@/lib/oauth-login-server");
    expect(await getOAuthPending(ticket)).toBeNull();
    expect(fetch).not.toHaveBeenCalled();
  });

  it("forwards only the matching binding when reading pending identity", async () => {
    state.values.set(cookieName, proof);
    state.values.set("omnia_session", "unrelated-session");
    state.values.set("unrelated_cookie", "unrelated-value");
    const { getOAuthPending } = await import("@/lib/oauth-login-server");
    expect(await getOAuthPending(ticket)).toEqual(pending);
    const [, init] = vi.mocked(fetch).mock.calls[0];
    expect(new Headers(init?.headers).get("Cookie")).toBe(`${cookieName}=${proof}`);
    expect(init?.cache).toBe("no-store");
  });

  it("forwards the ticket binding and trusted public Origin when completing registration", async () => {
    state.values.set(cookieName, proof);
    state.values.set("omnia_session", "unrelated-session");
    vi.mocked(fetch).mockResolvedValueOnce(new Response(JSON.stringify({ id: "u1" }), {
      status: 201, headers: { "Set-Cookie": "omnia_session=new-session; Path=/; HttpOnly" },
    }));
    const form = new FormData();
    for (const [key, value] of Object.entries({ ticket, terms_accepted: "on",
      privacy_accepted: "on", personal_data_accepted: "on", document_version: "2026-07-30" })) {
      form.set(key, value);
    }
    const { oauthCompleteAction } = await import("@/app/(auth)/actions");
    await expect(oauthCompleteAction({ error: null }, form)).rejects.toThrow("redirect:/max");
    const [, init] = vi.mocked(fetch).mock.calls[0];
    const headers = new Headers(init?.headers);
    expect(headers.get("Cookie")).toBe(`${cookieName}=${proof}`);
    expect(headers.get("Origin")).toBe("https://yleum.ru");
    expect(state.set).toHaveBeenCalledWith(expect.objectContaining({
      name: cookieName, value: "", maxAge: 0, path: "/", secure: true, httpOnly: true,
    }));
  });
});
