import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

function source(path: string): string {
  return readFileSync(resolve(process.cwd(), path), "utf8");
}

const chatMessage = source("src/components/workspace/ChatMessage.tsx");
const heroMediaPanel = source("src/components/workspace/HeroMediaPanel.tsx");
const button = source("src/components/ui/button.tsx");
const landing = source("src/app/page.tsx");

describe("semantic foreground contracts", () => {
  it("keeps Studio user messages and journey markers readable on blue", () => {
    expect(chatMessage).toContain('studio ? "text-[#121519]" : "text-accent"');
    expect(chatMessage).toContain("text-inherit");
    expect(chatMessage).not.toContain("text-white/80 underline-offset-2");
    expect(chatMessage).not.toContain("text-inherit opacity-80");
    expect(heroMediaPanel).toContain("border-accent bg-accent text-accent-fg");
    expect(heroMediaPanel).not.toContain("text-on-accent");
  });

  it("uses a dedicated light foreground for destructive buttons", () => {
    expect(button).toContain('danger: "bg-danger text-fg-on-danger');
    expect(button).toContain('destructive: "bg-danger text-fg-on-danger');
  });

  it("keeps the landing-page application mock readable", () => {
    expect(landing).toContain('rounded-[16px] bg-[#1c1e23] p-5 text-white');
    expect(landing).toContain('bg-[#4f81f7] px-4 py-2 text-[11px] font-semibold text-[#121519]');
  });
});
