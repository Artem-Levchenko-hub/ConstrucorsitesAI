import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import robots, { dynamic as robotsDynamic } from "@/app/robots";
import sitemap, { dynamic as sitemapDynamic } from "@/app/sitemap";
import { publicOrigin } from "@/lib/public-origin";

/**
 * The public domain belongs to the running container, not to the image. The
 * helper therefore reads a NON-public variable (a NEXT_PUBLIC_* one would be
 * inlined at build time and frozen) at CALL time, and never throws: a missing
 * or malformed value degrades to a loopback origin instead of taking every page
 * down through `new URL("")`.
 */
beforeEach(() => {
  // The helper warns once per distinct problem; keep it out of the test output.
  vi.spyOn(console, "warn").mockImplementation(() => {});
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

const FALLBACK = "http://localhost:3000";

describe("publicOrigin", () => {
  it.each<readonly [string, string | undefined, string | undefined, string]>([
    ["uses PUBLIC_ORIGIN", "https://studio.example", undefined, "https://studio.example"],
    ["keeps a non-default port", "https://studio.example:8443", undefined, "https://studio.example:8443"],
    ["accepts plain http", "http://studio.example", undefined, "http://studio.example"],
    ["strips a trailing slash", "https://studio.example/", undefined, "https://studio.example"],
    ["strips a path, query and hash", "https://studio.example/app?a=1#b", undefined, "https://studio.example"],
    ["trims surrounding whitespace", "  https://studio.example  ", undefined, "https://studio.example"],
    ["prefers PUBLIC_ORIGIN over the legacy name", "https://studio.example", "https://api.example", "https://studio.example"],
    ["falls back to the legacy name so an old environment keeps working", undefined, "https://api.example", "https://api.example"],
    ["ignores an EMPTY value", "", "https://api.example", "https://api.example"],
    ["ignores a blank value", "   ", "https://api.example", "https://api.example"],
    ["ignores a relative value", "/app", undefined, FALLBACK],
    ["ignores a bare host with no scheme", "studio.example", undefined, FALLBACK],
    ["ignores a non-http scheme", "ftp://studio.example", undefined, FALLBACK],
    ["ignores a javascript: url", "javascript:alert(1)", undefined, FALLBACK],
    ["ignores outright junk", "not a url", undefined, FALLBACK],
    ["falls back when nothing is set", undefined, undefined, FALLBACK],
    ["falls back past an invalid legacy value too", undefined, "not a url", FALLBACK],
  ])("%s", (_case, origin, legacy, expected) => {
    vi.stubEnv("PUBLIC_ORIGIN", origin);
    vi.stubEnv("NEXT_PUBLIC_API_URL", legacy);
    expect(publicOrigin()).toBe(expected);
  });

  it("never throws, whatever the value", () => {
    for (const value of ["", "   ", "://", "http://", "https://[", "not a url"]) {
      vi.stubEnv("PUBLIC_ORIGIN", value);
      expect(() => publicOrigin()).not.toThrow();
    }
  });

  it("honours a value change between calls — the origin is read per request, not captured at import", () => {
    vi.stubEnv("PUBLIC_ORIGIN", "https://one.example");
    expect(publicOrigin()).toBe("https://one.example");
    vi.stubEnv("PUBLIC_ORIGIN", "https://two.example");
    expect(publicOrigin()).toBe("https://two.example");
    vi.stubEnv("PUBLIC_ORIGIN", undefined);
    expect(publicOrigin()).toBe(FALLBACK);
  });
});

describe("sitemap.xml and robots.txt follow the running deployment", () => {
  it("both are dynamic so a build never freezes one domain into the image", () => {
    expect(sitemapDynamic).toBe("force-dynamic");
    expect(robotsDynamic).toBe("force-dynamic");
  });

  it.each(["https://studio.example", "https://white-label.example:8443"])(
    "every sitemap URL sits under %s",
    (origin) => {
      vi.stubEnv("PUBLIC_ORIGIN", origin);
      const entries = sitemap();

      expect(entries.length).toBeGreaterThan(0);
      for (const entry of entries) {
        expect(entry.url.startsWith(`${origin}/`)).toBe(true);
        expect(entry.url).not.toContain("lead-generator.ru");
        // A doubled slash would come from an origin that kept its trailing slash.
        expect(entry.url.slice(origin.length)).not.toContain("//");
      }
      expect(entries.map((entry) => entry.url)).toEqual(
        expect.arrayContaining([`${origin}/`, `${origin}/pricing`, `${origin}/max/register`]),
      );
    },
  );

  it.each(["https://studio.example", "https://white-label.example:8443"])(
    "robots.txt points at %s",
    (origin) => {
      vi.stubEnv("PUBLIC_ORIGIN", origin);
      expect(robots()).toMatchObject({
        sitemap: `${origin}/sitemap.xml`,
        host: origin,
      });
    },
  );

  it("both degrade to the loopback origin instead of throwing when nothing is configured", () => {
    vi.stubEnv("PUBLIC_ORIGIN", undefined);
    vi.stubEnv("NEXT_PUBLIC_API_URL", undefined);
    expect(sitemap()[0].url).toBe(`${FALLBACK}/`);
    expect(robots().host).toBe(FALLBACK);
  });

  it("reflects a domain change between requests without a rebuild", () => {
    vi.stubEnv("PUBLIC_ORIGIN", "https://first.example");
    expect(robots().host).toBe("https://first.example");
    expect(sitemap()[0].url).toBe("https://first.example/");

    vi.stubEnv("PUBLIC_ORIGIN", "https://second.example");
    expect(robots().host).toBe("https://second.example");
    expect(sitemap()[0].url).toBe("https://second.example/");
  });
});
