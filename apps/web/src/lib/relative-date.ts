/**
 * Дата по-человечески: «сегодня в 14:20», «вчера в 09:05», «2 дня назад».
 *
 * В списке приложений стояло «Обновлён 26.09.2026»: владельцу приходится
 * сравнивать цифры с сегодняшним числом, чтобы понять, работал он над проектом
 * утром или в прошлом месяце. Ближние даты называем словами, дальние — числом
 * и месяцем, а прошлый год дополняем годом, чтобы не путать.
 */

const MONTHS = [
  "января", "февраля", "марта", "апреля", "мая", "июня",
  "июля", "августа", "сентября", "октября", "ноября", "декабря",
];

function startOfDay(value: Date): number {
  return new Date(value.getFullYear(), value.getMonth(), value.getDate()).getTime();
}

function time(value: Date): string {
  return `${String(value.getHours()).padStart(2, "0")}:${String(value.getMinutes()).padStart(2, "0")}`;
}

function daysWord(days: number): string {
  const tail = days % 100 >= 11 && days % 100 <= 14 ? 0 : days % 10;
  if (tail === 1) return "день";
  if (tail >= 2 && tail <= 4) return "дня";
  return "дней";
}

/** `null` для нечитаемой даты — строку с датой тогда просто не показываем. */
export function relativeDateLabel(iso: string | null | undefined, now = new Date()): string | null {
  if (!iso) return null;
  const value = new Date(iso);
  if (Number.isNaN(value.getTime())) return null;

  const days = Math.round((startOfDay(now) - startOfDay(value)) / 86_400_000);
  if (days < 0) return `${value.getDate()} ${MONTHS[value.getMonth()]}`;
  if (days === 0) return `сегодня в ${time(value)}`;
  if (days === 1) return `вчера в ${time(value)}`;
  if (days < 7) return `${days} ${daysWord(days)} назад`;
  if (value.getFullYear() === now.getFullYear()) {
    return `${value.getDate()} ${MONTHS[value.getMonth()]}`;
  }
  return `${value.getDate()} ${MONTHS[value.getMonth()]} ${value.getFullYear()}`;
}
