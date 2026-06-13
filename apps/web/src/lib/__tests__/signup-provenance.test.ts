import { describe, expect, it } from "vitest";

import {
  SIGNUP_SOURCES,
  sanitizeReferrerProjectId,
  sanitizeSignupSource,
} from "@/lib/signup-provenance";

/**
 * V4.2b RETURN-EDGE — param-plumbing leg. The register page reads `?source` and
 * `?ref` from the URL and forwards them to the api. These sanitizers are the
 * teeth on the client side: a valid source/ref flows through (provenance is
 * recorded), anything malformed is dropped to `null` (signup proceeds organic,
 * NEVER a 422). Each assert below flips in exactly one direction — money-free,
 * 0 LLM, pure logic.
 */

const VALID_UUID = "3f9a1c7e-2b4d-4e6f-8a1b-9c0d1e2f3a4b";

describe("sanitizeSignupSource", () => {
  it("passes every backend-accepted source verbatim", () => {
    for (const src of SIGNUP_SOURCES) {
      expect(sanitizeSignupSource(src)).toBe(src);
    }
  });

  it("drops a value outside the closed enum to null (no 422-risk)", () => {
    expect(sanitizeSignupSource("evil")).toBeNull();
    expect(sanitizeSignupSource("Share_Link")).toBeNull(); // case-sensitive enum
    expect(sanitizeSignupSource("")).toBeNull();
  });

  it("drops non-string input to null (organic fallback)", () => {
    expect(sanitizeSignupSource(undefined)).toBeNull();
    expect(sanitizeSignupSource(null)).toBeNull();
    expect(sanitizeSignupSource(42)).toBeNull();
  });
});

describe("sanitizeReferrerProjectId", () => {
  it("passes a well-formed UUID verbatim", () => {
    expect(sanitizeReferrerProjectId(VALID_UUID)).toBe(VALID_UUID);
  });

  it("accepts uppercase UUIDs (case-insensitive)", () => {
    expect(sanitizeReferrerProjectId(VALID_UUID.toUpperCase())).toBe(
      VALID_UUID.toUpperCase(),
    );
  });

  it("drops anything that is not a canonical UUID to null", () => {
    expect(sanitizeReferrerProjectId("not-a-uuid")).toBeNull();
    expect(sanitizeReferrerProjectId("3f9a1c7e2b4d4e6f8a1b9c0d1e2f3a4b")).toBeNull(); // no dashes
    expect(sanitizeReferrerProjectId(`{${VALID_UUID}}`)).toBeNull(); // braces form
    expect(sanitizeReferrerProjectId("")).toBeNull();
  });

  it("drops non-string input to null", () => {
    expect(sanitizeReferrerProjectId(undefined)).toBeNull();
    expect(sanitizeReferrerProjectId(null)).toBeNull();
  });
});
