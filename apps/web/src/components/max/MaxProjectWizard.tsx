"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { Check, Eye, Loader2, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { MAX_APP_TYPES, MAX_BRIEF_LENGTH, MAX_FEATURES, MAX_FEATURE_INFO, MAX_MANAGEMENT_MODULES, briefManagementModuleOptOuts, defaultManagementModules, maxBriefFieldError, newProjectManagementModules, selectedMaxAppTypes, type MaxAppTypeId, type MaxFeature, type MaxProjectBrief } from "@/lib/max-brief";
import { characterCount } from "@/lib/codepoints";
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
  const previewedFeature = hoveredFeature ?? pinnedFeature;
  const [submitting, setSubmitting] = useState(false);
  const submitLock = useRef(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const busy = pending || submitting;
  const appTypes = selectedMaxAppTypes(values.appType, values.appTypes);
  const multipleTypes = values.appTypes !== undefined;
  const customSelected = appTypes.includes("custom");
  const briefOptOuts = briefManagementModuleOptOuts(values.idea);
  const moduleOptOuts = values.managementModuleOptOuts ?? [];
  // Keep the draft selections when the catalog temporarily hides dependent modules.
  const draftManagementModules = values.managementModules ?? defaultManagementModules(values.appType, values.appTypes);
  const managementModules = newProjectManagementModules(
    values.appType, draftManagementModules, values.appTypes, [...moduleOptOuts, ...briefOptOuts],
  );
  const length = characterCount(values.idea.trim());
  const ideaTooShort = customSelected && length < 10;
  const ideaTooLong = length > MAX_BRIEF_LENGTH;
  const fieldErrors = { name: maxBriefFieldError("name", values.name), audience: maxBriefFieldError("audience", values.audience),
    primaryAction: maxBriefFieldError("primaryAction", values.primaryAction), brandColors: maxBriefFieldError("brandColors", values.brandColors) };
  const ideaReady = characterCount(values.name.trim()) > 1 && !ideaTooShort && !ideaTooLong && !fieldErrors.name;
  const ready = ideaReady && !Object.values(fieldErrors).some(Boolean);
  const stepValid = step === 0 ? ideaReady
    : step === 1 ? !fieldErrors.audience && !fieldErrors.primaryAction : !fieldErrors.brandColors;

  useEffect(() => { if (open) heading.current?.focus(); }, [step, open]);

  function selectAppType(type: MaxAppTypeId) {
    if (!multipleTypes) {
      onChange({ appType: type });
      return;
    }
    const selected = appTypes.includes(type);
    if (selected && appTypes.length === 1) return;
    // The initial custom entry is neutral. Choosing a concrete scenario needs
    // no extra click to remove it; adding custom to explicit scenarios keeps them.
    const next = selected ? appTypes.filter(current => current !== type)
      : appTypes.length === 1 && customSelected ? [type] : [...appTypes, type];
    onChange({ appType: next[0], appTypes: next });
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
          if (step < 3) { if (stepValid) setStep(step + 1); }
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
                <Input id="max-project-name" disabled={busy} value={values.name} onChange={(event) => onChange({ name: event.target.value })} placeholder="Например, Кофе рядом" required aria-invalid={Boolean(fieldErrors.name)} aria-describedby="max-project-name-hint" />
                <p id="max-project-name-hint" className="text-xs text-fg-secondary">{fieldErrors.name || "От 2 до 100 символов"}</p>
              </div>
              <fieldset>
                <legend className="text-sm font-medium">Что пользователь сможет делать?</legend>
                {multipleTypes && <p className="max-wizard-note">Выберите хотя бы один</p>}
                <div className="max-wizard-type-options mt-3 grid gap-2 sm:grid-cols-2">
                  {MAX_APP_TYPES.map(item => <button key={item.id} type="button" disabled={busy} aria-pressed={appTypes.includes(item.id)} onClick={() => selectAppType(item.id)} className="max-wizard-option">
                    <span className="flex items-center justify-between gap-2 text-sm font-medium">{item.label}{appTypes.includes(item.id) && <Check className="size-4 text-accent" />}</span>
                    <span className="max-wizard-type-hint mt-1 block text-xs leading-5 text-fg-secondary">{item.description}</span>
                  </button>)}
                </div>
                <div id="max-project-details" className="mt-3 space-y-2">
                    <Label htmlFor="max-project-idea">
                      {customSelected ? "Опишите свою идею" : "Детали сценария (необязательно)"}
                    </Label>
                    <Textarea
                      id="max-project-idea"
                      disabled={busy}
                      value={values.idea}
                      onChange={(event) => {
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
                </div>
              </fieldset>
            </>}
            {step === 1 && <>
              <div className="space-y-2"><Label htmlFor="max-audience">Аудитория</Label><Input id="max-audience" disabled={busy} value={values.audience} aria-invalid={Boolean(fieldErrors.audience)} aria-describedby="max-audience-limit" onChange={event => onChange({ audience: event.target.value })} placeholder="Например, постоянные гости кофейни" /><p id="max-audience-limit" className="max-wizard-note">{fieldErrors.audience || `${characterCount(values.audience.trim())} / 400 символов`}</p></div>
              <div className="space-y-2"><Label htmlFor="max-action">Главное действие</Label><Input id="max-action" disabled={busy} value={values.primaryAction} aria-invalid={Boolean(fieldErrors.primaryAction)} aria-describedby="max-action-limit" onChange={event => onChange({ primaryAction: event.target.value })} placeholder="Например, обменять баллы на награду" /><p id="max-action-limit" className="max-wizard-note">{fieldErrors.primaryAction || `${characterCount(values.primaryAction.trim())} / 200 символов`}</p></div>
            </>}
            {step === 2 && <>
              <fieldset>
                <legend className="text-sm font-medium">Управление в Yleum</legend>
                <p className="max-wizard-note">Отметьте то, что точно нужно. Неотмеченные возможности доступны по задаче и не требуют отдельных экранов. Снятая вами отметка явно исключает модуль. Для заказов и записи нужен каталог.</p>
                {briefOptOuts.length > 0 && <p className="max-wizard-note">Исключения из описания учтены в выборе модулей.</p>}
                <div className="mt-3 grid gap-2 sm:grid-cols-2">
                  {MAX_MANAGEMENT_MODULES.map(module => <label key={module.id} className="max-wizard-option flex items-start gap-3">
                    <input type="checkbox" className="mt-1 size-4" checked={managementModules.includes(module.id)}
                      disabled={busy || briefOptOuts.includes(module.id) || (
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
                    <span><span className="block text-sm font-medium">{module.label}</span><span className="mt-1 block text-xs leading-5 text-fg-secondary">{module.description}{moduleOptOuts.includes(module.id) ? " Исключено вами." : ""}</span></span>
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
                      <button type="button" disabled={busy} aria-pressed={selected} className="max-feature-chip__toggle" onClick={() => onChange({ features: selected ? values.features.filter(item => item !== feature) : [...values.features, feature] })}>
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
                <Input id="max-brand" disabled={busy} value={values.brandColors} aria-invalid={Boolean(fieldErrors.brandColors)}
                  onChange={event => onChange({ brandColors: event.target.value })}
                  placeholder="#0062ee, графит, молочный" aria-describedby="max-brand-hint" />
                <p id="max-brand-hint" className="max-wizard-note">{fieldErrors.brandColors || `${characterCount(values.brandColors.trim())} / 200 символов. Другие пожелания к оформлению можно написать в описании идеи.`}</p>
              </div>
            </>}
            {step === 3 && <>
              {initialCatalog}
              <MaxProjectReview {...values} />
              <div className="space-y-2">
                <Label htmlFor="max-public-support-email">Email поддержки приложения</Label>
                <Input id="max-public-support-email" type="email" maxLength={254} disabled={busy}
                  value={values.supportEmail ?? ""} onChange={event => onChange({ supportEmail: event.target.value })}
                  aria-describedby="max-public-support-email-hint" />
                <p id="max-public-support-email-hint" className="max-wizard-note">Этот адрес увидят пользователи. Его можно указать позже в документах приложения.</p>
              </div>
            </>}
          </div>
          <footer className="max-wizard-footer">
            <Button type="button" variant="outline" disabled={busy} onClick={() => step === 0 ? onOpenChange(false) : setStep(step - 1)}>{step === 0 ? "Отмена" : "Назад"}</Button>
            <Button type="submit" disabled={busy || !stepValid || (step === 3 && (submitDisabled || !ready))}>
              {step === 3 && (busy ? <Loader2 className="size-4 animate-spin" /> : <Sparkles className="size-4" />)}
              {step === 3 ? "Создать проект" : "Далее"}
            </Button>
          </footer>
        </form>
      </DialogContent>
    </Dialog>
  );
}
