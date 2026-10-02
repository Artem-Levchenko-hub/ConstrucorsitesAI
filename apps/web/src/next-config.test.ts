import { describe, expect, it } from "vitest";
import nextConfig from "../next.config";

describe("platform web response policy", () => {
  it("prevents foreign framing without restricting Studio preview children or Next scripts", async () => {
    const rules = await nextConfig.headers!();
    const rule = rules.find(({ source }) => source === "/:path*");
    expect(rule, "all platform web responses need the framing policy").toBeDefined();
    const headers = Object.fromEntries(rule!.headers.map(({ key, value }) => [key.toLowerCase(), value]));
    expect(headers["x-frame-options"]).toBe("SAMEORIGIN");
    expect(headers["content-security-policy"]).toBe("frame-ancestors 'self'; object-src 'none'; base-uri 'self'");
    expect(headers["content-security-policy"]).not.toMatch(/(?:^|;)\s*(?:default-src|script-src|frame-src)\b/);
    // HTTPS transport and type policy belong to the platform edge; no API or
    // generated-app framing policy is installed by those shared basic headers.
    expect(headers["strict-transport-security"]).toBeUndefined();
  });

  it("keeps the versioned-font immutable cache policy", async () => {
    const rules = await nextConfig.headers!();
    const fonts = rules.find(({ source }) => source === "/fonts/:file*");
    expect(fonts?.headers).toContainEqual({ key: "Cache-Control", value: "public, max-age=31536000, immutable" });
  });
});
