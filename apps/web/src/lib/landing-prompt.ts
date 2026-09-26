/**
 * Запрос, написанный на лендинге до регистрации.
 *
 * Человек формулирует задачу в поле на главной, но проект создать ещё не может —
 * сначала регистрация, иногда с подтверждением почты. Между этими шагами текст
 * должен где-то пережить и переход, и закрытую вкладку, иначе человек напишет
 * его заново или (чаще) не напишет вовсе.
 *
 * Поэтому храним в localStorage, а не в адресе страницы: подтверждение почты
 * приходит письмом и открывается новой вкладкой, а иногда и через час — адрес
 * туда не доедет. Срок жизни ограничен сутками: запрос месячной давности,
 * всплывший при следующем входе, будет для человека загадкой, а не помощью.
 */

import { MAX_BRIEF_LENGTH } from "@/lib/max-brief";

const KEY = "yleum:landing-prompt";
const MAX_AGE_MS = 24 * 60 * 60 * 1000;

/**
 * Столько же, сколько принимает поле идеи в мастере создания проекта
 * (`MAX_BRIEF_LENGTH`). Числа должны совпадать: если резать здесь строже, текст
 * молча потеряет хвост по дороге, и человек увидит в мастере обрывок своей же
 * формулировки, не понимая, кто её укоротил.
 */
export const LANDING_PROMPT_MAX_LENGTH = MAX_BRIEF_LENGTH;

type Stored = { text: string; savedAt: number };

function storage(): Storage | null {
  // В приватном окне и при запрещённых данных сайта обращение само бросает
  // исключение — не на чтении значения, а на самом доступе к хранилищу.
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

export function saveLandingPrompt(text: string, now: number = Date.now()): boolean {
  const trimmed = text.trim();
  if (!trimmed) return false;
  const store = storage();
  if (!store) return false;
  try {
    const value: Stored = { text: trimmed.slice(0, LANDING_PROMPT_MAX_LENGTH), savedAt: now };
    store.setItem(KEY, JSON.stringify(value));
    return true;
  } catch {
    // Переполненное хранилище не повод ронять переход на регистрацию:
    // человек просто опишет задачу ещё раз в мастере.
    return false;
  }
}

/**
 * Возвращает сохранённый запрос и СРАЗУ его удаляет: он предназначен ровно для
 * одного проекта. Иначе он всплывал бы при каждом следующем создании.
 */
export function takeLandingPrompt(now: number = Date.now()): string | null {
  const store = storage();
  if (!store) return null;
  let raw: string | null = null;
  try {
    raw = store.getItem(KEY);
  } catch {
    return null;
  }
  if (!raw) return null;
  try {
    store.removeItem(KEY);
  } catch {
    // Не смогли убрать — не беда: проверка срока не даст ему всплывать вечно.
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (typeof parsed !== "object" || parsed === null) return null;
  const { text, savedAt } = parsed as Partial<Stored>;
  if (typeof text !== "string" || typeof savedAt !== "number") return null;
  if (!Number.isFinite(savedAt) || now - savedAt > MAX_AGE_MS || savedAt > now) return null;
  const trimmed = text.trim();
  return trimmed ? trimmed.slice(0, LANDING_PROMPT_MAX_LENGTH) : null;
}

/**
 * Что происходит по нажатию стрелки на витрине.
 *
 * Вынесено из компонента, потому что здесь принимается решение, которое стоит
 * денег: потерять текст — значит потерять человека, а отказать ему из-за
 * пустого поля — значит не пустить на регистрацию того, кто просто хочет
 * зарегистрироваться. Переход происходит ВСЕГДА, сохранение — только если есть
 * что сохранять, и неудача сохранения переходу не мешает.
 */
export function startWithPrompt(value: string, navigate: (href: string) => void): void {
  if (value.trim()) saveLandingPrompt(value);
  navigate("/max/register");
}
