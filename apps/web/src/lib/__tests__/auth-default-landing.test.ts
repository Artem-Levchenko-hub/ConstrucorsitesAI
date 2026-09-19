import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * After signing in without an explicit destination the user lands in MAX Studio.
 * (`/max` itself sends an anonymous session to registration and an unverified
 * account to onboarding.) An explicit same-origin `next` still wins; anything
 * unsafe falls back to the default.
 */
const redirect = vi.fn((target: string) => {
  throw new Error(`redirect:${target}`);
});
vi.mock("next/navigation", () => ({ redirect }));
vi.mock("next/headers", () => ({ cookies: async () => ({ set: vi.fn() }) }));

function form(fields: Record<string, string>): FormData {
  const data = new FormData();
  for (const [key, value] of Object.entries(fields)) data.set(key, value);
  return data;
}

const credentials = { email: "owner@example.com", password: "secret123", confirm: "secret123" };

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
  it("defaults to MAX Studio", async () => {
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
