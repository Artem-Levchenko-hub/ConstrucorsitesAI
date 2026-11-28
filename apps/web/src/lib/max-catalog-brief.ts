import { MAX_BRIEF_LENGTH } from "./max-brief";

type InitialCatalogItem = { title: string; price: string };
type Candidate = InitialCatalogItem & { priceKey: string };
type Segment = { text: string; explicitCatalog: boolean };

const ITEM_LIMIT = 100;
const QUOTES = new Map([["«", "»"], ["“", "”"], ['"', '"'], ["'", "'"]]);
const DAILY_RATE = /(?:[^\S\r\n]+(?:в|за)[^\S\r\n]+|[^\S\r\n]*\/[^\S\r\n]*)(?:сутки|сут\.)/iu;
const PRICE_END = new RegExp("(?:₽|руб(?:лей|ля|ль)?\\.?|р\\.)(?<daily>" + DAILY_RATE.source + ")?\\.?$", "iu");
const RATE_AFTER_RUBLE_DOT = new RegExp("^\\.(?:" + DAILY_RATE.source + "(?![\\p{L}\\p{N}_])|[^\\S\\r\\n]*\\/)", "iu");
const RUBLES_IN_TITLE = /\d\s*(?:руб(?:лей|ля|ль)?\.?|р\.)(?![\p{L}\p{N}_])/iu;
const LEADING_MARKER = /^(?:[-*•–]\s+|\d{1,3}[.)]\s+)/u;
const EMPTY_LINE = /^[^\S\r\n]*(?:\r?\n|\r)/u;
const EXCLUDED_ENTRY = /^(?:не|без|бюджет|например|пример|для\s+(?:примера|сравнения))(?![\p{L}\p{N}_])/iu;
const BUDGET_HEADING = /^бюджет(?![\p{L}\p{N}_])/iu;
const CATALOG_HEADING = /^(?:меню|(?:демо[- ]?)?каталог|товары|услуги|позиции|напитки|напитка)$/iu;
const CATALOG_STOCK = /^в\s+(?:демо[- ]?)?каталоге\s+(?:(?:один|одна|два|две|три|четыре|пять|\d{1,3})\s+)?(?:инструмент(?:а|ов)?|товар(?:а|ов)?|позиц(?:ия|ии|ий))(?:\s+по\s+одному\s+экземпляру)?$/iu;
const CATALOG_REQUEST = /(?:^|\s)(?:покажи(?:те)?|добавь(?:те)?|выведи(?:те)?)\s+(?:(?:два|две|три|четыре|пять|\d{1,3})\s+)?(?:товары|услуги|позиции|напитки|напитка)$/iu;
const NON_CATALOG_CONTEXT = /(?<![\p{L}\p{N}_])(?:не|без|бюджет|например|пример)(?![\p{L}\p{N}_])/iu;
const NON_ITEM = /(?:бюджет|телефон|доставк|курьер|самовывоз|минимальн|количеств|скидк|процент|итого|сумма|расход|баланс|лимит|выручк|доход|акци[яию]|стоимость|цена|\d\s*(?:мл|литр|кг|грамм|шт\.?|штук|порци)|\d[./-]\d|\d\s+(?:январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр)|\+\s*\d|\(\d{2,}\)|\d{7,}|[%@₽×]|https?:|(?:^|\s)(?:от|до|за|стоит|будет|нужно)(?:\s|$))/iu;
const INSTRUCTION = /^(?:создай|создать|сделай|сделать|хочу|нужен|нужна|добавь|например|бюджет)(?:\s|$)/iu;

function normalizeSpace(text: string): string {
  return text.normalize("NFC").replace(/\s+/gu, " ").trim();
}

function wantsEmptyCatalog(text: string): boolean {
  // An explicit empty instruction wins over any illustrative prices later on.
  const affirmative = text.replace(/не\s+(?:(?:делай|создавай|оставляй)\s+)?(?:пуст(?:ой|ым)\s+каталог|каталог\s+пуст(?:ой|ым))|каталог\s+не\s+пуст(?:ой|ым)/giu, "");
  return /(?<![\p{L}\p{N}_])(?:пуст(?:ой|ым)\s+каталог|каталог\s+(?:(?:будет|оставь|оставить)\s+)?(?:пуст(?:ой|ым)|без\s+товаров)|товары\s+не\s+добавлять|не\s+добавля(?:й|ть)\s+товары|не\s+заполня(?:й|ть)\s+каталог|оставь\s+каталог\s+пустым)/iu.test(affirmative);
}

