import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { NextRequest } from "next/server";

import { config, middleware } from "./middleware";

const originalMocksValue = process.env.NEXT_PUBLIC_USE_MOCKS;

function request(path: string, withSession = false, method = "GET") {
  return new NextRequest(`https://constructor.lead-generator.ru${path}`, {
    method,
    headers: withSession
      ? { cookie: "omnia_session=expired-or-invalid-token" }
      : undefined,
  });
}

describe("auth middleware", () => {
  beforeEach(() => {
    process.env.NEXT_PUBLIC_USE_MOCKS = "false";
  });

  afterEach(() => {
    if (originalMocksValue === undefined) {
      delete process.env.NEXT_PUBLIC_USE_MOCKS;
    } else {
      process.env.NEXT_PUBLIC_USE_MOCKS = originalMocksValue;
    }
  });

  it("matches the signed-out home destination used by logout redirects", () => {
    expect(config.matcher).toContain("/");
  });

  it.each([undefined, "omnia_session="])(
    "clears only cache on an anonymous home GET with cookie %s",
    (cookie) => {
      const req = new NextRequest("https://constructor.lead-generator.ru/?_rsc=logout", {
        headers: { rsc: "1", ...(cookie === undefined ? {} : { cookie }) },
      });
      const response = middleware(req);

      expect(response.headers.get("clear-site-data")).toBe('"cache"');
      expect(response.headers.get("cache-control")).toBe("no-store");
      expect(response.headers.get("set-cookie")).toBeNull();
      expect(response.headers.get("location")).toBeNull();
      expect(response.headers.get("x-middleware-request-x-omnia-return-to")).toBeNull();
    },
  );

  it.each([true, false])("preserves a home response for session-present=%s POST", (withSession) => {
    const response = middleware(request("/", withSession, "POST"));

    expect(response.headers.get("clear-site-data")).toBeNull();
    expect(response.headers.get("cache-control")).toBeNull();
    expect(response.headers.get("x-middleware-request-x-omnia-return-to")).toBeNull();
  });

  it("preserves an authenticated home GET", () => {
    const response = middleware(request("/", true));

    expect(response.headers.get("clear-site-data")).toBeNull();
    expect(response.headers.get("cache-control")).toBeNull();
    expect(response.headers.get("x-middleware-request-x-omnia-return-to")).toBeNull();
  });

  it("keeps login reachable when an expired session cookie is present", () => {
    const response = middleware(request("/login?next=/max", true));

    expect(response.headers.get("location")).toBeNull();
    expect(response.headers.get("x-middleware-next")).toBe("1");
  });

  it.each(["GET", "POST"])("clears only old browser cache on login %s", (method) => {
    const req = request("/login?next=/account", true, method);
    const response = middleware(req);

    expect(response.headers.get("clear-site-data")).toBe('"cache"');
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(response.headers.get("set-cookie")).toBeNull();
    expect(response.headers.get("location")).toBeNull();
    expect(response.headers.get("x-middleware-request-x-omnia-return-to")).toBe(
      "/login?next=/account",
    );
    expect(req.cookies.get("omnia_session")?.value).toBe("expired-or-invalid-token");
  });

  it.each(["/register", "/max/start", "/account"])(
    "keeps other auth and account response cache policies unchanged for %s",
    (path) => {
      const response = middleware(request(path, true));

      expect(response.headers.get("clear-site-data")).toBeNull();
      expect(response.headers.get("cache-control")).toBeNull();
      expect(response.headers.get("set-cookie")).toBeNull();
    },
  );

  it("keeps register reachable when an expired session cookie is present", () => {
    const response = middleware(request("/register", true));

    expect(response.headers.get("location")).toBeNull();
    expect(response.headers.get("x-middleware-next")).toBe("1");
  });

  it("keeps the MAX quick start public without a session", () => {
    const response = middleware(request("/max/start"));

    expect(response.headers.get("location")).toBeNull();
    expect(response.headers.get("x-middleware-next")).toBe("1");
  });

  it("still sends unauthenticated protected routes to login", () => {
    const response = middleware(request("/billing/transactions?filter=recent"));

    expect(response.status).toBe(307);
    expect(response.headers.get("location")).toBe(
      "https://constructor.lead-generator.ru/login?next=%2Fbilling%2Ftransactions%3Ffilter%3Drecent",
    );
  });
});
