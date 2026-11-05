import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import MaxGuidePage from "@/app/max/guide/page";
import MaxQuickStartPage from "@/app/max/start/page";

vi.mock("@/lib/auth-mock", () => ({ getSession: async () => null }));

describe("MAX quick-start navigation", () => {
  it("offers a distinct, descriptive instruction link for every step and reaches an existing guide chapter", async () => {
    const page = new DOMParser().parseFromString(renderToStaticMarkup(await MaxQuickStartPage()), "text/html");
    const guide = new DOMParser().parseFromString(renderToStaticMarkup(await MaxGuidePage()), "text/html");
    const cards = [...page.querySelectorAll("ol > li")];
    const destinations = ["project", "builder", "settings", "max-bot", "publish", "acceptance"];

    expect(cards).toHaveLength(6);
    cards.forEach((card, index) => {
      const title = card.querySelector("h2")!.textContent;
      const link = card.querySelector<HTMLAnchorElement>("a")!;
      expect(link.textContent).toContain("Подробная инструкция");
      expect(link.getAttribute("aria-label")).toBe(`Подробная инструкция: ${title}`);
      expect(link.getAttribute("href")).toBe(`/max/guide#${destinations[index]}`);
      expect(guide.getElementById(destinations[index])).not.toBeNull();
      expect(link.querySelector("svg")?.getAttribute("aria-hidden")).toBe("true");
    });
  });
});
