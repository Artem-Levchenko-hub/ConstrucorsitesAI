import type { MaxContentItem } from "./api/types";
import { redactChatSecrets } from "./max-chat-credentials";

/** Public brief and catalog must use the same redaction before persistence. */
export function publicMaxBriefText(value: string): string {
  return redactChatSecrets(value).replaceAll(
    "[ключ сохранён в Yleum]", "[секрет удалён из описания]",
  ).trim();
}

export function publicInitialCatalog(items: readonly MaxContentItem[]): MaxContentItem[] {
  return items.map(item => ({
    ...item,
    title: publicMaxBriefText(item.title),
    price: publicMaxBriefText(item.price),
    category: publicMaxBriefText(item.category),
    description: publicMaxBriefText(item.description),
    image_url: publicMaxBriefText(item.image_url),
    action_label: publicMaxBriefText(item.action_label),
    options: item.options.map(publicMaxBriefText),
  }));
}

/** Match the existing API contract; never trim away an invalid position. */
export function initialCatalogError(items: readonly MaxContentItem[], tooManyItems = false): string | null {
  if (tooManyItems || items.length > 100) {
    return "В каталоге может быть не больше 100 позиций. Сократите описание и распознайте заново или начните с пустого каталога.";
  }
  if (items.some(item => !item.title.trim())) return "Укажите название каждой позиции или уберите пустую строку.";
  if (items.some(item => Array.from(item.title).length > 120 || Array.from(item.price).length > 80)) {
    return "Название позиции — до 120 символов, цена — до 80. Сократите текст перед созданием.";
  }
  if (new Set(items.map(item => item.id)).size !== items.length) return "Позиции повторяются. Уберите повтор и добавьте его заново.";
  return null;
}
