import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import HomePage from "@/app/page";
import MaxProductPage from "@/app/max/product/page";

function render(page: ReturnType<typeof HomePage>) {
  return renderToStaticMarkup(page);
}

describe("public MAX entry", () => {
  it.each([
    ["/", HomePage],
    ["/max/product", MaxProductPage],
  ])("keeps %s focused on the authenticated MAX journey", (_route, Page) => {
    const html = render(Page());

    expect(html).toContain("data-max-studio");
    expect(html).toContain("Пример интерфейса");
    expect(html).toContain('href="/max/register"');
    expect(html).toContain('href="/login?next=/max"');
    expect(html).not.toContain("Веб-приложения");
    expect(html).not.toContain("Лендинги");
    expect(html).not.toContain('href="/max/new"');
  });
});
