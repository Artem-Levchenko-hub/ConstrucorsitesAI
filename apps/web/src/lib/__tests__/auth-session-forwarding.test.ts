import { afterEach, expect, it, vi } from "vitest";

const cookieGet = vi.fn();
const cookieSet = vi.fn();
vi.mock("next/headers", () => ({
  cookies: async () => ({ get: cookieGet, set: cookieSet }),
  headers: async () => new Headers({
    "user-agent": "Browser under test",
    "x-real-ip": "192.0.2.10",
    "x-forwarded-for": "198.51.100.99, 192.0.2.10",
  }),
}));
vi.mock("next/navigation", () => ({ redirect: (url: string) => { throw new Error(`redirect:${url}`); } }));

afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks(); });

it.each(["existing-signed-session", undefined])("forwards only the existing platform session on relogin (%s)", async (token) => {
  cookieGet.mockImplementation((name: string) => name === "omnia_session" && token ? { value: token } : undefined);
  const fetcher = vi.fn().mockResolvedValue(new Response("{}", {
    headers: { "set-cookie": "omnia_session=renewed-session; Path=/; HttpOnly" },
  }));
  vi.stubGlobal("fetch", fetcher);
  const data = new FormData();
  data.set("email", "owner@example.com");
  data.set("password", "secret123");
  const { loginAction } = await import("@/app/(auth)/actions");
  await expect(loginAction({ error: null }, data)).rejects.toThrow("redirect:/max");
  const headers = new Headers(fetcher.mock.calls[0][1].headers);
  expect(headers.get("cookie")).toBe(token ? `omnia_session=${token}` : null);
  expect(headers.get("authorization")).toBeNull();
  expect(headers.get("user-agent")).toBe("Browser under test");
  expect(headers.get("x-forwarded-for")).toBe("192.0.2.10");
  expect(cookieSet).toHaveBeenCalledWith(expect.objectContaining({ value: "renewed-session" }));
});
