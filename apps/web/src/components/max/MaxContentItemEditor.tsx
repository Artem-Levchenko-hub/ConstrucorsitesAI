"use client";

import { useRef, useState } from "react";
import { ImagePlus, Loader2, Trash2 } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { uploadMaxContentImage } from "@/lib/api/max-studio";
import { ApiError } from "@/lib/api/client";
import type { MaxContentAvailability, MaxContentItem } from "@/lib/api/types";

const AVAILABILITY: Array<{ id: MaxContentAvailability; label: string; hint: string }> = [
  { id: "in_stock", label: "В наличии", hint: "Можно заказать прямо сейчас" },
  { id: "on_request", label: "Под заказ", hint: "Привезём или подтвердим позже" },
  { id: "out_of_stock", label: "Нет в наличии", hint: "Показываем, но заказать нельзя" },
];

const MAX_IMAGE_BYTES = 8 * 1024 * 1024;

/** Так позиция будет выглядеть в приложении — рядом с полями, а не на словах. */
export function MaxContentCardPreview({ item }: { item: MaxContentItem }) {
  const availability = AVAILABILITY.find(option => option.id === item.availability) ?? AVAILABILITY[0];
  return (
    <div className="max-content-preview" data-inactive={!item.active}>
      <p className="max-content-preview__caption">Как увидит пользователь</p>
      <div className="max-content-card">
        {item.image_url
          ? /* eslint-disable-next-line @next/next/no-img-element */
            <img className="max-content-card__photo" src={item.image_url} alt="" />
          : <div className="max-content-card__photo max-content-card__photo--empty"><ImagePlus aria-hidden="true" /><span>Без фото</span></div>}
        <div className="max-content-card__body">
          {item.category && <span className="max-content-card__category">{item.category}</span>}
          <p className="max-content-card__title">{item.title || "Название позиции"}</p>
          {item.description && <p className="max-content-card__description">{item.description}</p>}
          {item.options.length > 0 && <div className="max-content-card__options">
            {item.options.slice(0, 6).map(option => <span key={option}>{option}</span>)}
          </div>}
          <div className="max-content-card__footer">
            <span className="max-content-card__price">{item.price || "Цена не указана"}</span>
            <span className="max-content-card__availability" data-state={item.availability}>{availability.label}</span>
          </div>
          <span className="max-content-card__action" data-disabled={item.availability === "out_of_stock"}>
            {item.action_label || "Открыть"}
          </span>
        </div>
      </div>
      {!item.active && <p className="max-content-preview__hidden">Позиция скрыта — в приложении её не будет</p>}
    </div>
  );
}

