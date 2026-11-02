import { MAX_BRIEF_LENGTH } from "./max-brief";

type InitialCatalogItem = { title: string; price: string };
type Candidate = InitialCatalogItem & { priceKey: string };

const ITEM_LIMIT = 100;
const QUOTES = new Map([["«", "»"], ["“", "”"], ['"', '"'], ["'", "'"]]);
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

function splitSegments(text: string): string[] {
  const segments: string[] = [];
  let start = 0;
  let closingQuote: string | undefined;
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
    if (char === ";" || char === "\n" || char === "\r" || (char === "," && !decimalComma)) {
      segments.push(text.slice(start, index));
      start = index + 1;
    }
  }
  segments.push(text.slice(start));
  return segments;
}

function parseCandidate(segment: string): Candidate | null {
  const text = segment.trim()
    .replace(/^(?:[-*•–]\s+|\d{1,3}[.)]\s+)/u, "")
    .replace(/^(?:меню|каталог|товары|услуги|позиции|напитки)\s*:\s*/iu, "");
  const currency = /(?:₽|руб(?:лей|ля|ль)?\.?|р\.)\.?$/iu.exec(text);
  if (!currency) return null;
  const beforeCurrency = text.slice(0, currency.index);
  let amountStart = beforeCurrency.length;
  while (amountStart > 0 && /[\d\s.,]/u.test(beforeCurrency[amountStart - 1])) amountStart -= 1;
  const rawAmount = beforeCurrency.slice(amountStart);
  const prefix = beforeCurrency.slice(0, amountStart);
  // No joining a number embedded in a name, guessing a sign, or choosing one of two prices.
  if (!/^\s/u.test(rawAmount) && !/["'»”’:—–]$/u.test(prefix)) return null;
  if (prefix.endsWith("+") || (prefix.endsWith("-") && !/^\s/u.test(rawAmount))) return null;
  const amount = normalizeSpace(rawAmount);
  if (!/^(?:\d{1,3}(?: \d{3})+|\d+)(?:[.,]\d{1,2})?$/u.test(amount)) return null;
  const price = `${amount} ₽`;
  if ([...rawAmount.trim(), ...currency[0]].length > 80 || [...price].length > 80) return null;

  let title = normalizeSpace(prefix.replace(/\s*[:—–-]\s*$/u, ""));
  const quote = QUOTES.get(title[0]);
  if (quote) {
    if (!title.endsWith(quote)) return null;
    title = normalizeSpace(title.slice(1, -1));
  } else if (/["«»“”:/+&]|^[’']|\s+(?:и|или|либо)\s+/iu.test(title)) return null;
  if (!title || [...title].length > 120 || !/\p{L}/u.test(title) || /\p{C}/u.test(title) || NON_ITEM.test(title) || INSTRUCTION.test(title)) return null;

  const [whole, fraction = ""] = amount.replace(/ /gu, "").replace(",", ".").split(".");
  const priceKey = `${whole.replace(/^0+(?=\d)/u, "")}.${fraction.padEnd(2, "0")}`;
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
