import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { LaunchVisual, PartnerVisual } from "@/components/max/guide/GuideVisuals";

describe("MAX guide visuals", () => {
  it("labels walkthrough screenshots as illustrative examples", () => {
    const html = renderToStaticMarkup(<PartnerVisual />);

    expect(html).toContain("Пример интерфейса");
    expect(html).toContain("MAX для партнёров");
  });

  it("keeps all six launch stages visible", () => {
    const html = renderToStaticMarkup(<LaunchVisual />);

    expect(html).toContain("Безопасный вход MAX");
    expect(html).toContain("Шаг 5 из 6");
  });

  it("shows the URL-first MAX Partner path without an early token step", () => {
    const html = renderToStaticMarkup(<PartnerVisual />);

    expect(html).toContain("Токен не требуется");
    expect(html).not.toContain("Секрет бота");
  });
});
