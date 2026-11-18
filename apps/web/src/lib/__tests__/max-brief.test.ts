import { describe, expect, it } from "vitest";

import * as maxBrief from "@/lib/max-brief";
import { type MaxAppTypeId } from "@/lib/max-brief";

const { buildMaxProjectPrompt, defaultManagementModules, newProjectManagementModules } = maxBrief;

describe("buildMaxProjectPrompt", () => {
  it("locks a guided project to the MAX mini-app platform", () => {
    const prompt = buildMaxProjectPrompt({
      name: "Кофе рядом",
      idea: "Карта лояльности с заказом напитка",
      appType: "loyalty",
      audience: "Гости сети кофеен",
      primaryAction: "Получить награду за баллы",
      features: ["Профиль пользователя", "Баллы и награды"],
      style: "brand",
      brandColors: "фиолетовый и молочный",
    });

    expect(prompt).toContain("именно для мессенджера MAX");
    expect(prompt).toContain("Не превращай его в обычный сайт");
    expect(prompt).toContain("Сценарий: Лояльность");
    expect(prompt).toContain("Баллы и награды");
    expect(prompt).toContain("MAX Bridge");
    expect(prompt).toContain("Не добавляй отдельную регистрацию");
  });

  it("adds useful defaults when optional fields are omitted", () => {
    const prompt = buildMaxProjectPrompt({
      name: "Помощник",
      idea: "Сервис быстрых консультаций",
      appType: "custom",
      audience: "",
      primaryAction: "",
      features: [],
      style: "clean",
      brandColors: "",
    });

    expect(prompt).toContain("определи по описанию продукта");
    expect(prompt).toContain("определи главное действие пользователя");
    expect(prompt).toContain("подбери уместную палитру");
  });

  it.each([
    "Один экран каталога без корзины, истории, профиля и вкладок.",
    "Магазин с корзиной, оплатой, заказами и профилем.",
  ])("keeps the owner's journeys instead of imposing navigation: %s", idea => {
    const prompt = buildMaxProjectPrompt({
      name: "Свой продукт", idea: `${idea} Шрифт Lora.`, appType: "custom", audience: "", primaryAction: "",
      features: [], style: "brand", brandColors: "#7c3aed",
    });

    expect(prompt).toContain(idea);
    expect(prompt).toContain("Шрифт Lora.");
    expect(prompt).toContain("Цвета бренда: #7c3aed");
    expect(prompt).toContain("Экраны и навигацию добавляй по запросу владельца");
    expect(prompt).not.toContain("мобильную навигацию, все основные экраны");
    expect(prompt).toContain(".omnia/management.json описывает доступные SDK-возможности, а не обязательные экраны или функции");
    expect(prompt).toContain("серверную проверку initData");
  });

  it.each([undefined, "brand", "clean", "bright"] as const)("uses free design wishes instead of the legacy style value: %s", style => {
    const brief = {
      name: "Мой помощник", idea: "Один экран. Заголовки Lora, текст Manrope, без карточек.",
      appType: "custom" as const, audience: "", primaryAction: "", features: [],
      brandColors: "Белый фон, красные кнопки #b71c1c",
    };
    const prompt = buildMaxProjectPrompt({ ...brief, style });
    expect(prompt).toBe(buildMaxProjectPrompt(brief));
    expect(prompt).toContain(brief.idea);
    expect(prompt).toContain(`Цвета бренда: ${brief.brandColors}`);
    expect(prompt).not.toContain("Визуальное направление:");
  });
});

const longBrief = {
  name: "Склад", idea: "я".repeat(19_990) + "КОНЕЦ ТЗ", appType: "custom" as const,
  audience: "", primaryAction: "", features: [], style: "clean" as const, brandColors: "",
};
it("preserves the complete long brief including its final requirement", () => {
  expect(buildMaxProjectPrompt(longBrief)).toContain(longBrief.idea);
});

