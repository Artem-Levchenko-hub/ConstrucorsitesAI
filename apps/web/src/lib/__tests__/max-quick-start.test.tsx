import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import MaxGuidePage from "@/app/max/guide/page";
import MaxQuickStartPage from "@/app/max/start/page";

describe("MAX quick-start navigation", () => {
  it("offers a distinct, descriptive instruction link for every step and reaches an existing guide chapter", () => {
    const page = new DOMParser().parseFromString(renderToStaticMarkup(<MaxQuickStartPage />), "text/html");
    const guide = new DOMParser().parseFromString(renderToStaticMarkup(<MaxGuidePage />), "text/html");
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
