import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
import { formatDistanceToNow } from "date-fns";
import { ru } from "date-fns/locale";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatRub(amount: number): string {
  return new Intl.NumberFormat("ru-RU", {
    style: "currency",
    currency: "RUB",
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  }).format(amount);
}

export function formatRelativeTime(iso: string): string {
  return formatDistanceToNow(new Date(iso), { locale: ru, addSuffix: true });
}

export function shortSha(commitSha: string): string {
  return commitSha.slice(0, 7);
}