it("includes explicit owner modules without exposing private diary data", () => {
  const prompt = buildMaxProjectPrompt({ ...longBrief, managementModules: ["catalog", "orders"] });
  expect(prompt).toContain("Управление в Yleum: Каталог, Заказы.");
  expect(buildMaxProjectPrompt({ ...longBrief, managementModules: [] })).toContain("Управление в Yleum: не уточнено — используй возможности по задаче.");
  expect(prompt).toContain("Личные данные пользователей не превращай в общий реестр владельца");
});
it.each(["event", "education", "custom"] as const)("preserves an explicit empty module choice for %s", appType => {
  expect(defaultManagementModules(appType)).toEqual(["catalog", "leads"]);
  expect(newProjectManagementModules(appType, [])).toEqual([]);
});
it("keeps scenario defaults while respecting an explicit opt-out of orders or bookings", () => {
  expect(defaultManagementModules("catalog")).toEqual(["catalog", "orders", "leads"]);
  expect(defaultManagementModules("loyalty")).toEqual(["catalog", "orders", "leads"]);
  expect(defaultManagementModules("booking")).toEqual(["catalog", "leads", "bookings"]);
  expect(newProjectManagementModules("catalog", [])).toEqual([]);
  expect(newProjectManagementModules("catalog", ["catalog", "leads"])).toEqual(["catalog", "leads"]);
  expect(newProjectManagementModules("booking", ["catalog", "leads"])).toEqual(["catalog", "leads"]);
});

it("lets a new informational application explicitly opt out of catalog and requests", () => {
  expect(newProjectManagementModules("custom", [], undefined, ["catalog", "leads"])).toEqual([]);
  expect(newProjectManagementModules("booking", [], undefined, ["catalog"])).toEqual([]);
  expect(maxBrief.briefManagementModuleOptOuts("Справочная страница без заявок и каталога.")).toEqual(["catalog", "leads"]);
  expect(maxBrief.briefManagementModuleOptOuts("Покажи заявки и каталог.")).toEqual([]);
});

