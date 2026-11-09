"use client";

import { useId } from "react";
import { X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { MaxContentItem } from "@/lib/api/types";
import { newMaxContentItem } from "@/lib/max-catalog";
import "./max-initial-catalog.css";

type MaxInitialCatalogProps = {
  items: MaxContentItem[];
  onChange: (items: MaxContentItem[]) => void;
  onReset: () => void;
  pending: boolean;
  tooManyItems: boolean;
  descriptionChanged: boolean;
  error?: string | null;
};

/** Edits only the reviewed draft; creation and catalog recognition belong to the parent. */
export function MaxInitialCatalog({
  items, onChange, onReset, pending, tooManyItems, descriptionChanged, error,
}: MaxInitialCatalogProps) {
  const id = useId();
  const overLimit = tooManyItems || items.length > 100;
  const message = error || (overLimit
    ? "Можно сохранить не более 100 позиций. Сократите описание и распознайте заново или начните с пустого каталога."
    : null);

  function changeItems(next: MaxContentItem[]) {
    if (!pending) onChange(next);
  }

  function editItem(itemId: string, patch: Partial<Pick<MaxContentItem, "title" | "price">>) {
    changeItems(items.map(item => item.id === itemId ? { ...item, ...patch } : item));
  }

  return (
    <section className="max-initial-catalog" aria-labelledby={`${id}-heading`} aria-busy={pending}>
      <header className="max-initial-catalog__heading">
        <h3 id={`${id}-heading`}>Каталог приложения</h3>
        <p id={`${id}-description`}>Эти позиции сохраним в админке до первой сборки. Проверьте названия и цены. Для распознавания укажите цену каждой позиции: «Латте — 300 ₽; Капучино — 200 ₽».</p>
      </header>

      {descriptionChanged && (
        <p className="max-initial-catalog__notice" role="status">
          Описание изменилось. Проверьте список или распознайте заново.
        </p>
      )}

      {items.length === 0 ? (
        <p className="max-initial-catalog__empty">
          Каталог начнёт работу без позиций. Можно добавить их здесь или позже в админке.
        </p>
      ) : (
        <ol className="max-initial-catalog__rows" aria-describedby={`${id}-description ${id}-limits`}>
          {items.map((item, index) => (
            <li className="max-initial-catalog__row" key={item.id}>
              <div className="max-initial-catalog__field">
                <Label htmlFor={`${id}-${item.id}-title`}>Название позиции {index + 1}</Label>
                <Input id={`${id}-${item.id}-title`} value={item.title} maxLength={120}
                  disabled={pending} placeholder="Например, латте"
                  onChange={event => editItem(item.id, { title: event.target.value })} />
              </div>
              <div className="max-initial-catalog__field">
                <Label htmlFor={`${id}-${item.id}-price`}>Цена позиции {index + 1}</Label>
                <Input id={`${id}-${item.id}-price`} value={item.price} maxLength={80}
                  disabled={pending} placeholder="Например, 300 ₽"
                  onChange={event => editItem(item.id, { price: event.target.value })} />
              </div>
              <Button type="button" variant="ghost" disabled={pending}
                className="max-initial-catalog__remove" aria-label={`Убрать позицию ${index + 1}`}
                onClick={() => changeItems(items.filter(current => current.id !== item.id))}>
                <X aria-hidden="true" />Убрать
              </Button>
            </li>
          ))}
        </ol>
      )}

      <div className="max-initial-catalog__limits" id={`${id}-limits`}>
        <p className="max-initial-catalog__count" aria-live="polite">Позиций: {items.length} из 100</p>
        <p>Название — до 120 символов, цена — до 80. Цену можно оставить пустой.</p>
      </div>
      {message && <p className="max-initial-catalog__error" role="alert">{message}</p>}

      <div className="max-initial-catalog__actions">
        <Button type="button" variant="outline" className="max-initial-catalog__button"
          disabled={pending || overLimit || items.length >= 100}
          onClick={() => {
            if (!pending && !overLimit && items.length < 100) changeItems([...items, newMaxContentItem()]);
          }}>Добавить позицию</Button>
        <Button type="button" variant="ghost" className="max-initial-catalog__button" disabled={pending}
          onClick={() => { if (!pending) onReset(); }}>Распознать заново</Button>
        <Button type="button" variant="ghost" className="max-initial-catalog__button" disabled={pending}
          onClick={() => changeItems([])}>Начать с пустого каталога</Button>
      </div>
    </section>
  );
}
