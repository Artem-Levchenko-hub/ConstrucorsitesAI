import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { config, middleware } from "./middleware";

beforeEach(() => {
  vi.stubEnv("NEXT_PUBLIC_USE_MOCKS", "false");
  vi.stubEnv("PUBLIC_ORIGIN", "https://yleum.ru");
});
afterEach(() => vi.unstubAllEnvs());

it.each(["/login", "/register", "/max/register", "/oauth/complete"])(
  "moves browser auth navigation %s to the configured canonical host", (path) => {
    const response = middleware(new NextRequest(`https://www.yleum.ru${path}?next=%2Fmax`));
    expect(response.headers.get("location")).toBe(`https://yleum.ru${path}?next=%2Fmax`);
    expect(response.headers.get("cache-control")).toBe("no-store");
  },
);

it("matches the OAuth consent page as well as auth entry pages", () => {
  expect(config.matcher).toContain("/oauth/complete");
});

it("keeps a canonical-host request on that host despite forwarded host input", () => {
  const response = middleware(new NextRequest("https://yleum.ru/login", {
    headers: { "x-forwarded-host": "attacker.example", "x-forwarded-proto": "http" },
  }));
  expect(response.headers.get("location")).toBeNull();
});

it.each(["/login", "/register", "/max/register", "/oauth/complete"])(
  "serves canonical %s behind the actual standalone internal URL", (path) => {
    const response = middleware(new NextRequest(`https://0.0.0.0:3000${path}`, {
      headers: { host: "yleum.ru", "x-forwarded-proto": "https", "x-forwarded-host": "attacker.example" },
    }));
    expect(response.headers.get("location")).toBeNull();
  },
);

it("redirects an alias Host even when Next exposes only the internal URL", () => {
  const response = middleware(new NextRequest("https://0.0.0.0:3000/login?next=%2Fmax", {
    headers: { host: "www.yleum.ru", "x-forwarded-proto": "https" },
  }));
  expect(response.headers.get("location")).toBe("https://yleum.ru/login?next=%2Fmax");
});

it.each(["/max/product", "/max/guide", "/"])("keeps unrelated public navigation %s", (path) => {
  expect(middleware(new NextRequest(`https://www.yleum.ru${path}`)).headers.get("location")).toBeNull();
});

it("does not transfer an in-flight action body to another host", () => {
  const response = middleware(new NextRequest("https://www.yleum.ru/login", { method: "POST" }));
  expect(response.headers.get("location")).toBeNull();
});