it.each(["Не только API: нужен интерфейс", "Не нужен api-only, нужен каталог", 'Пример: "api-only". Создай интерфейс', "Не оставляй backend-only: добавь экраны", "API-only не нужен: нужен интерфейс", "API-only — не нужно. Нужен интерфейс."])("does not turn a negative or quoted mention into API-only: %s", idea => {
  expect(maxBrief.isExplicitApiOnlyBrief(idea)).toBe(false);
  const prompt = buildMaxProjectPrompt({ ...longBrief, idea, brandColors: "красный" });
  expect(prompt).toContain("Цвета бренда: красный");
  expect(prompt).toContain(idea);
  expect(prompt.startsWith("Реализуй запрошенный API")).toBe(false);
});
it.each(["Только API для MAX", "Создай только API без интерфейса", "Implement API-only for MAX", "Нужен «API-only»", 'Сделай "API-only"', "Build `API-only` service", "Нужен ‘API-only’ сервис", "Build an API-only service", "I need an `API-only` backend"])("keeps an unambiguous positive API request: %s", idea => {
  expect(maxBrief.isExplicitApiOnlyBrief(idea)).toBe(true);
});
it.each(["Пример, так не надо:\n```text\nAPI-only\n```\nНужен интерфейс", "Пример:\n~~~text\nBuild API-only service\n~~~\nНужен интерфейс", "Пример:\n```\nAPI-only\n"])("does not classify fenced examples while preserving the raw request: %s", idea => {
  expect(maxBrief.isExplicitApiOnlyBrief(idea)).toBe(false);
  expect(buildMaxProjectPrompt({ ...longBrief, idea })).toContain(idea.trim());
});
it("does not remove modules because of quoted or double-negative exclusions", () => {
  expect(maxBrief.briefManagementModuleOptOuts('Не без каталога. Пример: «без заявок».')).toEqual([]);
  expect(maxBrief.briefManagementModuleOptOuts("Без ошибок, с каталогом и заказами")).toEqual([]);
  expect(maxBrief.isExplicitApiOnlyBrief('Название «API-only», нужен интерфейс')).toBe(false);
});
it("keeps SDK availability independent of vocabulary and explicit UI choices", () => {
  const brief = { ...longBrief, idea: "Хочу магазин с покупкой и оплатой", appTypes: ["custom"] as MaxAppTypeId[], managementModules: [] };
  expect(maxBrief.availableManagementModules()).toEqual(["catalog", "orders", "leads", "bookings"]);
  const prompt = buildMaxProjectPrompt(brief);
  expect(prompt).toContain(brief.idea);
  expect(prompt).toContain("Управление в Yleum: не уточнено — используй возможности по задаче.");
  expect(prompt).toContain(".omnia/management.json описывает доступные SDK-возможности, а не обязательные экраны или функции");
  expect(prompt).not.toContain("Используй только модули");
  expect(prompt).not.toContain("Владелец подтвердил пустой начальный каталог");
  expect(newProjectManagementModules("custom", [])).toEqual([]);
});
it("limits availability only through actual exclusions, including catalog dependencies", () => {
  expect(maxBrief.availableManagementModules(["orders"])).toEqual(["catalog", "leads", "bookings"]);
  expect(maxBrief.availableManagementModules(["catalog"])).toEqual(["leads"]);
  const brief = { ...longBrief, idea: "Хочу магазин с покупкой и оплатой, без заявок", managementModules: [] };
  const exclusions = maxBrief.briefManagementModuleOptOuts(brief.idea);
  expect(exclusions).toEqual(["leads"]);
  expect(maxBrief.availableManagementModules(exclusions)).toEqual(["catalog", "orders", "bookings"]);
  expect(buildMaxProjectPrompt(brief)).toContain("Явно исключено владельцем: Заявки.");
});
it("API-only changes presentation scope, not explicit functional data capabilities", () => {
  const brief = { ...longBrief, idea: "Только API каталога и заказов, без интерфейса", appTypes: ["custom"] as MaxAppTypeId[], managementModules: [],
    primaryAction: "Оформлять подтверждённый заказ", features: ["Уведомления бота" as const], brandColors: "Красный" };
  expect(maxBrief.isExplicitApiOnlyBrief(brief.idea)).toBe(true);
  expect(maxBrief.briefManagementModuleOptOuts(brief.idea)).toEqual([]);
  const prompt = buildMaxProjectPrompt(brief);
  expect(prompt).toContain("Управление в Yleum: не уточнено — используй возможности по задаче.");
  expect(maxBrief.availableManagementModules(maxBrief.briefManagementModuleOptOuts(brief.idea))).toContain("orders");
  expect(prompt).toContain(brief.primaryAction); expect(prompt).toContain("Уведомления бота"); expect(prompt).toContain("Цвета бренда: Красный");
  expect(prompt).not.toContain("Сразу собери целостный рабочий MVP");
});
it.each(["audience", "primaryAction", "brandColors"] as const)("validates the API field budget before creating: %s", field => {
  const limit = maxBrief.MAX_BRIEF_FIELD_LIMITS[field];
  expect(() => buildMaxProjectPrompt({ ...longBrief, [field]: "😀".repeat(limit) })).not.toThrow();
  expect(() => buildMaxProjectPrompt({ ...longBrief, [field]: "я".repeat(limit + 1) })).toThrow("Текст сохранён");
});
it("rejects an oversized brief instead of sending a partial request", () => {
  expect(() => buildMaxProjectPrompt({ ...longBrief, idea: "я".repeat(20_001) })).toThrow();
});
it("checks the assembled request including optional fields", () => {
  expect(() => buildMaxProjectPrompt({ ...longBrief, audience: "я".repeat(30_000) })).toThrow();
});

it("does not contradict an explicit request for empty lists with mandatory demo data", () => {
  const idea = "Список заметок. Пустой список, без демоданных. Новые записи создаёт пользователь.";
  const prompt = buildMaxProjectPrompt({ ...longBrief, idea });

  expect(prompt).toContain(idea);
  expect(prompt).not.toContain("реальные русские тексты и демонстрационные данные");
  expect(prompt).toContain("Начальные данные добавляй только по явному запросу пользователя");
});

it("preserves an explicit seed-data request instead of banning initial data", () => {
  const idea = "Каталог книг. Добавь три демонстрационные записи: Альфа, Бета и Гамма.";
  const prompt = buildMaxProjectPrompt({ ...longBrief, idea });

  expect(prompt).toContain(idea);
  expect(prompt).toContain("Начальные данные добавляй только по явному запросу пользователя");
});

