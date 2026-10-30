import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import MaxGuidePage from "@/app/max/guide/page";

import {
  BuilderVisual,
  DashboardVisual,
  GuideScreenshot,
  IntegrationVisual,
  LaunchVisual,
  PartnerVisual,
  ProjectCreationVisual,
} from "@/components/max/guide/GuideVisuals";
import { guideScreenshots } from "@/components/max/guide/guide-screenshots";

function renderScreenshot(id: string) {
  return new DOMParser().parseFromString(
    renderToStaticMarkup(<GuideScreenshot id={id} />),
    "text/html",
  );
}

describe("MAX guide screenshots", () => {
  it("renders all linked chapters with existing captures and current MAX field names", () => {
    const document = new DOMParser().parseFromString(renderToStaticMarkup(<MaxGuidePage />), "text/html");
    const anchors = ["start", "account", "project", "builder", "settings", "integrations", "amocrm", "max-bot", "publish", "partner", "acceptance", "operations", "billing", "errors", "checklists"];

    for (const anchor of anchors) {
      expect(document.getElementById(anchor)).not.toBeNull();
    }
    const pictures = [...document.querySelectorAll<HTMLElement>("[data-guide-screenshot]")];
    expect(new Set(pictures.map((picture) => picture.dataset.guideScreenshot))).toEqual(new Set(Object.keys(guideScreenshots)));
    expect(document.getElementById("partner")?.textContent).toContain("Введите ссылку");
    expect(document.getElementById("settings")?.textContent).toContain("не нужен запуск ИИ");
    expect(document.body.textContent).not.toContain("Шаг 6 из 6");
    expect(document.body.textContent).not.toContain("Расширенные настройки → Настроить");
  });

  it("publishes only flattened redacted PNG files rather than raw captures or fabricated controls", () => {
    expect(Object.keys(guideScreenshots).length).toBeGreaterThan(0);
    const publicRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../../../public");
    expect(existsSync(resolve(publicRoot, "guide/2026-10-30"))).toBe(false);

    for (const [id, screenshot] of Object.entries(guideScreenshots)) {
      expect(screenshot.src).toMatch(/^\/guide\/2026-10-30-redacted\/[^/]+\.png$/);
      const imagePath = resolve(publicRoot, screenshot.src.slice(1));
      const imageBytes = readFileSync(imagePath);
      expect(imageBytes.length).toBeGreaterThan(1_000);
      expect([...imageBytes.subarray(0, 8)]).toEqual([137, 80, 78, 71, 13, 10, 26, 10]);
      expect(imageBytes.readUInt32BE(16)).toBe(screenshot.width);
      expect(imageBytes.readUInt32BE(20)).toBe(screenshot.height);

      const document = renderScreenshot(id);
      const image = document.querySelector("figure img");
      expect(image?.getAttribute("src")).toBe(screenshot.src);
      expect(image?.getAttribute("width")).toBe(String(screenshot.width));
      expect(image?.getAttribute("height")).toBe(String(screenshot.height));
      expect(image?.getAttribute("alt")).toBe(screenshot.alt);
      expect(document.querySelector("figcaption")?.textContent).toContain("Реальные данные скрыты");
      expect(document.querySelectorAll('button, input, select, textarea, [role="button"]')).toHaveLength(0);
    }
  });

  it("keeps numbered annotations in the screenshot coordinate system", () => {
    for (const [id, screenshot] of Object.entries(guideScreenshots)) {
      const document = renderScreenshot(id);
      const overlay = document.querySelector("figure svg");
      expect(overlay?.getAttribute("viewBox")).toBe(`0 0 ${screenshot.width} ${screenshot.height}`);
      expect(overlay?.getAttribute("preserveAspectRatio")).toBe("xMidYMid meet");
      expect(overlay?.getAttribute("aria-hidden")).toBe("true");

      const annotations = Array.from(document.querySelectorAll("figure svg > g"));
      const captions = Array.from(document.querySelectorAll("figcaption ol > li"));
      expect(annotations).toHaveLength(screenshot.callouts.length);
      expect(captions).toHaveLength(screenshot.callouts.length);

      screenshot.callouts.forEach((callout, index) => {
        const annotation = annotations[index];
        const badge = annotation.querySelector("circle");
        expect(Number(badge?.getAttribute("cx")) / screenshot.width).toBeCloseTo(callout.x / 100);
        expect(Number(badge?.getAttribute("cy")) / screenshot.height).toBeCloseTo(callout.y / 100);
        expect(annotation.querySelector("text")?.textContent).toBe(String(index + 1));
        expect(annotation.querySelector("path")?.getAttribute("d")).toContain(
          `L ${(callout.targetX / 100) * screenshot.width} ${(callout.targetY / 100) * screenshot.height}`,
        );
        expect(captions[index].textContent).toContain(callout.text);
      });
    }
  });

  it("provides a full-size original and dated source for every capture", () => {
    for (const [id, screenshot] of Object.entries(guideScreenshots)) {
      const document = renderScreenshot(id);
      const caption = document.querySelector("figcaption");
      expect(caption?.textContent).toContain(screenshot.title);

      const links = Array.from(document.querySelectorAll("figure a"));
      const original = links.find((link) => link.getAttribute("href") === screenshot.src);
      expect(original?.textContent).toContain("Открыть в полном размере");
      expect(original?.getAttribute("aria-label")).toContain(screenshot.title);

      const source = links.find((link) => link.getAttribute("href") === screenshot.sourceUrl);
      expect(source?.textContent).toBe(screenshot.sourceLabel);
      expect(document.querySelector("time")?.getAttribute("datetime")).toBe(screenshot.capturedAt);

      for (const link of [original, source]) {
        expect(link?.getAttribute("target")).toBe("_blank");
        expect(link?.getAttribute("rel")).toContain("noopener");
      }
    }
  });

  it.each(["missing-capture", "", "__proto__", "constructor"])(
    "rejects an unknown screenshot ID %j without substituting a picture",
    (id) => {
      expect(() => renderScreenshot(id)).toThrow(`Unknown guide screenshot: ${id}`);
    },
  );

  it("keeps existing visual exports connected to the corresponding real capture", () => {
    const visuals = [
      [ProjectCreationVisual, "create"],
      [BuilderVisual, "builder"],
      [IntegrationVisual, "integrations"],
      [LaunchVisual, "publish"],
      [PartnerVisual, "max-settings"],
      [DashboardVisual, "management"],
    ] as const;

    for (const [Visual, id] of visuals) {
      const document = new DOMParser().parseFromString(renderToStaticMarkup(<Visual />), "text/html");
      expect(document.querySelector("figure img")?.getAttribute("src")).toBe(guideScreenshots[id].src);
      expect(document.querySelectorAll("button, input, select, textarea")).toHaveLength(0);
    }
  });
});
