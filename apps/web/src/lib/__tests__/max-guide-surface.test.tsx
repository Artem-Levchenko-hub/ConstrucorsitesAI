import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import MaxGuidePage from "@/app/max/guide/page";

import {
  BuilderVisual,
  DashboardVisual,
  GuideScreenshot,
  GoldenPathVisual,
  LaunchVisual,
  PartnerVisual,
  ProjectCreationVisual,
} from "@/components/max/guide/GuideVisuals";
import { guideScreenshots } from "@/components/max/guide/guide-screenshots";

vi.mock("@/lib/auth-mock", () => ({ getSession: async () => null }));

const chapterIds = [
  "start", "account", "project", "builder", "settings", "integrations",
  "max-bot", "publish", "partner", "acceptance", "operations", "staff", "billing", "errors", "checklists",
];

function renderScreenshot(id: string) {
  return new DOMParser().parseFromString(
    renderToStaticMarkup(<GuideScreenshot id={id} />),
    "text/html",
  );
}

describe("MAX guide screenshots", () => {
  it("connects the visual launch journey to real chapters in the order a new owner needs them", async () => {
    const page = new DOMParser().parseFromString(renderToStaticMarkup(await MaxGuidePage()), "text/html");
    const journey = new DOMParser().parseFromString(renderToStaticMarkup(<GoldenPathVisual />), "text/html");
    const links = [...journey.querySelectorAll('nav[aria-label="Путь от проекта до запуска"] a')];

    expect(links.map(link => link.getAttribute("href"))).toEqual([
      "#project", "#builder", "#settings", "#max-bot", "#publish", "#acceptance",
    ]);
    links.forEach(link => {
      expect(link.textContent?.trim().length).toBeGreaterThan(10);
      expect(page.querySelector(link.getAttribute("href")!)).not.toBeNull();
    });
    expect(page.querySelector('[data-learning="guide-journey"]')).not.toBeNull();
  });

  it("gives every real screenshot an accessible title and its dated source", () => {
    for (const [id, screenshot] of Object.entries(guideScreenshots)) {
      const page = renderScreenshot(id);
      const figure = page.querySelector("figure")!;
      const title = page.getElementById(figure.getAttribute("aria-labelledby") ?? "");
      const source = page.getElementById(figure.getAttribute("aria-describedby") ?? "");
      expect(title?.textContent).toBe(screenshot.title);
      expect(source?.querySelector("time")?.getAttribute("datetime")).toBe(screenshot.capturedAt);
      expect(source?.textContent).toContain("Реальные данные скрыты");
    }
  });

  it("renders all linked chapters with existing captures and current MAX field names", async () => {
    const document = new DOMParser().parseFromString(renderToStaticMarkup(await MaxGuidePage()), "text/html");

    for (const anchor of chapterIds) {
      expect(document.getElementById(anchor)).not.toBeNull();
    }
    const pictures = [...document.querySelectorAll<HTMLElement>("[data-guide-screenshot]")];
    expect(new Set(pictures.map((picture) => picture.dataset.guideScreenshot))).toEqual(new Set(Object.keys(guideScreenshots)));
    expect(document.getElementById("partner")?.textContent).toContain("Введите ссылку");
    expect(document.getElementById("settings")?.textContent).toContain("обновляются без генерации после доставки данных");
    expect(document.getElementById("settings")?.textContent).not.toContain("«Данные» → «Контент»");
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

  it("provides a screenshot dialog trigger with the original image as a fallback", () => {
    for (const [id, screenshot] of Object.entries(guideScreenshots)) {
      const document = renderScreenshot(id);
      const caption = document.querySelector("figcaption");
      expect(caption?.textContent).toContain(screenshot.title);

      const links = Array.from(document.querySelectorAll("figure a"));
      const original = links.find((link) => link.getAttribute("href") === screenshot.src);
      expect(original?.textContent).toContain("Открыть в полном размере");
      expect(original?.getAttribute("aria-label")).toContain(screenshot.title);
      expect(original?.getAttribute("aria-haspopup")).toBe("dialog");

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

describe("MAX guide navigation", () => {
  it("offers every written chapter in both tables of contents with a keyboard-accessible desktop list", async () => {
    const page = new DOMParser().parseFromString(renderToStaticMarkup(await MaxGuidePage()), "text/html");
    const desktop = page.querySelector('nav[aria-label="Оглавление"] [tabindex="0"]');
    const mobile = page.querySelector('details nav[aria-label="Главы руководства"]');
    for (const contents of [desktop, mobile]) {
      const links = Array.from(contents?.querySelectorAll("a") ?? []);
      expect(links.map(link => link.getAttribute("href"))).toEqual(chapterIds.map(id => `#${id}`));
      expect(links.at(-1)?.textContent).toBe("13Списки для запуска");
    }
  });

  it("directs old CRM chapter links to the current integration instructions", async () => {
    const page = new DOMParser().parseFromString(renderToStaticMarkup(await MaxGuidePage()), "text/html");
    expect(page.body.textContent).not.toMatch(/amoCRM|\bCRM\b/i);
    expect(page.querySelector('[data-guide-screenshot="integrations"]')).toBeNull();
    expect(page.querySelector('a[href="#amocrm"]')).toBeNull();
    for (const anchor of ["amocrm", "video-amocrm"]) {
      expect(page.getElementById(anchor)?.closest("section")?.id).toBe("integrations");
    }
  });

  it("keeps legacy chapter links usable after removing the video lessons", async () => {
    const page = new DOMParser().parseFromString(renderToStaticMarkup(await MaxGuidePage()), "text/html");
    expect(page.querySelector("video, a[href^='#video-']")).toBeNull();
    expect(page.body.textContent).not.toMatch(/Видеоуроки|Смотреть видео/);
    expect(page.getElementById("video-lessons")?.querySelector('nav[aria-label="Оглавление"]')).toBeTruthy();

    for (const id of chapterIds) {
      expect(page.getElementById(`video-${id}`)?.closest("section")?.id).toBe(id);
    }
  });
});
