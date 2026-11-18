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

  it("supports API-only envelopes without inventing UI parameters", () => {
    const view = userMessagePresentation(buildMaxProjectPrompt({ ...brief, idea: "API-only. Только чтение списка." }))!;
    expect(view.task).toBe("API-only. Только чтение списка.");
    expect(view.groups.map(group => group.title)).toEqual(["Сценарий и функции", "Управление"]);
    expect(view.groups.flatMap(group => group.fields).map(item => item.label)).toEqual(["Сценарий", "Аудитория", "Данные приложения"]);
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

  it.each(["current", "legacy", "api-only"] as const)("never hides an unknown trailing requirement inside a %s envelope", mode => {
    let source = buildMaxProjectPrompt({ ...brief, idea: mode === "api-only" ? "API-only. Список." : brief.idea });
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
