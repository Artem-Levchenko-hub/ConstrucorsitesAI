import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const source = readFileSync(
  resolve(process.cwd(), "src/components/max/guide/GuideVisuals.tsx"),
  "utf8",
);

const partnerVisual = source.slice(
  source.indexOf("export function PartnerVisual"),
  source.indexOf("export function DashboardVisual"),
);

describe("MAX guide visuals", () => {
  it("keeps the MAX Partner walkthrough readable in the dark product theme", () => {
    expect(partnerVisual).toContain('bg-[#121519] text-white');
    expect(partnerVisual).not.toMatch(/bg-\[#(?:f4f5f7|f7f8fa|eef0f3)\]/i);
    expect(partnerVisual).not.toContain('text-[#15171a]');
  });
});
