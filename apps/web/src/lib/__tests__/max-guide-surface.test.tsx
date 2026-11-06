import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import MaxGuidePage from "@/app/max/guide/page";

import {
  BuilderVisual,
  DashboardVisual,
  GuideScreenshot,
  GoldenPathVisual,
  IntegrationVisual,
  LaunchVisual,
  PartnerVisual,
  ProjectCreationVisual,
} from "@/components/max/guide/GuideVisuals";
import { guideScreenshots } from "@/components/max/guide/guide-screenshots";

vi.mock("@/lib/auth-mock", () => ({ getSession: async () => null }));

const chapterIds = [
  "start", "account", "project", "builder", "settings", "integrations", "amocrm",
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

describe("MAX guide video lessons", () => {
  it("links every chapter to a compact video player with its dated media and Russian captions", async () => {
    const page = new DOMParser().parseFromString(renderToStaticMarkup(await MaxGuidePage()), "text/html");
    expect(page.querySelector('a[href="#video-lessons"]')?.textContent).toBe("Видеоуроки");
    const lessonLinks = page.querySelectorAll("#video-lessons nav a");
    expect(Array.from(lessonLinks, link => link.getAttribute("href"))).toEqual(chapterIds.map(id => `#video-${id}`));

    for (const id of chapterIds) {
      const player = page.querySelector(`#${id} details#video-${id}`);
      expect(player?.querySelector("summary")?.textContent).toBe("Смотреть видео");
      expect(player?.hasAttribute("open")).toBe(false);
      const video = player?.querySelector("video");
      const mediaPath = `/guide/lessons/2026-11-06/${id}`;
      expect(video?.getAttribute("preload")).toBe("none");
      expect(video?.getAttribute("poster")).toBe(`${mediaPath}.jpg`);
      expect(video?.hasAttribute("controls")).toBe(true);
      expect(video?.hasAttribute("playsinline")).toBe(true);
      expect(video?.hasAttribute("autoplay")).toBe(false);
      expect(video?.querySelector('source[type="video/mp4"]')?.getAttribute("src")).toBe(`${mediaPath}.mp4`);
      expect(video?.querySelector('track[kind="captions"][srclang="ru"][default]')?.getAttribute("src")).toBe(`${mediaPath}.vtt`);
    }
  });

  it("opens a direct link to the requested lesson and pauses its video when closed", async () => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    const previousUrl = location.href;
    history.replaceState(null, "", "/max/guide#video-builder");
    const container = document.createElement("div");
    document.body.append(container);
    const root = createRoot(container);
    try {
      const page = await MaxGuidePage();
      await act(async () => root.render(page));
      const lesson = container.querySelector<HTMLDetailsElement>("#video-builder");
      expect(lesson?.open).toBe(true);
      expect(container.querySelector<HTMLDetailsElement>("#video-project")?.open).toBe(false);

      const pause = vi.spyOn(lesson!.querySelector("video")!, "pause").mockImplementation(() => {});
      await act(async () => {
        lesson!.open = false;
        lesson!.dispatchEvent(new Event("toggle"));
      });
      expect(pause).toHaveBeenCalled();
    } finally {
      await act(async () => root.unmount());
      container.remove();
      history.replaceState(null, "", previousUrl);
      vi.unstubAllGlobals();
    }
  });
});
