"use client";

import { useId, useRef, useState } from "react";
import { X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { FONT_FILE_ACCEPT, FONT_ROLE_LABELS } from "@/lib/api/font-assets";
import type { FontRole } from "@/lib/api/types";
import type { useFontAttachments } from "@/hooks/useFontAttachments";

type Props = { fonts: ReturnType<typeof useFontAttachments>; disabled: boolean };

export function FontAttachmentPanel({ fonts, disabled }: Props) {
  const [name, setName] = useState("");
  const [role, setRole] = useState<FontRole>("all");
  const files = useRef<HTMLInputElement>(null);
  const id = useId();
  const locked = disabled || fonts.busy;
  const addName = () => { if (!locked && name.trim()) void fonts.addName(name, role); };
  return <section className="space-y-3 rounded-lg border border-border-subtle bg-surface-raised p-3" aria-label="Добавление шрифта">
    <p className="text-xs text-fg-secondary">Укажите бесплатный шрифт из каталога, ссылку Google Fonts / Fontsource или загрузите свой файл до 8 МБ.</p>
    <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_auto]">
      <div className="min-w-0"><label htmlFor={`${id}-name`} className="mb-1 block text-xs text-fg-secondary">Название шрифта или ссылка</label>
        <input id={`${id}-name`} aria-label="Название шрифта или ссылка" value={name} maxLength={500} disabled={locked}
          placeholder="Например, Manrope" className="min-h-11 w-full min-w-0 rounded border border-border-default bg-surface-input px-3 text-sm text-fg-primary"
          onChange={event => setName(event.target.value)} onKeyDown={event => { if (event.key === "Enter") { event.preventDefault(); event.stopPropagation(); addName(); } }} /></div>
      <div><label htmlFor={`${id}-role`} className="mb-1 block text-xs text-fg-secondary">Где использовать</label>
        <select id={`${id}-role`} aria-label="Роль шрифта" value={role} disabled={locked} onChange={event => setRole(event.target.value as FontRole)}
          className="min-h-11 w-full rounded border border-border-default bg-surface-input px-2 text-sm text-fg-primary">
          {Object.entries(FONT_ROLE_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select></div>
    </div>
    <div className="flex flex-wrap gap-2">
      <Button type="button" size="sm" variant="secondary" disabled={locked || !name.trim()} onClick={addName} className="min-h-11">Добавить шрифт</Button>
      <input ref={files} type="file" multiple accept={FONT_FILE_ACCEPT} aria-label="Выбрать файл шрифта" tabIndex={-1} className="sr-only"
        disabled={locked} onChange={event => { const selected = Array.from(event.target.files ?? []); event.target.value = ""; if (!locked) void fonts.addFiles(selected, role); }} />
      <Button type="button" size="sm" variant="secondary" disabled={locked} onClick={() => files.current?.click()} className="min-h-11">Загрузить файл</Button>
    </div>
    <p className="text-[11px] text-fg-tertiary">TTF, OTF, WOFF, WOFF2 · до 6 вложений. Применим после отправки запроса.</p>
  </section>;
}

export function FontAttachmentChips({ fonts, disabled }: Props) {
  if (!fonts.items.length) return null;
  return <ul aria-label="Шрифты к запросу" className="flex flex-wrap gap-2">
    {fonts.items.map(item => <li key={item.key} className="max-w-full rounded-lg border border-border-subtle bg-surface-raised p-2 text-xs">
      <div className="flex items-center gap-2"><span className="min-w-0 break-all font-medium text-fg-primary">{item.asset?.family ?? item.file?.name ?? item.familyName}</span>
        <button type="button" aria-label={`Удалить шрифт ${item.asset?.family ?? item.file?.name ?? item.familyName}`} disabled={disabled || fonts.busy}
          onClick={() => fonts.remove(item.key)} className="flex h-11 w-11 shrink-0 items-center justify-center rounded"><X aria-hidden="true" className="h-4 w-4" /></button></div>
      <p className="text-fg-secondary">{FONT_ROLE_LABELS[item.role]}{item.asset ? ` · ${item.asset.weight}${item.asset.style === "italic" ? " · курсив" : ""}` : ""}</p>
      {item.error ? <><p role="alert" className="mt-1 max-w-80 text-danger">{item.error}</p><button type="button" disabled={disabled || fonts.busy} onClick={() => void fonts.retry(item.key)} className="min-h-11 underline">Повторить загрузку шрифта</button></>
        : !item.asset ? <span role="status" className="text-fg-secondary">Проверяем шрифт…</span> : null}
    </li>)}
  </ul>;
}
