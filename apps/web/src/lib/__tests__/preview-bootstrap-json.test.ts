import { createHmac } from "node:crypto";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import ts from "typescript";
import { NextResponse } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";
const file = resolve(process.cwd(), "../orchestrator/templates/max-miniapp-nextjs/src/app/api/omnia/preview-session/route.ts");
const compiled = ts.transpileModule(readFileSync(file, "utf8"), { compilerOptions: {
  module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
} }).outputText;
function route(currentUser: { id: string } | null = null) {
  const insert = vi.fn(() => ({ values: () => ({ onConflictDoNothing: () => Promise.resolve() }) }));
  const exports = {} as { GET: (request: Request) => Promise<NextResponse> };
  const imports = {
    "node:crypto": { createHmac, timingSafeEqual: (a: Buffer, b: Buffer) => a.equals(b) },
    "next/server": { NextResponse },
    "@/lib/db": { schema: { maxUsers: { maxUserId: "id" } }, withMaxUser: (_: string, fn: (tx: unknown) => unknown) => fn({ insert }) },
    "@/lib/max/session": { MAX_SESSION_COOKIE: "session", getMaxUser: async () => currentUser, createMaxSession: (_: unknown, opts: { maxAge: number }) => ({ value: "signed-cookie", maxAge: opts.maxAge }) },
  };
  new Function("exports", "require", compiled)(exports, (id: keyof typeof imports) => imports[id]);
  return { ...exports, insert };
}
function request(accept = "application/json", project = "own", expiry = Math.floor(Date.now() / 1000) + 60) {
  const signature = createHmac("sha256", "secret").update(`omnia:max-preview-session:v1\n${project}\n${expiry}`, "utf8").digest("base64url");
  return new Request(`https://cell.dev2.yleum.ru/api/omnia/preview-session?expires=${expiry}&signature=${signature}`, { headers: { Accept: accept } });
}
afterEach(() => vi.unstubAllEnvs());
describe("signed preview bootstrap JSON without navigation", () => {
  function env() { vi.stubEnv("NODE_ENV", "development"); vi.stubEnv("AUTH_SECRET", "secret"); vi.stubEnv("OMNIA_PROJECT_ID", "own"); }
  it("uses the same 900s secure cookie and leaves ordinary navigation as 307", async () => {
    env(); const { GET } = route();
    const json = await GET(request());
    expect(json.status).toBe(200); expect(await json.json()).toEqual({ user: { id: "preview" }, mode: "preview" });
    expect(json.headers.get("location")).toBeNull(); expect(json.headers.get("cache-control")).toBe("no-store");
    const cookie = json.headers.get("set-cookie")!;
    for (const flag of ["Max-Age=900", "HttpOnly", "Secure", "SameSite=none", "Partitioned", "Path=/"]) expect(cookie).toContain(flag);
    const navigation = await GET(request("text/html"));
    expect(navigation.status).toBe(307); expect(navigation.headers.get("location")).toBe("/");
  });
  it("does not bypass project, expiry, signature or public-environment guards", async () => {
    env(); const { GET, insert } = route();
    for (const input of [request("application/json", "other"), request("application/json", "own", 1), request("application/json", "own", Math.floor(Date.now()/1000)+121)]) expect((await GET(input)).status).toBe(404);
    vi.stubEnv("NODE_ENV", "production"); expect((await GET(request())).status).toBe(404);
    expect(insert).not.toHaveBeenCalled();
  });
  it("does not overwrite a real actor that appeared during renewal", async () => {
    env(); const { GET, insert } = route({ id: "123" });
    const response = await GET(request());
    expect(response.status).toBe(401); expect(response.headers.get("set-cookie")).toBeNull(); expect(insert).not.toHaveBeenCalled();
    expect((await GET(request("text/html"))).status).toBe(307);
  });

});