function isCatalogHeading(prefix: string): boolean {
  const clause = prefix.split(/[.!?\n]/u).at(-1)?.trim() ?? "";
  return CATALOG_HEADING.test(clause) || CATALOG_REQUEST.test(clause) || CATALOG_STOCK.test(clause);
}

function priceEnding(text: string, explicitCatalog: boolean): RegExpExecArray | null {
  const match = PRICE_END.exec(text);
  return match?.groups?.daily && !explicitCatalog ? null : match;
}

function splitSegments(text: string): Segment[] {
  const segments: Segment[] = [];
  let start = 0;
  let headingContextStart = 0;
  let explicitCatalog = false;
  let excludedSection: "example" | "budget" | null = null;
  let closingQuote: string | undefined;
  function pushSegment(end: number, lineEnd = false) {
    const segment = text.slice(start, end);
    if (excludedSection === "budget") {
      const amount = segment.trim();
      const ending = priceEnding(amount, false);
      if (ending && /^\d[\d\s.,]*$/u.test(amount.slice(0, ending.index))) excludedSection = null;
    }
    if (explicitCatalog && (
      EXCLUDED_ENTRY.test(segment.trimStart().replace(LEADING_MARKER, ""))
      || EXCLUDED_ENTRY.test(text.slice(Math.max(start, headingContextStart), end).trimStart())
      || (lineEnd && segment.trimEnd().endsWith(":"))
    )) {
      explicitCatalog = false;
    }
    segments.push({ text: segment, explicitCatalog });
  }
  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    if (closingQuote) {
      if (char === closingQuote) closingQuote = undefined;
      continue;
    }
    // Apostrophes inside a name are not opening quotes.
    if (QUOTES.has(char) && (char !== "'" || index === 0 || /[\s:—–-]/u.test(text[index - 1]))) {
      closingQuote = QUOTES.get(char);
      continue;
    }
    const decimalComma = char === "," && /\d/u.test(text[index - 1] ?? "") && /\d/u.test(text[index + 1] ?? "");
    if (char === ":" && index - headingContextStart <= 512) {
      const prefix = text.slice(headingContextStart, index);
      const lead = prefix.trimStart().replace(LEADING_MARKER, "");
      if (EXCLUDED_ENTRY.test(lead)) {
        explicitCatalog = false;
        // A standalone heading also governs its list. A budget ends after
        // its value, while examples and exclusions end at a blank line.
        if (EMPTY_LINE.test(text.slice(index + 1))) {
          excludedSection ??= BUDGET_HEADING.test(lead) ? "budget" : "example";
        }
      } else if (!excludedSection && !NON_CATALOG_CONTEXT.test(prefix) && isCatalogHeading(prefix)) {
        start = index + 1;
        headingContextStart = start;
        explicitCatalog = true;
        continue;
      }
    }
    // Look only at a bounded suffix; large noisy briefs must stay linear.
    const tail = text.slice(Math.max(start, index - 40), index).trimEnd();
    if (char === "." && explicitCatalog && /(?:руб|р)$/iu.test(tail)
      && RATE_AFTER_RUBLE_DOT.test(text.slice(index, index + 40))) continue;
    const pricedClause = (char === "." || /\s/u.test(char))
      && priceEnding(tail, explicitCatalog) !== null;
    const conjunction = pricedClause && /\s/u.test(char)
      ? /^\s+и\s+/iu.exec(text.slice(index)) : null;
    if (conjunction) {
      pushSegment(index);
      index += conjunction[0].length - 1;
      start = index + 1;
      continue;
    }
    if (char === "." && pricedClause && !/^\s+и\s+/iu.test(text.slice(index + 1))) {
      pushSegment(index);
      start = index + 1;
      headingContextStart = start;
      explicitCatalog &&= EMPTY_LINE.test(text.slice(start));
      continue;
    }
    if (char === "." && !/^\s+и\s+/iu.test(text.slice(index + 1))) {
      headingContextStart = index + 1;
    }
    if (char === ";" || char === "\n" || char === "\r" || (char === "," && !decimalComma)) {
      pushSegment(index, char === "\n" || char === "\r");
      start = index + 1;
      if (char !== ",") headingContextStart = start;
      if ((char === "\n" || (char === "\r" && text[start] !== "\n"))
        && EMPTY_LINE.test(text.slice(start))) {
        explicitCatalog = false;
        excludedSection = null;
      }
    }
  }
  pushSegment(text.length);
  return segments;
}

