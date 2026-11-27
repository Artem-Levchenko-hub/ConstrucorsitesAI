import { describe, expect, it } from "vitest";
import { buildMaxProjectPrompt, type MaxProjectBrief } from "@/lib/max-brief";
import { userMessageLayers, userMessagePresentation } from "@/lib/user-message-presentation";

const brief: MaxProjectBrief = {
  name: "Кофе рядом.", appType: "custom", audience: "Гости кофейни", primaryAction: "Заказать кофе",
  idea: "Один экран без карточек.\nЦелевая аудитория: эта строка — часть самой задачи.\nСохранить 🎨 и переносы.",
  features: ["Профиль пользователя"], brandColors: "Молочный, #b71c1c",
};

describe("known user-message envelopes", () => {
  it("presents the generated wizard title and entire multiline task without rewriting its source", () => {
    const source = buildMaxProjectPrompt(brief);
    const view = userMessagePresentation(source)!;
    expect(view.kind).toBe("wizard");
    expect(view.title).toBe(brief.name);
    expect(view.task).toBe(brief.idea);
    expect(view.groups.map(group => group.title)).toEqual(["Сценарий и функции", "Оформление", "Управление"]);
    expect(view.groups.flatMap(group => group.fields)).toContainEqual({ label: "Цвета", value: brief.brandColors });
    expect(view.task).not.toContain("Это служебное задание");
    expect(source).toBe(buildMaxProjectPrompt(brief));
  });

  it("supports the current API-only envelope and preserves its explicit parameters", () => {
    const view = userMessagePresentation(buildMaxProjectPrompt({ ...brief, idea: "API-only. Только чтение списка." }))!;
    expect(view.task).toBe("API-only. Только чтение списка.");
    expect(view.groups.map(group => group.title)).toEqual(["Сценарий и функции", "Оформление", "Управление"]);
    expect(view.groups.flatMap(group => group.fields)).toEqual(expect.arrayContaining([
      { label: "Главное действие", value: brief.primaryAction },
      { label: "Возможности", value: brief.features.join(", ") },
      { label: "Цвета", value: brief.brandColors },
    ]));
  });

  it.each(Array.from({ length: 8 }, (_, mask) => mask))("presents only explicitly supplied API parameters (mask %i)", mask => {
    const input = { ...brief, idea: "API-only. Только чтение списка.",
      primaryAction: mask & 1 ? brief.primaryAction : "",
      features: mask & 2 ? brief.features : [], brandColors: mask & 4 ? brief.brandColors : "" };
    const source = buildMaxProjectPrompt(input);
    const view = userMessagePresentation(source)!;
    expect(view.task).toBe(input.idea);
    expect(view.groups.flatMap(group => group.fields).map(item => item.label)).toEqual([
      "Сценарий", "Аудитория", ...(mask & 1 ? ["Главное действие"] : []),
      ...(mask & 2 ? ["Возможности"] : []), ...(mask & 4 ? ["Цвета"] : []), "Данные приложения",
    ]);
    expect(source).toBe(buildMaxProjectPrompt(input));
  });

  it("retains the historical API envelope without optional parameters or the later scope guard", () => {
    const source = buildMaxProjectPrompt({ ...brief, idea: "API-only. Только чтение списка.", primaryAction: "", features: [], brandColors: "" })
      .replace(/^Режим только API[^\n]+\n/m, "");
    const view = userMessagePresentation(source)!;
    expect(view.task).toBe("API-only. Только чтение списка.");
    expect(view.groups.map(group => group.title)).toEqual(["Сценарий и функции", "Управление"]);
    expect(view.groups.flatMap(group => group.fields).map(item => item.label)).toEqual(["Сценарий", "Аудитория", "Данные приложения"]);
  });

  it.each(["unknown-field", "unknown-scope", "missing-scope", "malformed-field", "reordered-fields"] as const)("keeps an unrecognized or incomplete API parameter block intact: %s", mode => {
    let source = buildMaxProjectPrompt({ ...brief, idea: "API-only. Только чтение списка." });
    if (mode === "unknown-field") source = source.replace("Нужные возможности:", "Другое пожелание:");
    if (mode === "unknown-scope") source = source.replace("Режим только API не требует", "НЕ ТЕРЯТЬ: режим только API требует");
    if (mode === "missing-scope") source = source.replace(/^Режим только API[^\n]+\n/m, "");
    if (mode === "malformed-field") source = source.replace("Главное действие пользователя: Заказать кофе.", "Главное действие пользователя: Заказать кофе");
    if (mode === "reordered-fields") source = source.replace("Главное действие пользователя: Заказать кофе.\nНужные возможности: Профиль пользователя.", "Нужные возможности: Профиль пользователя.\nГлавное действие пользователя: Заказать кофе.");
    expect(userMessagePresentation(source)).toBeNull();
    expect(userMessageLayers(source)).toEqual([]);
  });

  it("preserves a complete wizard nested inside a current API task", () => {
    const nested = buildMaxProjectPrompt(brief);
    const idea = `API-only.\n${nested}`;
    const source = buildMaxProjectPrompt({ ...brief, idea });
    const view = userMessagePresentation(source)!;
    expect(view.task).toBe(idea);
    expect(view.groups.flatMap(group => group.fields)).toContainEqual({ label: "Цвета", value: brief.brandColors });
    expect(source).toContain(nested);
  });

  it("retains historical style parameters and a legacy task without the later public-description guard", () => {
    const legacy = buildMaxProjectPrompt(brief)
      .replace(/^Это служебное задание[^\n]+\n/m, "")
      .replace("Цвета бренда:", "Визуальное направление: В стиле бренда.\nЦвета бренда:");
    const view = userMessagePresentation(legacy)!;
    expect(view.task).toBe(brief.idea);
    expect(view.groups[1].fields).toContainEqual({ label: "Визуальное направление", value: "В стиле бренда" });
  });

  it("preserves a complete nested wizard as the outer task instead of splitting at its inner fields", () => {
    const inner = buildMaxProjectPrompt(brief);
    const outer = buildMaxProjectPrompt({ ...brief, name: "Внешнее приложение", idea: inner });
    const view = userMessagePresentation(outer)!;
    expect(view.title).toBe("Внешнее приложение");
    expect(view.task).toBe(inner);
    expect(userMessagePresentation(view.task)?.task).toBe(brief.idea);
  });

  it("peels the older management-first, unterminated-name envelope inside a later wizard", () => {
    const current = buildMaxProjectPrompt(brief);
    const management = current.split("\n").find(line => line.startsWith("Управление в Yleum: "))!;
    const legacy = current.replace("Название продукта: Кофе рядом..", "Название продукта: Кофе рядом")
      .replace(management + "\n", "")
      .replace("Цвета бренда:", management + "\nВизуальное направление: В стиле бренда.\nЦвета бренда:");
    const outer = buildMaxProjectPrompt({ ...brief, idea: legacy });
    const layers = userMessageLayers(outer);
    expect(layers).toHaveLength(2);
    expect(layers[1].title).toBe("Кофе рядом");
    expect(layers[1].task).toBe(brief.idea);
    expect(layers[1].groups.flatMap(group => group.fields)).toContainEqual({ label: "Визуальное направление", value: "В стиле бренда" });
    expect(layers[0].task).toBe(legacy);
  });

  it("preserves nested quiz envelopes and every palette/value character", () => {
    const inner = `${buildMaxProjectPrompt(brief)}\n— Бриф из опроса —\n• Тон: Спокойный (палитра: #abcdef)`;
    const outer = `${inner}\n— Бриф из опроса —\n• Разделы: A, B`;
    const view = userMessagePresentation(outer)!;
    expect(view.kind).toBe("quiz"); expect(view.task).toBe(inner);
    const nested = userMessagePresentation(view.task)!;
    expect(nested.groups[0].fields[0].value).toBe("Спокойный (палитра: #abcdef)");
    expect(userMessagePresentation(nested.task)?.task).toBe(brief.idea);
  });

  it.each([
    "Своя задача\nНазвание продукта: Тест.\nЦелевая аудитория: Все.",
    "Пример — Бриф из опроса —\n• Тон: Спокойный",
    "Задача\n— Бриф из опроса —\n• Тон: Спокойный\nНе потерять эту строку",
    "Задача\n— Бриф из опроса —\n• Тон: Первая строка\n  Продолжение ответа",
    buildMaxProjectPrompt(brief).replace("Не превращай его", "Изменённая обёртка: не превращай его"),
    buildMaxProjectPrompt(brief) + "\nДополнительное пожелание после обёртки.",
    buildMaxProjectPrompt(brief).replace("Нужные возможности:", "Незнакомый параметр:"),
  ])("leaves unknown or incomplete freeform text intact: %s", source => {
    expect(userMessagePresentation(source)).toBeNull();
  });

  it("accepts CRLF for display while the original string remains exact", () => {
    const source = buildMaxProjectPrompt(brief).replaceAll("\n", "\r\n");
    expect(userMessagePresentation(source)?.task).toBe(brief.idea);
    expect(source).toContain("\r\n");
  });

  it.each(["current", "previous", "legacy", "api-only"] as const)("never hides an unknown trailing requirement inside a %s envelope", mode => {
    let source = buildMaxProjectPrompt({ ...brief, idea: mode === "api-only" ? "API-only. Список." : brief.idea });
    if (mode === "previous") source = source.replace(/^Сразу собери целостный рабочий MVP[^\n]+$/m,
      "Сразу собери целостный рабочий MVP. Экраны и навигацию добавляй по запросу владельца, а не по отраслевому шаблону. Предусмотри состояния загрузки/пустого списка/ошибки, реальные русские тексты. Начальные данные добавляй только по явному запросу пользователя; иначе оставь списки пустыми.");
    if (mode === "legacy") source = source.replace(/^Сразу собери целостный рабочий MVP[^\n]+$/m,
      "Сразу собери целостный рабочий MVP: мобильную навигацию, все основные экраны, состояния загрузки/пустого списка/ошибки, реальные русские тексты. Начальные данные добавляй только по явному запросу пользователя; иначе оставь списки пустыми.");
    expect(userMessagePresentation(source)).not.toBeNull();
    const lastLine = source.lastIndexOf("\n");
    const unknown = source.slice(0, lastLine) + "\nНЕ ТЕРЯТЬ: пользовательское условие после параметров." + source.slice(lastLine);
    expect(userMessagePresentation(unknown)).toBeNull();
    expect(userMessageLayers(unknown)).toEqual([]);
  });

  it.each([0, 1, 100])("recognizes the exact supported catalog suffix for %i confirmed records", count => {
    const source = buildMaxProjectPrompt({ ...brief, catalogItemCount: count });
    expect(userMessagePresentation(source)?.task).toBe(brief.idea);
  });
});
