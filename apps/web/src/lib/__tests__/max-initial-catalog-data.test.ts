import { describe, expect, it } from "vitest";
import { newMaxContentItem } from "@/lib/max-catalog";
import { initialCatalogError, publicInitialCatalog, publicMaxBriefText } from "@/lib/max-initial-catalog";
import { buildMaxProjectPrompt, type MaxProjectBrief } from "@/lib/max-brief";

const brief: MaxProjectBrief = { name: "Кофе", idea: "Латте 300 ₽", appType: "catalog", audience: "", primaryAction: "", features: [], style: "clean", brandColors: "" };

describe("reviewed initial catalog contract", () => {
  it("retains content identity and exact public data without mutating the draft", () => {
    const item = { ...newMaxContentItem(), title: " Латте ", price: " 300 ₽ ", category: "Кофе", description: "С молоком", options: ["250 мл"] };
    expect(publicInitialCatalog([item])[0]).toEqual({ ...item, title: "Латте", price: "300 ₽" });
    expect(item.title).toBe(" Латте ");
  });
  it("redacts credentials consistently from both the brief and public item fields", () => {
    const token = "sk-" + "abcdef123456".repeat(5);
    const input = `API key: ${token}`;
    expect(publicMaxBriefText(input)).not.toContain(token);
    const item = { ...newMaxContentItem(), title: input, price: "300 ₽", description: input, options: [input] };
    expect(JSON.stringify(publicInitialCatalog([item]))).not.toContain(token);
  });
  it("accepts an empty catalog and an intentionally unpriced named item", () => {
    expect(initialCatalogError([])).toBeNull();
    expect(initialCatalogError([{ ...newMaxContentItem(), title: "Консультация" }])).toBeNull();
  });
  it("does not silently remove blank, too long, duplicated or overflowing records", () => {
    const item = { ...newMaxContentItem(), title: "Кофе" };
    expect(initialCatalogError([newMaxContentItem()])).not.toBeNull();
    expect(initialCatalogError([{ ...item, title: "я".repeat(121) }])).not.toBeNull();
    expect(initialCatalogError([{ ...item, price: "1".repeat(81) }])).not.toBeNull();
    expect(initialCatalogError([item, item])).not.toBeNull();
    expect(initialCatalogError([item], true)).not.toBeNull();
  });
  it("tells generation to use the stored catalog, including an explicit empty decision", () => {
    expect(buildMaxProjectPrompt({ ...brief, catalogItemCount: 2 })).toContain("2 позиций уже сохранены в Yleum");
    expect(buildMaxProjectPrompt({ ...brief, catalogItemCount: 0 })).toContain("Не добавляй позиции из описания или примеров");
    expect(() => buildMaxProjectPrompt({ ...brief, catalogItemCount: 101 })).toThrow();
  });
});
