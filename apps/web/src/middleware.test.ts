import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { NextRequest } from "next/server";

import { middleware } from "./middleware";

const originalMocksValue = process.env.NEXT_PUBLIC_USE_MOCKS;

function request(path: string, withSession = false) {
  return new NextRequest(`https://constructor.lead-generator.ru${path}`, {
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

  it("keeps login reachable when an expired session cookie is present", () => {
    const response = middleware(request("/login?next=/max", true));

    expect(response.headers.get("location")).toBeNull();
    expect(response.headers.get("x-middleware-next")).toBe("1");
  });

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
    const response = middleware(request("/projects?filter=recent"));

    expect(response.status).toBe(307);
    expect(response.headers.get("location")).toBe(
      "https://constructor.lead-generator.ru/login?next=%2Fprojects%3Ffilter%3Drecent",
    );
  });
});
