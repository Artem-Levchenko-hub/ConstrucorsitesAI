import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * After signing in without an explicit destination the user lands in Yleum.
 * (`/max` itself sends an anonymous session to registration and an unverified
 * account to onboarding.) An explicit same-origin `next` still wins; anything
 * unsafe falls back to the default.
 */
const redirect = vi.fn((target: string) => {
  throw new Error(`redirect:${target}`);
});
vi.mock("next/navigation", () => ({ redirect }));
vi.mock("next/headers", () => ({ cookies: async () => ({ get: vi.fn(), set: vi.fn() }), headers: async () => new Headers() }));

function form(fields: Record<string, string>): FormData {
  const data = new FormData();
  for (const [key, value] of Object.entries(fields)) data.set(key, value);
  return data;
}

const credentials = { email: "owner@example.com", password: "moss river blue", confirm: "moss river blue" };

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response("{}", {
        status: 200,
        headers: { "set-cookie": "omnia_session=token; Path=/; HttpOnly" },
      }),
    ),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  redirect.mockClear();
});

describe.each(["loginAction", "registerAction"] as const)("%s landing", (name) => {
  it("defaults to Yleum", async () => {
    const actions = await import("@/app/(auth)/actions");
    await expect(actions[name]({ error: null }, form(credentials))).rejects.toThrow("redirect:/max");
  });

  it("honours an explicit same-origin destination", async () => {
    const actions = await import("@/app/(auth)/actions");
    await expect(
      actions[name]({ error: null }, form({ ...credentials, next: "/billing/plan" })),
    ).rejects.toThrow("redirect:/billing/plan");
  });

  it("ignores an off-site destination", async () => {
    const actions = await import("@/app/(auth)/actions");
    await expect(
      actions[name]({ error: null }, form({ ...credentials, next: "//evil.example/x" })),
    ).rejects.toThrow("redirect:/max");
  });
});

it.each(["x", "secret123", "alllettersnodigit", "🔐".repeat(128)])("keeps existing login passwords compatible (%#)", async password => {
  const { loginAction } = await import("@/app/(auth)/actions");
  await expect(loginAction({ error: null }, form({ ...credentials, password }))).rejects.toThrow("redirect:/max");
  expect(JSON.parse(String(vi.mocked(fetch).mock.calls[0][1]?.body)).password).toBe(password);
});

it.each(["registerAction", "maxRegisterAction"] as const)("%s rejects common passwords before transmitting them", async name => {
  const actions = await import("@/app/(auth)/actions");
  const result = await actions[name]({ error: null }, form({ ...credentials, password: "passwordpassword", confirm: "passwordpassword" }));
  expect(result.error).toContain("менее распространённый");
  expect(result.error).not.toContain("passwordpassword");
  expect(fetch).not.toHaveBeenCalled();
});
