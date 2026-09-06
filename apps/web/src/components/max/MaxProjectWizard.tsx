"use client";

import { useEffect, useRef, useState } from "react";
import { Check, Loader2, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { MAX_APP_TYPES, MAX_BRIEF_LENGTH, MAX_FEATURES, MAX_STYLES, type MaxAppTypeId, type MaxFeature, type MaxStyleId } from "@/lib/max-brief";
import "./max-studio.css";

type WizardValues = {
  name: string;
  idea: string;
  appType: MaxAppTypeId;
  audience: string;
  primaryAction: string;
  features: MaxFeature[];
  style: MaxStyleId;
  brandColors: string;
};

const titles = ["Что создаём?", "Для кого приложение?", "Функции и оформление", "Проверьте описание"];

/** Presentation only: the parent owns all answers and the real creation mutation. */
export function MaxProjectWizard({ open, onOpenChange, values, onChange, pending, onSubmit }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  values: WizardValues;
  onChange: (patch: Partial<WizardValues>) => void;
  pending: boolean;
  onSubmit: () => Promise<unknown>;
}) {
  const [step, setStep] = useState(0);
  const [submitting, setSubmitting] = useState(false);
  const submitLock = useRef(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const busy = pending || submitting;
  const length = Array.from(values.idea.trim()).length;
  const ready = values.name.trim().length > 1 && values.idea.trim().length > 9 && length <= MAX_BRIEF_LENGTH;

  useEffect(() => { if (open) heading.current?.focus(); }, [step, open]);

  async function submit() {
    if (submitLock.current || busy || !ready) return;
    submitLock.current = true;
    setSubmitting(true);
    try {
      await onSubmit();
      // Remain locked after success until routing unmounts us; the project exists.
    } catch {
      // The parent mutation owns error feedback. A failed request can be retried.
      submitLock.current = false;
      setSubmitting(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(next) => { if (!busy && !submitLock.current) onOpenChange(next); }}>
      <DialogContent data-max-studio data-busy={busy} className="max-project-wizard" onOpenAutoFocus={(event) => { event.preventDefault(); heading.current?.focus(); }} onEscapeKeyDown={(event) => { if (busy) event.preventDefault(); }} onInteractOutside={(event) => { if (busy) event.preventDefault(); }}>
        <form className="contents" aria-busy={busy} onSubmit={(event) => {
          event.preventDefault();
          if (busy || submitLock.current) return;
          if (step < 3) { if (step !== 0 || ready) setStep(step + 1); }
          else void submit();
        }}>
          <div className="max-wizard-heading">
            <p className="omnia-kicker text-accent">Новое приложение · Шаг {step + 1} из 4</p>
            <DialogTitle ref={heading} tabIndex={-1} className="mt-3 text-2xl">{titles[step]}</DialogTitle>
            <DialogDescription className="mt-2">{step === 0 ? "Название и идея станут основой первой сборки." : step === 3 ? "После подтверждения создадим проект и откроем редактор для первой сборки." : "Можно оставить эти поля пустыми и уточнить их позже."}</DialogDescription>
            <progress className="max-wizard-progress" max={4} value={step + 1} aria-label={`Шаг ${step + 1} из 4`} />
          </div>
          <div className="max-wizard-body">
            {step === 0 && <>
              <div className="space-y-2">
                <Label htmlFor="max-project-name">Название</Label>
                <Input id="max-project-name" value={values.name} onChange={(event) => onChange({ name: event.target.value })} placeholder="Например, Кофе рядом" maxLength={100} required aria-describedby="max-project-name-hint" />
                <p id="max-project-name-hint" className="text-xs text-fg-secondary">Не менее 2 символов.</p>
              </div>
              <div className="space-y-2">
                <Label htmlFor="max-project-idea">Что пользователь сможет делать?</Label>
                <Textarea id="max-project-idea" value={values.idea} onChange={(event) => onChange({ idea: event.target.value })} placeholder="Получать баллы, выбирать награды и оформлять заказ к выдаче" className="min-h-28 resize-y" required aria-describedby="max-project-idea-limit" aria-invalid={length > MAX_BRIEF_LENGTH} />
                <p id="max-project-idea-limit" className="text-xs text-fg-secondary">{length} / {MAX_BRIEF_LENGTH} символов. {length > MAX_BRIEF_LENGTH ? "Сократите описание перед отправкой — текст не обрезан." : "Не менее 10 символов. Описание отправится целиком."}</p>
              </div>
              <fieldset>
                <legend className="text-sm font-medium">Тип приложения</legend>
                <div className="mt-3 grid gap-2 sm:grid-cols-2">
                  {MAX_APP_TYPES.map(item => <button key={item.id} type="button" aria-pressed={values.appType === item.id} onClick={() => onChange({ appType: item.id })} className="max-wizard-option">
                    <span className="flex items-center justify-between gap-2 text-sm font-medium">{item.label}{values.appType === item.id && <Check className="size-4 text-accent" />}</span>
                    <span className="mt-1 block text-xs leading-5 text-fg-secondary">{item.description}</span>
                  </button>)}
                </div>
              </fieldset>
            </>}
            {step === 1 && <>
              <div className="space-y-2"><Label htmlFor="max-audience">Аудитория</Label><Input id="max-audience" value={values.audience} onChange={event => onChange({ audience: event.target.value })} placeholder="Например, постоянные гости кофейни" /></div>
              <div className="space-y-2"><Label htmlFor="max-action">Главное действие</Label><Input id="max-action" value={values.primaryAction} onChange={event => onChange({ primaryAction: event.target.value })} placeholder="Например, обменять баллы на награду" /></div>
            </>}
            {step === 2 && <>
              <fieldset><legend className="text-sm font-medium">Функции</legend><div className="mt-3 flex flex-wrap gap-2">{MAX_FEATURES.map(feature => <button key={feature} type="button" aria-pressed={values.features.includes(feature)} className="max-wizard-option text-xs" onClick={() => onChange({ features: values.features.includes(feature) ? values.features.filter(item => item !== feature) : [...values.features, feature] })}>{feature}</button>)}</div></fieldset>
              <fieldset><legend className="text-sm font-medium">Стиль</legend><div className="mt-3 grid gap-2 sm:grid-cols-3">{MAX_STYLES.map(item => <button key={item.id} type="button" aria-pressed={values.style === item.id} className="max-wizard-option text-sm" onClick={() => onChange({ style: item.id })}>{item.label}</button>)}</div></fieldset>
              <div className="space-y-2"><Label htmlFor="max-brand">Цвета бренда</Label><Input id="max-brand" value={values.brandColors} onChange={event => onChange({ brandColors: event.target.value })} placeholder="#2563eb, графит, молочный" /></div>
            </>}
            {step === 3 && <dl className="max-wizard-review">
              {[
                ["Название", values.name], ["Идея", values.idea],
                ["Тип приложения", MAX_APP_TYPES.find(item => item.id === values.appType)?.label],
                ["Аудитория", values.audience || "Уточним позже"], ["Главное действие", values.primaryAction || "Уточним позже"],
                ["Функции", values.features.join(", ") || "Без дополнительных функций"],
                ["Стиль", MAX_STYLES.find(item => item.id === values.style)?.label], ["Цвета бренда", values.brandColors || "Подобрать автоматически"],
              ].map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}
            </dl>}
          </div>
          <footer className="max-wizard-footer">
            <Button type="button" variant="outline" disabled={busy} onClick={() => step === 0 ? onOpenChange(false) : setStep(step - 1)}>{step === 0 ? "Отмена" : "Назад"}</Button>
            <Button type="submit" disabled={busy || ((step === 0 || step === 3) && !ready)}>
              {step === 3 && (busy ? <Loader2 className="size-4 animate-spin" /> : <Sparkles className="size-4" />)}
              {step === 3 ? "Создать проект" : "Далее"}
            </Button>
          </footer>
        </form>
      </DialogContent>
    </Dialog>
  );
}
