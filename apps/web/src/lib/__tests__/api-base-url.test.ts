import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * The web image carries no domain. In the browser the API base is EMPTY unless a
 * split-origin setup configures it, so every request is same-origin and one image
 * runs under any domain. On the server an empty base is useless (Node cannot
 * fetch a relative URL), so there an empty value must fall through to the next
 * candidate. Both rules hinge on `||` instead of `??`: a Dockerfile `ENV X=$X`
 * with no build arg defines X as "", and Next inlines exactly that.
 */
const redirect = vi.fn((target: string) => {
  throw new Error(`redirect:${target}`);
});
vi.mock("next/navigation", () => ({ redirect }));
vi.mock("next/headers", () => ({
  cookies: async () => ({ get: () => ({ value: "session-token" }), set: vi.fn() }),
}));

import { apiFetch, apiUrl, postBlob } from "@/lib/api/client";
import { downloadProjectFiles } from "@/lib/api/projects";
import { versionImageUrl } from "@/lib/project-version";

const fetchMock = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>();
const requestedUrl = () => fetchMock.mock.calls.at(-1)?.[0];

beforeEach(() => {
  fetchMock.mockImplementation(async () => new Response(null, { status: 404 }));
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  fetchMock.mockReset();
  redirect.mockClear();
});

describe("browser API base", () => {
  it.each<readonly [string, string | undefined, string]>([
    ["unset means same origin", undefined, "/api/x"],
    ["an EMPTY value means same origin too", "", "/api/x"],
    ["a lone slash means same origin", "/", "/api/x"],
    ["an absolute value is used verbatim", "https://api.example", "https://api.example/api/x"],
    ["a port survives", "http://localhost:8000", "http://localhost:8000/api/x"],
    ["a trailing slash never doubles up", "https://api.example/", "https://api.example/api/x"],
    ["several trailing slashes neither", "https://api.example///", "https://api.example/api/x"],
  ])("%s", (_case, configured, expected) => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", configured);
    expect(apiUrl("/api/x")).toBe(expected);
  });

  it("is read per call, not captured when the module loads", () => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", "https://one.example");
    expect(apiUrl("/api/x")).toBe("https://one.example/api/x");
    vi.stubEnv("NEXT_PUBLIC_API_URL", undefined);
    expect(apiUrl("/api/x")).toBe("/api/x");
  });

  describe.each<readonly [string, string | undefined, string]>([
    ["same origin", undefined, ""],
    ["a split API origin", "https://api.example", "https://api.example"],
  ])("with %s every browser request shares the one base", (_case, configured, base) => {
    beforeEach(() => vi.stubEnv("NEXT_PUBLIC_API_URL", configured));

    it("apiFetch", async () => {
      await apiFetch("/api/projects").catch(() => undefined);
      expect(requestedUrl()).toBe(`${base}/api/projects`);
    });
    it("postBlob", async () => {
      await postBlob("/api/transcribe", new Blob(["a"], { type: "audio/webm" })).catch(() => undefined);
      expect(requestedUrl()).toBe(`${base}/api/transcribe`);
    });
    it("downloadProjectFiles, which used to read the variable on its own", async () => {
      await expect(downloadProjectFiles("p1")).rejects.toThrow("Пока нечего скачивать");
      expect(requestedUrl()).toBe(`${base}/api/projects/p1/download`);
    });
    it("versionImageUrl", () => {
      expect(versionImageUrl("/api/projects/p1/snapshots/s/previews/0")).toBe(
        `${base}/api/projects/p1/snapshots/s/previews/0`,
      );
      expect(versionImageUrl("/rebuilt.png")).toBe("/rebuilt.png");
    });
  });
});

describe("server API base", () => {
  const callers: Record<string, () => Promise<unknown>> = {
    "lib/api/server serverApiFetchResult": async () =>
      (await import("@/lib/api/server")).serverApiFetchResult("/api/probe"),
    "lib/auth-mock getSession": async () => (await import("@/lib/auth-mock")).getSession(),
    "(auth)/actions loginAction": async () => {
      const form = new FormData();
      form.set("email", "owner@example.com");
      form.set("password", "secret123");
      return (await import("@/app/(auth)/actions")).loginAction({ error: null }, form);
    },
  };
  const paths: Record<string, string> = {
    "lib/api/server serverApiFetchResult": "/api/probe",
    "lib/auth-mock getSession": "/api/auth/me",
    "(auth)/actions loginAction": "/api/auth/login",
  };

  describe.each(Object.keys(callers))("%s", (name) => {
    // getSession answers with a demo user, without any request, unless mocks are off.
    beforeEach(() => vi.stubEnv("NEXT_PUBLIC_USE_MOCKS", "false"));

    it.each<readonly [string, string | undefined, string | undefined, string]>([
      ["prefers the internal network address", "http://api:8000", "https://public.example", "http://api:8000"],
      ["falls back to the public address", undefined, "https://public.example", "https://public.example"],
      ["skips an EMPTY internal address", "", "https://public.example", "https://public.example"],
      ["skips an EMPTY public address too", "", "", "http://localhost:8000"],
      ["keeps the local-dev default when nothing is set", undefined, undefined, "http://localhost:8000"],
    ])("%s", async (_case, internal, publicUrl, base) => {
      vi.stubEnv("INTERNAL_API_URL", internal);
      vi.stubEnv("NEXT_PUBLIC_API_URL", publicUrl);
      await callers[name]().catch(() => undefined);
      expect(requestedUrl()).toBe(`${base}${paths[name]}`);
    });
  });
});
