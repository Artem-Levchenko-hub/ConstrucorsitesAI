"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { Check, Eye, Loader2, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { MAX_APP_TYPES, MAX_BRIEF_LENGTH, MAX_FEATURES, MAX_FEATURE_INFO, MAX_MANAGEMENT_MODULES, briefManagementModuleOptOuts, defaultManagementModules, newProjectManagementModules, selectedMaxAppTypes, type MaxAppTypeId, type MaxFeature, type MaxProjectBrief } from "@/lib/max-brief";
import { MaxFeaturePreview } from "./MaxFeaturePreview";
import { MaxProjectReview } from "./MaxProjectReview";
import "./max-studio.css";

type WizardValues = Omit<MaxProjectBrief, "catalogItemCount">;

const titles = ["Что создаём?", "Для кого приложение?", "Функции и оформление", "Проверьте описание"];

/** Presentation only: the parent owns all answers and the real creation mutation. */
export function MaxProjectWizard({ open, onOpenChange, values, onChange, pending, onSubmit, submitDisabled = false, quotaNotice, initialCatalog }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  values: WizardValues;
  onChange: (patch: Partial<WizardValues>) => void;
  pending: boolean;
  submitDisabled?: boolean;
  quotaNotice?: ReactNode;
  initialCatalog?: ReactNode;
  onSubmit: () => Promise<unknown>;
}) {
  const [step, setStep] = useState(0);
  const [pinnedFeature, setPinnedFeature] = useState<MaxFeature | null>(null);
  const [hoveredFeature, setHoveredFeature] = useState<MaxFeature | null>(null);
  const [detailsRevealed, setDetailsRevealed] = useState(false);
  const previewedFeature = hoveredFeature ?? pinnedFeature;
  const [submitting, setSubmitting] = useState(false);
  const submitLock = useRef(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const detailsField = useRef<HTMLTextAreaElement>(null);
  const busy = pending || submitting;
  const appTypes = selectedMaxAppTypes(values.appType, values.appTypes);
  const multipleTypes = values.appTypes !== undefined;
  const customSelected = appTypes.includes("custom");
  const showDetails = customSelected || detailsRevealed || values.idea.length > 0;
  const briefOptOuts = briefManagementModuleOptOuts(values.idea);
  const moduleOptOuts = values.managementModuleOptOuts ?? [];
  // Keep the draft selections when the catalog temporarily hides dependent modules.
  const draftManagementModules = values.managementModules ?? defaultManagementModules(values.appType, values.appTypes);
  const managementModules = newProjectManagementModules(
    values.appType, draftManagementModules, values.appTypes, [...moduleOptOuts, ...briefOptOuts],
  );
  const length = Array.from(values.idea.trim()).length;
  const ideaTooShort = customSelected && length < 10;
  const ideaTooLong = length > MAX_BRIEF_LENGTH;
  const ready = values.name.trim().length > 1 && !ideaTooShort && !ideaTooLong;

  useEffect(() => { if (open) heading.current?.focus(); }, [step, open]);
  useEffect(() => { if (detailsRevealed) detailsField.current?.focus(); }, [detailsRevealed]);

  function selectAppType(type: MaxAppTypeId) {
    if (!multipleTypes) {
      onChange({ appType: type });
      return;
    }
    const selected = appTypes.includes(type);
    if (selected && appTypes.length === 1) return;
    const next = type === "custom" || customSelected
      ? [type]
      : selected ? appTypes.filter(current => current !== type) : [...appTypes, type];
    const defaults = selected ? [] : defaultManagementModules(type);
    onChange({
      appType: next[0],
      appTypes: next,
      managementModules: newProjectManagementModules(
        next[0], [...draftManagementModules, ...defaults], next,
      ),
    });
  }

  async function submit() {
    if (submitLock.current || busy || submitDisabled || !ready) return;
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
      <DialogContent
        {...(step === 0 ? { "aria-describedby": undefined } : {})}
        data-max-studio
        data-busy={busy}
        className="max-project-wizard"
        onOpenAutoFocus={(event) => { event.preventDefault(); heading.current?.focus(); }}
        onEscapeKeyDown={(event) => { if (busy) event.preventDefault(); }}
        onInteractOutside={(event) => { if (busy) event.preventDefault(); }}
      >
        <form className="contents" aria-busy={busy} onSubmit={(event) => {
          event.preventDefault();
          if (busy || submitLock.current) return;
          if (step < 3) { if (step !== 0 || ready) setStep(step + 1); }
          else void submit();
        }}>
          <div className="max-wizard-heading">
            <p className="omnia-kicker text-accent">Новое приложение · Шаг {step + 1} из 4</p>
            <DialogTitle ref={heading} tabIndex={-1} className="mt-3 text-2xl">{titles[step]}</DialogTitle>
            {step > 0 && (
              <DialogDescription className="mt-2">
                {step === 3
                  ? "После подтверждения создадим проект и откроем редактор для первой сборки."
                  : "Можно оставить эти поля пустыми и уточнить их позже."}
              </DialogDescription>
            )}
            <progress className="max-wizard-progress" max={4} value={step + 1} aria-label={`Шаг ${step + 1} из 4`} />
          </div>
          {quotaNotice}
          <div className="max-wizard-body">
            {step === 0 && <>
              <div className="space-y-2">
                <Label htmlFor="max-project-name">Название</Label>
                <Input id="max-project-name" value={values.name} onChange={(event) => onChange({ name: event.target.value })} placeholder="Например, Кофе рядом" maxLength={100} required aria-describedby="max-project-name-hint" />
                <p id="max-project-name-hint" className="text-xs text-fg-secondary">Не менее 2 символов</p>
              </div>
              <fieldset>
                <legend className="text-sm font-medium">Что пользователь сможет делать?</legend>
                {multipleTypes && <p className="max-wizard-note">Выберите хотя бы один</p>}
                <div className="mt-3 grid gap-2 sm:grid-cols-2">
                  {MAX_APP_TYPES.map(item => <button key={item.id} type="button" aria-pressed={appTypes.includes(item.id)} onClick={() => selectAppType(item.id)} className="max-wizard-option">
                    <span className="flex items-center justify-between gap-2 text-sm font-medium">{item.label}{appTypes.includes(item.id) && <Check className="size-4 text-accent" />}</span>
                    <span className="mt-1 block text-xs leading-5 text-fg-secondary">{item.description}</span>
                  </button>)}
                </div>
                {!showDetails && (
                  <Button type="button" variant="outline" className="mt-3" aria-expanded={false}
                    aria-controls="max-project-details" onClick={() => setDetailsRevealed(true)}>
                    Добавить детали
                  </Button>
                )}
                <div id="max-project-details" className="mt-3 space-y-2" hidden={!showDetails}>
                  {showDetails && <>
                    <Label htmlFor="max-project-idea">
                      {customSelected ? "Опишите свою идею" : "Детали сценария (необязательно)"}
                    </Label>
                    <Textarea
                      ref={detailsField}
                      id="max-project-idea"
                      value={values.idea}
                      onChange={(event) => {
                        setDetailsRevealed(true);
                        onChange({ idea: event.target.value });
                      }}
                      placeholder="Например, получать баллы, выбирать награды и оформлять заказ к выдаче"
                      className="min-h-28 resize-y"
                      required={customSelected}
                      aria-describedby="max-project-idea-limit"
                      aria-invalid={ideaTooLong || (ideaTooShort && length > 0)}
                    />
                    <p id="max-project-idea-limit" className="text-xs text-fg-secondary">
                      {length} / {MAX_BRIEF_LENGTH} символов
                      {ideaTooShort && <span className="block">Не менее 10 символов</span>}
                      {ideaTooLong && <span className="block">Сократите описание перед отправкой</span>}
                    </p>
                  </>}
                </div>
              </fieldset>
            </>}
            {step === 1 && <>
              <div className="space-y-2"><Label htmlFor="max-audience">Аудитория</Label><Input id="max-audience" value={values.audience} onChange={event => onChange({ audience: event.target.value })} placeholder="Например, постоянные гости кофейни" /></div>
              <div className="space-y-2"><Label htmlFor="max-action">Главное действие</Label><Input id="max-action" value={values.primaryAction} onChange={event => onChange({ primaryAction: event.target.value })} placeholder="Например, обменять баллы на награду" /></div>
            </>}
            {step === 2 && <>
              <fieldset>
                <legend className="text-sm font-medium">Управление в Yleum</legend>
                <p className="max-wizard-note">Выберите нужные модули. Для заказов и записи нужен каталог.</p>
                {briefOptOuts.length > 0 && <p className="max-wizard-note">Исключения из описания учтены в выборе модулей.</p>}
                <div className="mt-3 grid gap-2 sm:grid-cols-2">
                  {MAX_MANAGEMENT_MODULES.map(module => <label key={module.id} className="max-wizard-option flex items-start gap-3">
                    <input type="checkbox" className="mt-1 size-4" checked={managementModules.includes(module.id)}
                      disabled={briefOptOuts.includes(module.id) || (
                        (module.id === "orders" || module.id === "bookings") &&
                        !managementModules.includes("catalog")
                      )}
                      onChange={event => {
                        const selected = new Set(draftManagementModules);
                        const excluded = new Set(moduleOptOuts);
                        if (event.target.checked) {
                          selected.add(module.id);
                          excluded.delete(module.id);
                        } else {
                          selected.delete(module.id);
                          excluded.add(module.id);
                        }
                        onChange({ managementModules: [...selected], managementModuleOptOuts: [...excluded] });
                      }} />
                    <span><span className="block text-sm font-medium">{module.label}</span><span className="mt-1 block text-xs leading-5 text-fg-secondary">{module.description}</span></span>
                  </label>)}
                </div>
              </fieldset>
              <fieldset>
                <legend className="text-sm font-medium">Функции</legend>
                <p className="max-wizard-note">Нажмите на функцию, чтобы добавить её в приложение. Кнопка <Eye aria-hidden="true" className="inline size-3.5 -mt-0.5" /> показывает, как выглядит такой экран.</p>
                <div className="mt-3 grid gap-2 sm:grid-cols-2" onMouseLeave={() => setHoveredFeature(null)}>
                  {MAX_FEATURES.map(feature => {
                    const selected = values.features.includes(feature);
                    return <div key={feature} className="max-feature-chip" data-selected={selected}>
                      <button type="button" aria-pressed={selected} className="max-feature-chip__toggle" onClick={() => onChange({ features: selected ? values.features.filter(item => item !== feature) : [...values.features, feature] })}>
                        <span className="max-feature-chip__check" aria-hidden="true">{selected && <Check className="size-3" />}</span>
                        <span className="max-feature-chip__copy">
                          <span className="max-feature-chip__label">{feature}</span>
                          <span className="max-feature-chip__hint">{MAX_FEATURE_INFO[feature].summary}</span>
                        </span>
                      </button>
                      <button type="button" className="max-feature-chip__peek" aria-pressed={previewedFeature === feature}
                        aria-label={`Показать пример экрана: ${feature}`} title="Показать пример экрана"
                        onMouseEnter={() => setHoveredFeature(feature)}
                        onFocus={() => setHoveredFeature(feature)}
                        onBlur={() => setHoveredFeature(null)}
                        onClick={() => setPinnedFeature(pinnedFeature === feature ? null : feature)}>
                        <Eye className="size-3.5" aria-hidden="true" />
                      </button>
                    </div>;
                  })}
                </div>
                <MaxFeaturePreview feature={previewedFeature} />
              </fieldset>
              <div className="space-y-2">
                <Label htmlFor="max-brand">Цвета бренда</Label>
                <Input id="max-brand" value={values.brandColors}
                  onChange={event => onChange({ brandColors: event.target.value })}
                  placeholder="#0062ee, графит, молочный" aria-describedby="max-brand-hint" />
                <p id="max-brand-hint" className="max-wizard-note">Другие пожелания к оформлению можно написать в описании идеи.</p>
              </div>
            </>}
            {step === 3 && <>{initialCatalog}<MaxProjectReview {...values} /></>}
          </div>
          <footer className="max-wizard-footer">
            <Button type="button" variant="outline" disabled={busy} onClick={() => step === 0 ? onOpenChange(false) : setStep(step - 1)}>{step === 0 ? "Отмена" : "Назад"}</Button>
            <Button type="submit" disabled={busy || (step === 3 && submitDisabled) || ((step === 0 || step === 3) && !ready)}>
              {step === 3 && (busy ? <Loader2 className="size-4 animate-spin" /> : <Sparkles className="size-4" />)}
              {step === 3 ? "Создать проект" : "Далее"}
            </Button>
          </footer>
        </form>
      </DialogContent>
    </Dialog>
  );
}
