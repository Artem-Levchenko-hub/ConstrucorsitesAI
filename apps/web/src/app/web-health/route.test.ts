import { afterEach, describe, expect, it } from "vitest";

import { GET } from "./route";

const originalReleaseSha = process.env.OMNIA_RELEASE_SHA;

afterEach(() => {
  if (originalReleaseSha === undefined) {
    delete process.env.OMNIA_RELEASE_SHA;
  } else {
    process.env.OMNIA_RELEASE_SHA = originalReleaseSha;
  }
});

describe("web health", () => {
  it("exposes the runtime release identity", async () => {
    process.env.OMNIA_RELEASE_SHA = "a7c4fc22";

    const response = GET();

    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toEqual({
      status: "ok",
      service: "web",
      release_sha: "a7c4fc22",
    });
  });

  it.each(["A7C4FC22", "abc123", "a7c4fc22\nSECRET=x"])(
    "never reflects unsafe release value %j",
    async (unsafe) => {
      process.env.OMNIA_RELEASE_SHA = unsafe;

      const response = GET();

      await expect(response.json()).resolves.toMatchObject({
        release_sha: "unknown",
      });
    },
  );
});
