import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

import { MaxPublicLanding } from "@/components/marketing/MaxPublicLanding";

function source(path: string): string {
  return readFileSync(resolve(process.cwd(), path), "utf8");
}

const chatMessage = source("src/components/workspace/ChatMessage.tsx");
const heroMediaPanel = source("src/components/workspace/HeroMediaPanel.tsx");
const button = source("src/components/ui/button.tsx");

describe("semantic foreground contracts", () => {
  it("keeps Studio user messages and journey markers readable on blue", () => {
    expect(chatMessage).toContain('studio && isUser && "bg-accent-subtle text-accent"');
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

  it("keeps the landing-page application example readable in the light palette", () => {
    const landing = renderToStaticMarkup(MaxPublicLanding());

    expect(landing).toContain("data-max-studio");
    expect(landing).toContain("Пример интерфейса");
    expect(landing).toContain("max-public-button--primary");
    expect(landing).not.toContain("data-graphite-shell");
  });
});