it("carries every chosen scenario with a loyalty primary and the reviewed catalog contract", () => {
  const appTypes: MaxAppTypeId[] = ["loyalty", "catalog", "booking"];
  const brief = { ...longBrief, appType: "loyalty" as const, appTypes, catalogItemCount: 2 };
  const prompt = buildMaxProjectPrompt(brief);
  expect(prompt).toContain("Сценарии: Лояльность, Каталог, Запись.");
  expect(prompt).toContain("Управление в Yleum: Каталог, Заказы, Заявки, Запросы на запись");
  expect(prompt).toContain("2 позиций уже сохранены в Yleum");
  expect(prompt).toContain("не вшивай позиции в код");
  expect(appTypes).toEqual(["loyalty", "catalog", "booking"]);
});

it("describes selected scenarios when the owner leaves optional details empty", () => {
  const brief = { ...longBrief, appType: "booking", appTypes: ["booking", "catalog"], idea: "  " } satisfies maxBrief.MaxProjectBrief;
  expect(buildMaxProjectPrompt(brief)).toContain(
    "Сценарии: Запись, Каталог.\nЧто должно делать приложение: Расписание, специалисты и бронь. Товары, услуги, карточки и заказ.",
  );
  expect(buildMaxProjectPrompt({ ...brief, idea: "Запись по субботам" })).toContain(
    "Что должно делать приложение: Запись по субботам.",
  );
});

it("unions defaults for all selected types without replacing explicit module choices", () => {
  const appTypes: MaxAppTypeId[] = ["education", "catalog", "booking"];
  expect(defaultManagementModules("education", appTypes)).toEqual(["catalog", "orders", "leads", "bookings"]);
  expect(newProjectManagementModules("education", undefined, appTypes)).toEqual(["catalog", "orders", "leads", "bookings"]);
  expect(newProjectManagementModules("education", ["catalog", "leads", "bookings"], appTypes)).toEqual(["catalog", "leads", "bookings"]);
});

it("serializes scenario features separately from UI features and preserves legacy payloads", () => {
  expect(maxBrief.serializeMaxProjectFeatures).toBeTypeOf("function");
  const features = ["Профиль пользователя" as const];
  const brief = { ...longBrief, appType: "loyalty" as const, appTypes: ["loyalty", "catalog", "booking"] as MaxAppTypeId[], features };
  expect(maxBrief.serializeMaxProjectFeatures(brief)).toEqual([
    "Профиль пользователя", "Сценарий: Лояльность", "Сценарий: Каталог", "Сценарий: Запись",
  ]);
  expect(features).toEqual(["Профиль пользователя"]);
  expect(maxBrief.serializeMaxProjectFeatures({ ...longBrief, features })).toEqual(features);
  expect(maxBrief.serializeMaxProjectFeatures({ ...longBrief, appTypes: ["custom"], features })).toEqual(features);
});

it("rejects an empty or unknown multi-selection instead of silently falling back to the primary", () => {
  expect(() => buildMaxProjectPrompt({ ...longBrief, appTypes: [] })).toThrow();
  expect(() => buildMaxProjectPrompt({ ...longBrief, appTypes: ["unknown"] as unknown as MaxAppTypeId[] })).toThrow();
  const sparse: MaxAppTypeId[] = ["loyalty"];
  sparse.length = 2;
  expect(() => buildMaxProjectPrompt({ ...longBrief, appTypes: sparse })).toThrow();
});

it.each([0, 2])("keeps the reviewed data decision separate from selected catalog UI: %s", catalogItemCount => {
  const brief = { ...longBrief, managementModules: [], catalogItemCount };
  const prompt = buildMaxProjectPrompt(brief);
  expect(prompt).toContain("Управление в Yleum: не уточнено — используй возможности по задаче.");
  expect(prompt).toContain(catalogItemCount ? "2 позиций уже сохранены в Yleum" : "Владелец подтвердил пустой начальный каталог");
  const excluded = buildMaxProjectPrompt({ ...brief, managementModuleOptOuts: ["catalog"] });
  expect(excluded).not.toContain("Владелец подтвердил начальный каталог");
  expect(excluded).not.toContain("Владелец подтвердил пустой начальный каталог");
});