function parseCandidate(segment: Segment): Candidate | null {
  const text = segment.text.trim()
    .replace(LEADING_MARKER, "")
    .replace(/^(?:меню|каталог|товары|услуги|позиции|напитки)\s*:\s*/iu, "");
  const priceEnd = priceEnding(text, segment.explicitCatalog);
  if (!priceEnd) return null;
  const beforeCurrency = text.slice(0, priceEnd.index);
  let amountStart = beforeCurrency.length;
  while (amountStart > 0 && /[\d\s.,]/u.test(beforeCurrency[amountStart - 1])) amountStart -= 1;
  const rawAmount = beforeCurrency.slice(amountStart);
  const prefix = beforeCurrency.slice(0, amountStart);
  // No joining a number embedded in a name, guessing a sign, or choosing one of two prices.
  if (!/^\s/u.test(rawAmount) && !/["'»”’:—–]$/u.test(prefix)) return null;
  if (prefix.endsWith("+") || (prefix.endsWith("-") && !/^\s/u.test(rawAmount))) return null;
  const amount = normalizeSpace(rawAmount);
  if (!/^(?:\d{1,3}(?: \d{3})+|\d+)(?:[.,]\d{1,2})?$/u.test(amount)) return null;
  const daily = Boolean(priceEnd.groups?.daily);
  const price = `${amount} ₽${daily ? " в сутки" : ""}`;
  if ([...rawAmount.trim(), ...priceEnd[0]].length > 80 || [...price].length > 80) return null;

  let title = normalizeSpace(prefix.replace(/\s*[:—–-]\s*$/u, "").replace(/\s+за\s*$/iu, ""));
  const quote = QUOTES.get(title[0]);
  if (quote) {
    if (!title.endsWith(quote)) return null;
    title = normalizeSpace(title.slice(1, -1));
  } else if (/["«»“”:/+&]|^[’']|\s+(?:и|или|либо)\s+/iu.test(title)) return null;
  if (daily && !quote && RUBLES_IN_TITLE.test(title)) return null;
  if (!title || [...title].length > 120 || !/\p{L}/u.test(title) || /\p{C}/u.test(title) || NON_ITEM.test(title) || INSTRUCTION.test(title)) return null;

  const [whole, fraction = ""] = amount.replace(/ /gu, "").replace(",", ".").split(".");
  const priceKey = `${whole.replace(/^0+(?=\d)/u, "")}.${fraction.padEnd(2, "0")}${daily ? "/day" : ""}`;
  return { title, price, priceKey };
}

/** Deterministic proposals only; the owner must review them before catalog creation. */
export function extractInitialCatalog(text: string): { items: InitialCatalogItem[]; tooManyItems: boolean; requestedEmpty: boolean } {
  const empty = { items: [], tooManyItems: false, requestedEmpty: false };
  // Reject the whole oversized brief. Never silently parse a truncated prefix.
  if (text.length > MAX_BRIEF_LENGTH * 2 || [...text].length > MAX_BRIEF_LENGTH) return empty;
  if (wantsEmptyCatalog(text)) return { ...empty, requestedEmpty: true };

  const byTitle = new Map<string, Candidate | null>();
  for (const segment of splitSegments(text)) {
    const item = parseCandidate(segment);
    if (!item) continue;
    const key = item.title.normalize("NFKC").toLowerCase();
    if (!byTitle.has(key)) byTitle.set(key, item);
    else if (byTitle.get(key)?.priceKey !== item.priceKey) byTitle.set(key, null);
  }
  const items = [...byTitle.values()].filter((item): item is Candidate => item !== null);
  return { items: items.slice(0, ITEM_LIMIT).map(({ title, price }) => ({ title, price })), tooManyItems: items.length > ITEM_LIMIT, requestedEmpty: false };
}
