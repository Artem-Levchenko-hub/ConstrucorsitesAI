import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/**
 * Merge Tailwind classes with conflict resolution. The one helper every
 * component uses for conditional / overridable class names:
 *
 *   cn("px-4 py-2", isActive && "bg-primary", className)
 *
 * `clsx` flattens conditions/arrays/objects; `twMerge` makes the LAST
 * conflicting utility win (so a caller's `className` can always override a
 * component's defaults — e.g. `cn("p-4", "p-6")` → `"p-6"`).
 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

/** Format a number as RUB without forcing decimals (1 234 ₽, 1 234,50 ₽). */
export function formatRub(value: number): string {
  return new Intl.NumberFormat("ru-RU", {
    style: "currency",
    currency: "RUB",
    maximumFractionDigits: Number.isInteger(value) ? 0 : 2,
  }).format(value);
}

/** Human date in Russian (12 июн 2026). Accepts ISO string, Date, or epoch ms. */
export function formatDate(value: string | number | Date | null | undefined): string {
  if (value == null || value === "") return "—";
  const d = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(d.getTime())) return "—";
  return new Intl.DateTimeFormat("ru-RU", {
    day: "numeric",
    month: "short",
    year: "numeric",
  }).format(d);
}

/**
 * Parse a possibly-missing/invalid date value into a Date, or null. NEVER throws.
 * Entity date fields are often optional ("" / null / garbage) — calling
 * `new Date(x).toISOString()` on such a value throws `RangeError: Invalid time
 * value` and crashes the whole page. Use this for any computation on a date.
 */
export function parseDate(value: string | number | Date | null | undefined): Date | null {
  if (value == null || value === "") return null;
  const d = value instanceof Date ? value : new Date(value);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** Safe `YYYY-MM-DD` key, or null on missing/invalid date. Never throws. */
export function toISODate(value: string | number | Date | null | undefined): string | null {
  const d = parseDate(value);
  return d ? d.toISOString().slice(0, 10) : null;
}

/** Safe `YYYY-MM` bucket for grouping/charting by month, or null. Never throws. */
export function monthKey(value: string | number | Date | null | undefined): string | null {
  const d = parseDate(value);
  return d ? d.toISOString().slice(0, 7) : null;
}

/** First letters for an avatar fallback ("Иван Петров" → "ИП"). */
export function initials(name: string | null | undefined): string {
  if (!name) return "?";
  return name
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase() ?? "")
    .join("");
}
