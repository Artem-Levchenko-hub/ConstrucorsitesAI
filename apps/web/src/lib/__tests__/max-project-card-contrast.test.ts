import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const source = readFileSync(
  resolve(process.cwd(), "src/components/max/MaxStudioProjectCard.tsx"),
  "utf8",
);

describe("MAX project card contrast", () => {
  it("keeps the management action readable before and during hover", () => {
    expect(source).toContain("text-white");
    expect(source).toContain("hover:bg-[#4f81f7]");
    expect(source).toContain("hover:text-[#121519]");
  });
});
