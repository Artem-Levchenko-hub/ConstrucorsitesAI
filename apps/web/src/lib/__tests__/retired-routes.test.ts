import { describe, expect, it, vi } from "vitest";

vi.mock("next-intl/plugin", () => ({ default: () => (config: unknown) => config }));

/**
 * The site-builder editor left this app. Its addresses must keep working as
 * entrances to MAX Studio: old bookmarks, `?next=/projects` login links and the
 * API's remix redirect all still point there.
 */
describe("retired site-builder routes", () => {
  it("send visitors to the MAX Studio cabinet without caching the redirect", async () => {
    const { default: config } = await import("../../../next.config");
    const redirects = await config.redirects?.();

    expect(redirects).toEqual([
      { source: "/projects", destination: "/max", permanent: false },
      { source: "/projects/:path*", destination: "/max", permanent: false },
      { source: "/deep-research", destination: "/max", permanent: false },
    ]);
  });
});
