import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const topBar = readFileSync(
  resolve(process.cwd(), "src/components/workspace/TopBar.tsx"),
  "utf8",
);
const brandMark = readFileSync(
  resolve(process.cwd(), "src/components/marketing/BrandMark.tsx"),
  "utf8",
);
const localeSwitcher = readFileSync(
  resolve(process.cwd(), "src/components/LocaleSwitcher.tsx"),
  "utf8",
);

describe("dark workspace top bar contrast", () => {
  it("keeps the inverse brand label readable", () => {
    expect(brandMark).toContain(
      'inverse ? "text-[#fcfbf7]" : "text-[#171716]"',
    );
  });

  it("uses the dark language switcher treatment", () => {
    expect(topBar).toContain("<LocaleSwitcher inverse />");
    expect(localeSwitcher).toContain('inverse ? "border-white/35 bg-white/[0.04]"');
    expect(localeSwitcher).toContain(
      '"text-slate-300 hover:bg-white/[0.08] hover:text-white"',
    );
  });

  it("renders a high-contrast account trigger in every state", () => {
    expect(topBar).toContain(
      'className="bg-[#f15a38] font-semibold text-white"',
    );
    expect(topBar).toContain("border-white/15 bg-white/[0.05]");
    expect(topBar).toContain("data-[state=open]:border-white/35");
    expect(topBar).toContain("text-slate-300");
  });
});
