import { describe, expect, it } from "vitest";

import {
  createMaxDemoDraft,
  parseMaxDemoDraft,
} from "@/lib/max-demo";

describe("MAX public demo draft", () => {
  it("turns one restaurant description into a project-ready brief", () => {
    const draft = createMaxDemoDraft(
      "Кофейня «Смена»: меню, заказ навынос и бонусы",
      "2026-08-08T08:00:00.000Z",
    );

    expect(draft).toMatchObject({
      version: 1,
      createdAt: "2026-08-08T08:00:00.000Z",
      industry: "restaurant",
      industryLabel: "Кафе и ресторан",
      brief: {
        name: "Смена",
        appType: "catalog",
        primaryAction: "выбрать позиции и оформить заказ",
      },
    });
    expect(draft.brief.features).toContain("Уведомления бота");
    expect(draft.preview.items).toHaveLength(3);
  });

  it("recognises booking, learning and fallback service scenarios", () => {
    expect(createMaxDemoDraft("Салон красоты с записью к мастеру").industry).toBe(
      "beauty",
    );
    expect(createMaxDemoDraft("Школа английского с уроками и заданиями").industry).toBe(
      "education",
    );
    expect(createMaxDemoDraft("Юридические консультации для бизнеса").industry).toBe(
      "services",
    );
  });

  it("normalises text and rejects unusably short descriptions", () => {
    expect(
      createMaxDemoDraft("  Магазин    одежды с каталогом и заказом  ").description,
    ).toBe("Магазин одежды с каталогом и заказом");
    expect(() => createMaxDemoDraft("Кафе")).toThrow(
      "Опишите задачу хотя бы десятью символами",
    );
  });

  it("restores only a complete versioned draft", () => {
    const draft = createMaxDemoDraft("Фитнес-клуб с баллами и расписанием");
    expect(parseMaxDemoDraft(JSON.stringify(draft))).toEqual(draft);
    expect(parseMaxDemoDraft("not-json")).toBeNull();
    expect(parseMaxDemoDraft("x".repeat(50_001))).toBeNull();
    expect(parseMaxDemoDraft(JSON.stringify({ ...draft, version: 2 }))).toBeNull();
    expect(parseMaxDemoDraft(JSON.stringify({ version: 1 }))).toBeNull();
    expect(
      parseMaxDemoDraft(
        JSON.stringify({
          ...draft,
          brief: { ...draft.brief, appType: "unknown" },
        }),
      ),
    ).toBeNull();
    expect(
      parseMaxDemoDraft(
        JSON.stringify({
          ...draft,
          brief: { ...draft.brief, audience: { injected: true } },
        }),
      ),
    ).toBeNull();
    expect(
      parseMaxDemoDraft(
        JSON.stringify({
          ...draft,
          preview: { ...draft.preview, items: [{ title: null }] },
        }),
      ),
    ).toBeNull();
  });
});