/** Одна позиция каталога: поля слева, живое превью карточки справа. */
export function MaxContentItemFields({ projectId, index, item, onPatch }: {
  projectId: string;
  index: number;
  item: MaxContentItem;
  onPatch: (patch: Partial<MaxContentItem>) => void;
}) {
  const fileInput = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const fieldId = `max-content-${index}`;

  async function upload(file: File) {
    setUploadError(null);
    if (!file.type.startsWith("image/")) {
      setUploadError("Подойдёт JPG, PNG, WebP или GIF");
      return;
    }
    if (file.size > MAX_IMAGE_BYTES) {
      setUploadError("Файл больше 8 МБ — уменьшите изображение");
      return;
    }
    setUploading(true);
    try {
      const { url } = await uploadMaxContentImage(projectId, file);
      onPatch({ image_url: url });
    } catch (error) {
      setUploadError(error instanceof ApiError ? error.message : "Не удалось загрузить фото");
    } finally {
      setUploading(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  }

  return (
    <div className="max-content-item-layout">
      <div className="max-setup-grid">
        <div className="max-setup-field max-setup-wide">
          <Label htmlFor={`${fieldId}-title`}>Название</Label>
          <Input id={`${fieldId}-title`} aria-label={`Название элемента ${index + 1}`} value={item.title}
            placeholder="Например, Худи оверсайз" onChange={event => onPatch({ title: event.target.value })} />
        </div>

        <div className="max-setup-field max-setup-wide">
          <span className="max-content-label">Фото позиции</span>
          <div className="max-content-photo">
            <button type="button" className="max-content-upload" disabled={uploading}
              onClick={() => fileInput.current?.click()}>
              {uploading ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <ImagePlus className="h-4 w-4" aria-hidden="true" />}
              {uploading ? "Загружаем…" : item.image_url ? "Заменить фото" : "Загрузить фото"}
            </button>
            {item.image_url && <button type="button" className="max-setup-delete" onClick={() => onPatch({ image_url: "" })}>
              <Trash2 className="h-4 w-4" aria-hidden="true" /><span>Убрать фото</span>
            </button>}
            <input ref={fileInput} type="file" accept="image/png,image/jpeg,image/webp,image/gif" className="sr-only"
              aria-label={`Фото элемента ${index + 1}`}
              onChange={event => { const file = event.target.files?.[0]; if (file) void upload(file); }} />
          </div>
          <p className="max-setup-hint" data-invalid={Boolean(uploadError)} role={uploadError ? "alert" : undefined}>
            {uploadError ?? "JPG, PNG, WebP или GIF до 8 МБ."}
          </p>
          <Label htmlFor={`${fieldId}-image`}>Или ссылка на фото</Label>
          <Input id={`${fieldId}-image`} aria-label={`Ссылка на фото элемента ${index + 1}`} value={item.image_url}
            inputMode="url" placeholder="https://..." onChange={event => onPatch({ image_url: event.target.value })} />
        </div>

        <div className="max-setup-field">
          <Label htmlFor={`${fieldId}-category`}>Раздел каталога</Label>
          <Input id={`${fieldId}-category`} aria-label={`Раздел элемента ${index + 1}`} value={item.category} maxLength={80}
            placeholder="Например, Женское" onChange={event => onPatch({ category: event.target.value })} />
          <p className="max-setup-hint">Позиции с одинаковым разделом приложение соберёт в одну группу.</p>
        </div>

        <div className="max-setup-field">
          <Label htmlFor={`${fieldId}-price`}>Цена или подпись</Label>
          <Input id={`${fieldId}-price`} aria-label={`Цена или подпись элемента ${index + 1}`} value={item.price} maxLength={80}
            placeholder="Например, 3 900 ₽" onChange={event => onPatch({ price: event.target.value })} />
        </div>

        <div className="max-setup-field max-setup-wide">
          <span className="max-content-label" id={`${fieldId}-availability-label`}>Наличие</span>
          <div className="max-content-availability" role="group" aria-labelledby={`${fieldId}-availability-label`}>
            {AVAILABILITY.map(option => <button key={option.id} type="button" aria-pressed={item.availability === option.id}
              onClick={() => onPatch({ availability: option.id })}>
              <span>{option.label}</span><small>{option.hint}</small>
            </button>)}
          </div>
        </div>

        <div className="max-setup-field max-setup-wide">
          <Label htmlFor={`${fieldId}-options`}>Варианты выбора</Label>
          <Input id={`${fieldId}-options`} aria-label={`Варианты элемента ${index + 1}`} value={item.options.join(", ")}
            placeholder="S, M, L, XL"
            onChange={event => onPatch({ options: event.target.value.split(",").map(option => option.trim()).filter(Boolean).slice(0, 24) })} />
          <p className="max-setup-hint">Размеры, объёмы или длительности через запятую. Пользователь выберет вариант перед заказом.</p>
        </div>

        <div className="max-setup-field max-setup-wide">
          <Label htmlFor={`${fieldId}-description`}>Описание</Label>
          <Textarea id={`${fieldId}-description`} aria-label={`Описание элемента ${index + 1}`} className="max-setup-short-textarea"
            value={item.description} placeholder="Что важно знать об этой позиции?"
            onChange={event => onPatch({ description: event.target.value })} />
        </div>

        <div className="max-setup-field">
          <Label htmlFor={`${fieldId}-action`}>Текст кнопки</Label>
          <Input id={`${fieldId}-action`} aria-label={`Текст кнопки элемента ${index + 1}`} value={item.action_label}
            maxLength={40} placeholder="Открыть" onChange={event => onPatch({ action_label: event.target.value })} />
        </div>
      </div>

      <MaxContentCardPreview item={item} />
    </div>
  );
}
