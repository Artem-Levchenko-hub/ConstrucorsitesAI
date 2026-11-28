import { describe, expect, it } from "vitest";

import { extractInitialCatalog } from "../max-catalog-brief";

describe("extractInitialCatalog", () => {
  it("extracts explicit menu prices without treating the product brief as an item", () => {
    expect(extractInitialCatalog("Создай кофейню в стиле брутализм, латте 300 ₽, капучино 200 ₽")).toEqual({
      items: [{ title: "латте", price: "300 ₽" }, { title: "капучино", price: "200 ₽" }],
      tooManyItems: false,
      requestedEmpty: false,
    });
  });

  it("supports lists, explicit price separators, quoted commas and RUB spellings", () => {
    expect(extractInitialCatalog('Меню:\n- «Латте, карамель» — 1 200,50 руб.\n• Капучино: 250 рублей; 3. "Какао" - 199.90 р.').items).toEqual([
      { title: "Латте, карамель", price: "1 200,50 ₽" },
      { title: "Капучино", price: "250 ₽" },
      { title: "Какао", price: "199.90 ₽" },
    ]);
  });

  it("supports a catalog heading and nonbreaking thousand separators", () => {
    expect(extractInitialCatalog("Каталог: Консультация — 2\u202f500 руб; Разбор — 1\u00a0000 рублей").items).toEqual([
      { title: "Консультация", price: "2 500 ₽" }, { title: "Разбор", price: "1 000 ₽" },
    ]);
  });

  it("recognizes an explicit priced list inside a brief, without copying the instructions into item names", () => {
    expect(extractInitialCatalog("Сделай кофейню «Два зерна». На одной странице покажи два напитка: латте за 300 ₽ и капучино за 200 ₽. Владелец меняет цены в каталоге.").items).toEqual([
      { title: "латте", price: "300 ₽" }, { title: "капучино", price: "200 ₽" },
    ]);
  });

  it("extracts the promised rental stock with its daily prices", () => {
    const brief = "В демо-каталоге два инструмента по одному экземпляру: перфоратор за 650 ₽ в сутки и строительный пылесос за 500 ₽ в сутки. Бронь должна сохраняться после перезагрузки.";
    expect(extractInitialCatalog(brief).items).toEqual([
      { title: "перфоратор", price: "650 ₽ в сутки" },
      { title: "строительный пылесос", price: "500 ₽ в сутки" },
    ]);
    expect(extractInitialCatalog("Демо-каталог:\n1. Шлифмашина — 720 руб./сут.\n2. Плиткорез — 430 ₽ в сутки.").items).toEqual([
      { title: "Шлифмашина", price: "720 ₽ в сутки" },
      { title: "Плиткорез", price: "430 ₽ в сутки" },
    ]);
  });

  it("neither drops an unsupported rate's unit nor folds a separate price into a rental item name", () => {
    expect(extractInitialCatalog("Демо-каталог: Дрель 650 руб./час; Плиткорез 430 ₽ в сутки").items).toEqual([
      { title: "Плиткорез", price: "430 ₽ в сутки" },
    ]);
    expect(extractInitialCatalog("Демо-каталог: Перфоратор 650 р. Плиткорез 430 ₽ в сутки").items).toEqual([]);
  });

  it("keeps literal item names and opens the catalog after a separate budget", () => {
    expect(extractInitialCatalog("Бюджет:\n15 000 ₽\nДемо-каталог:\n- Пылесос без мешка: 500 ₽ в сутки\n- Перфоратор: 650 ₽ в сутки").items).toEqual([
      { title: "Пылесос без мешка", price: "500 ₽ в сутки" },
      { title: "Перфоратор", price: "650 ₽ в сутки" },
    ]);
    expect(extractInitialCatalog("Бюджет:\n15 000 ₽\nПокажи два напитка: Латте 300 ₽ и капучино 250 ₽").items).toEqual([
      { title: "Латте", price: "300 ₽" },
      { title: "капучино", price: "250 ₽" },
    ]);
  });

  it("keeps consecutive catalog lines and ends the section at unrelated instructions", () => {
    const stock = "Демо-каталог:\nШлифмашина 720 ₽ в сутки.\nПлиткорез 430 ₽ в сутки";
    const items = [{ title: "Шлифмашина", price: "720 ₽ в сутки" }, { title: "Плиткорез", price: "430 ₽ в сутки" }];
    expect(extractInitialCatalog(stock).items).toEqual(items);
    expect(extractInitialCatalog(stock + "\nХарактеристики тестовой комнаты:\nГенератор 900 ₽ в сутки").items).toEqual(items);
    expect(extractInitialCatalog(stock + "\nПредупреждение. Например, генератор 900 ₽ в сутки").items).toEqual(items);
    expect(extractInitialCatalog(stock + ".\nДля примера генератор 900 ₽ в сутки").items).toEqual(items);
  });

  it("does not turn rental prices in an example or restriction into supplied stock", () => {
    for (const prefix of ["Например,", "Не заполняй это: ", "Бюджет:"]) {
      const brief = `${prefix} в демо-каталоге два инструмента: шлифмашина 720 ₽ в сутки и плиткорез 430 ₽ в сутки`;
      expect(extractInitialCatalog(brief).items).toEqual([]);
    }
    const stock = "Демо-каталог: Шлифмашина — 720 ₽ в сутки";
    for (const example of ["; например, Плиткорез 430 ₽ в сутки", "\nПример:\nПлиткорез 430 ₽ в сутки", "; Не добавляй дрель 650 ₽ в сутки и плиткорез 430 ₽ в сутки"]) {
      expect(extractInitialCatalog(stock + example).items).toEqual([{ title: "Шлифмашина", price: "720 ₽ в сутки" }]);
    }
    expect(extractInitialCatalog("Например:\nДемо-каталог:\n- Шлифмашина 720 ₽ в сутки").items).toEqual([]);
    expect(extractInitialCatalog("Бюджет на аренду:\n  Демо-каталог:\n    - Шлифмашина 720 ₽ в сутки").items).toEqual([]);
    expect(extractInitialCatalog("Например:\n- Товар 450 ₽/сутки\n\nДемо-каталог: Шлифмашина 720 ₽ в сутки").items).toEqual([
      { title: "Шлифмашина", price: "720 ₽ в сутки" },
    ]);
  });

  it.each(["Латте за 300 ₽; капучино за 200 ₽", "Латте 300 ₽ и капучино 200 ₽"])("allows each explicitly priced member of a list: %s", brief => {
    expect(extractInitialCatalog(brief).items).toEqual([
      { title: "Латте", price: "300 ₽" }, { title: "капучино", price: "200 ₽" },
    ]);
  });

  it("keeps abbreviated currencies out of the next item's title", () => {
    expect(extractInitialCatalog("Латте 300 руб. и капучино 200 руб.").items).toEqual([
      { title: "Латте", price: "300 ₽" }, { title: "капучино", price: "200 ₽" },
    ]);
  });

  it.each(["Не добавляй в каталог: латте 300 ₽", "Не включай в меню: латте 300 ₽", "Бюджет на товары: Латте 300 ₽", "Например, покажи два напитка: Латте 300 ₽", "Бюджет: покажи товары: Латте 300 ₽"])("does not strip the intent from a non-catalog heading: %s", brief => {
    expect(extractInitialCatalog(brief).items).toEqual([]);
  });

  it.each([
    "Бюджет 300 ₽", "бюджет проекта: 300 рублей", "Доставка — 200 ₽", "Стоимость доставки: 200 ₽",
    "Минимальный заказ 500 ₽", "телефон +7 999 123 45 67", "Телефон — 300 ₽",
    "Латте 300 мл", "Кофе 300 мл — 200 ₽", "Количество: 2 шт — 300 ₽", "2 порции — 300 ₽",
    "до 20.12.2026 — 300 ₽", "Скидка 10% — 300 ₽", "Цена от 300 ₽", "Латте от 300 ₽",
    "Латте 300", "Латте 300 руб за штуку", "Латте 12 34 ₽",
    "Латте 300,999 ₽", "Латте -300 ₽", "Латте +300 ₽", "Создай кофейню за 300 ₽",
    "Свяжитесь test@example.com — 300 ₽", "https://example.com — 300 ₽", "«Латте 300 ₽",
    "300 ₽", "Латте300 ₽", "iPhone15 300 ₽", "Латте 300 $",
    "Контакт +7 (999) 1234567 — 300 ₽", "Контакт 79991234567 — 300 ₽", "30 ноября 2026 — 300 ₽",
    "Латте ×2 — 300 ₽", "Кофе\u0000 — 300 ₽",
    "Латте и капучино — 300 ₽", "Латте/Капучино 300 ₽", "Латте или капучино 300 ₽",
  ])("does not invent a catalog item from ambiguous/non-item text: %s", (brief) => {
    expect(extractInitialCatalog(brief).items).toEqual([]);
  });

  it.each([
    "Пустой каталог. Например, Латте 300 ₽",
    "Оставь каталог пустым; Латте 300 ₽",
    "Товары не добавлять\nЛатте 300 ₽",
    "Не добавляй товары; Латте 300 ₽",
    "Не заполняй каталог, Латте 300 ₽",
    "Каталог без товаров; Латте 300 ₽",
    "Пусть каталог будет пустым; Латте 300 ₽",
    "Каталог оставь пустым; Латте 300 ₽",
  ])("honors an explicit empty-catalog instruction before later examples", (brief) => {
    expect(extractInitialCatalog(brief)).toEqual({ items: [], requestedEmpty: true, tooManyItems: false });
  });

  it.each(["Не делай пустой каталог", "Не пустой каталог", "Каталог не пустой", "Непустой каталог"])("does not read a negation of empty as an empty-catalog request: %s", (prefix) => {
    expect(extractInitialCatalog(`${prefix}, Латте 300 ₽`)).toEqual({
      items: [{ title: "Латте", price: "300 ₽" }], requestedEmpty: false, tooManyItems: false,
    });
  });

  it("deduplicates normalized titles/prices and removes conflicting titles entirely", () => {
    expect(extractInitialCatalog("Латте 300 ₽; латте 300,00 руб.; Капучино 200 ₽; Капучино 250 ₽; Капучино 200 ₽; Какао 0 ₽").items).toEqual([
      { title: "Латте", price: "300 ₽" }, { title: "Какао", price: "0 ₽" },
    ]);
  });

  it("keeps an explicitly quoted compound product name intact", () => {
    expect(extractInitialCatalog("«Чай и кофе» — 300 ₽").items).toEqual([{ title: "Чай и кофе", price: "300 ₽" }]);
  });

  it("counts distinct unambiguous items before applying the 100-item cap", () => {
    const hundred = Array.from({ length: 100 }, (_, i) => `Товар ${i + 1} — 100 ₽`).join("\n");
    expect(extractInitialCatalog(hundred).items).toHaveLength(100);
    expect(extractInitialCatalog(hundred).tooManyItems).toBe(false);
    const overflow = extractInitialCatalog(`${hundred}\nОсобый товар — 200 ₽`);
    expect(overflow.items).toHaveLength(100);
    expect(overflow.tooManyItems).toBe(true);
    expect(extractInitialCatalog(`${hundred}\nТовар 1 — 200 ₽`).items).toHaveLength(99);
    expect(extractInitialCatalog(`${hundred}\nТовар 1 — 200 ₽`).tooManyItems).toBe(false);
  });

  it("does not silently truncate titles or prices", () => {
    const title = "Я".repeat(120);
    expect(extractInitialCatalog(`${title} — 300 ₽`).items).toEqual([{ title, price: "300 ₽" }]);
    expect(extractInitialCatalog(`${title}Я — 300 ₽; Латте — ${"1".repeat(81)} ₽`).items).toEqual([]);
    expect(extractInitialCatalog(`Латте ${"1".repeat(78)}₽`).items).toEqual([{ title: "Латте", price: `${"1".repeat(78)} ₽` }]);
    expect(extractInitialCatalog(`Латте ${"1".repeat(79)}₽`).items).toEqual([]);
  });

  it("keeps valid Unicode briefs above 20 KiB and rejects over 20,000 codepoints as a whole", () => {
    expect(extractInitialCatalog(`${"Я".repeat(19_000)};Латте 300 ₽`).items).toEqual([{ title: "Латте", price: "300 ₽" }]);
    expect(extractInitialCatalog(`${"😀".repeat(19_000)};Латте 300 ₽`).items).toEqual([{ title: "Латте", price: "300 ₽" }]);
    expect(extractInitialCatalog(`Латте 300 ₽;${"Я".repeat(20_000)}`)).toEqual({
      items: [], requestedEmpty: false, tooManyItems: false,
    });
    expect(extractInitialCatalog(`Латте 300 ₽;${"a".repeat(21_000)}`).items).toEqual([]);
  });

  it("stays deterministic and bounded for varied Unicode/noisy strings", () => {
    let seed = 17;
    const alphabet = ["я", " ", "0", ",", "₽", "\n", "«", "»", "😀", ":", ".", "\ud800"];
    for (let sample = 0; sample < 100; sample += 1) {
      const text = Array.from({ length: sample * 7 }, () => {
        seed = (seed * 1664525 + 1013904223) >>> 0;
        return alphabet[seed % alphabet.length];
      }).join("");
      const result = extractInitialCatalog(text);
      expect(extractInitialCatalog(text)).toEqual(result);
      expect(result.items.length).toBeLessThanOrEqual(100);
      for (const item of result.items) {
        expect([...item.title].length).toBeLessThanOrEqual(120);
        expect([...item.price].length).toBeLessThanOrEqual(80);
      }
    }
    expect(extractInitialCatalog("9".repeat(20_000)).items).toEqual([]);
  });

  it.each(["Не оставляй каталог пустым", "Не делай каталог пустым", "Не создавай каталог пустым"])("does not invert reverse-order negation: %s", instruction => {
    expect(extractInitialCatalog(`${instruction}, Latte 300 ₽`)).toEqual({
      items: [{ title: "Latte", price: "300 ₽" }], requestedEmpty: false, tooManyItems: false,
    });
  });
});
