import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { YleumLanding } from "@/components/marketing/YleumLanding";

function landing() {
  const host = document.createElement("div");
  host.innerHTML = renderToStaticMarkup(<YleumLanding />);
  return host;
}

describe("landing explanations and examples", () => {
  it("places a readable function description above each of the five screens", () => {
    const section = landing().querySelector("#examples")!;
    const items = section.querySelectorAll(".ys-collage-item");
    expect(items).toHaveLength(5);
    for (const item of items) {
      const description = item.querySelector(".ys-collage-description");
      const phone = item.querySelector(".ys-phone-frame")!;
      expect(description?.textContent?.length).toBeGreaterThan(20);
      expect(description!.compareDocumentPosition(phone) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
      expect(description?.closest('[aria-hidden="true"]')).toBeNull();
    }
    expect(section.querySelector(".yl-usecases")).toBeNull();
  });

  it("does not advertise unavailable external integrations", () => {
    const host = landing();
    for (const name of ["amoCRM", "Битрикс24", "МойСклад", "YCLIENTS", "r_keeper", "iiko", "СДЭК", "Яндекс Метрика"]) {
      expect(host.textContent).not.toContain(name);
    }
    expect(host.querySelector('[data-marketing-section="proof"]')).toBeNull();
  });

  it("shows an expanded checkout summary after the edit with consistent menu data", () => {
    const figures = landing().querySelectorAll(".yl-diff > figure");
    const [before, after] = Array.from(figures);
    expect(before.querySelector(".ys-checkout")).toBeNull();
    expect(after.querySelector(".ys-checkout")?.textContent).toMatch(/К оплате.*430/s);
    expect(after.querySelector(".ys-checkout")?.textContent).toContain("Оформить заказ");
    for (const screen of [before, after]) {
      expect(screen.textContent).toContain("9:56");
      expect(screen.textContent).not.toContain("8:40");
      expect(Array.from(screen.querySelectorAll(".ys-tile > strong")).map(el => el.textContent)).toContain("Круассан с миндалём");
      expect(screen.textContent).toContain("1 порция");
    }
  });
});
