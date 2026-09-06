"use client";

import { useId, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, FileCheck2, Loader2, Plus, Settings2, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { ApiError } from "@/lib/api/client";
import {
  getMaxProjectConfig,
  saveMaxProjectConfig,
} from "@/lib/api/max-studio";
import type { MaxProjectConfigPayload } from "@/lib/api/types";
import { MAX_BRIEF_LENGTH } from "@/lib/max-brief";
import { cn } from "@/lib/utils";
import "./max-studio.css";
import "./max-project-workspace.css";

const CHECKS: {
  key: "has_sales" | "has_user_content" | "marketing_notifications";
  label: string;
  description: string;
}[] = [
  {
    key: "has_sales",
    label: "Продажи или оплата",
    description: "Добавит условия заказа, цены, отмены и возврата.",
  },
  {
    key: "has_user_content",
    label: "Пользовательский контент",
    description: "Добавит правила публикаций, жалоб и модерации.",
  },
  {
    key: "marketing_notifications",
    label: "Маркетинговые уведомления",
    description: "Потребует явного согласия и возможности отписаться.",
  },
];

type SetupSection = "details" | "content" | "owner" | "policies";

const SETUP_SECTIONS: Array<{ id: SetupSection; label: string }> = [
  { id: "details", label: "Основное" },
  { id: "content", label: "Контент" },
  { id: "owner", label: "Владелец" },
  { id: "policies", label: "Политики" },
];

function errorMessage(error: unknown) {
  return error instanceof ApiError ? error.message : "Попробуйте ещё раз";
}

export function MaxProjectSetupDialog({
  projectId,
  display = "panel",
  emphasized = false,
  label = "Данные приложения",
}: {
  projectId: string;
  display?: "panel" | "toolbar";
  emphasized?: boolean;
  label?: string;
}) {
  const qc = useQueryClient();
  const tabsId = useId();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<MaxProjectConfigPayload | null>(null);
  const [section, setSection] = useState<SetupSection>("details");
  const config = useQuery({
    queryKey: ["max-config", projectId],
    queryFn: () => getMaxProjectConfig(projectId),
    enabled: open,
  });

  const current = draft ?? config.data?.config ?? null;
  const pendingApplication = config.data?.application_mode === "runtime"
    && config.data.config_version > 0 && !config.data.synced_snapshot_id;

  const save = useMutation({
    mutationFn: (payload: MaxProjectConfigPayload) =>
      saveMaxProjectConfig(projectId, payload),
    onSuccess: (data) => {
      qc.setQueryData(["max-config", projectId], data);
      void qc.invalidateQueries({ queryKey: ["max-readiness", projectId] });
      void qc.invalidateQueries({ queryKey: ["snapshots", projectId] });
      void qc.invalidateQueries({ queryKey: ["max-preview-session", projectId] });
      toast.success(data.application_mode === "runtime" && !data.synced_snapshot_id
        ? "Настройки сохранены на сервере" : "Настройки применены без генерации", {
        description: data.application_mode === "runtime"
          ? (data.synced_snapshot_id
            ? "Конфигурация, поддержка и юридические страницы обновлены. Сборка и данные приложения не изменены."
            : "Они будут использованы при создании приложения.")
          : "Контент и юридические страницы сохранены в новой версии.",
      });
      setOpen(false);
      setDraft(null);
    },
    onError: (error) =>
      toast.error("Не удалось сохранить", { description: errorMessage(error) }),
  });
  const inputClass = "h-11 border-border-default bg-surface sm:h-10";
  const sectionClass = "max-settings-section space-y-5";
  const saved = config.data?.config;
  const changedSections = current && saved
    ? [
        JSON.stringify({
          app_name: current.app_name,
          app_type: current.app_type,
          summary: current.summary,
          audience: current.audience,
          primary_action: current.primary_action,
          features: current.features,
          style: current.style,
          brand_colors: current.brand_colors,
        }) !==
          JSON.stringify({
            app_name: saved.app_name,
            app_type: saved.app_type,
            summary: saved.summary,
            audience: saved.audience,
            primary_action: saved.primary_action,
            features: saved.features,
            style: saved.style,
            brand_colors: saved.brand_colors,
          }),
        JSON.stringify(current.content) !== JSON.stringify(saved.content),
        JSON.stringify({ operator: current.operator, support: current.support }) !==
          JSON.stringify({ operator: saved.operator, support: saved.support }),
        JSON.stringify(current.legal) !== JSON.stringify(saved.legal),
      ].filter(Boolean).length
    : 0;

  return (
    <>
      <Button
        size="sm"
        variant={emphasized ? "primary" : "secondary"}
        className={
          display === "panel"
            ? cn(
                "h-11 min-w-0 w-full gap-1.5 overflow-hidden rounded-lg px-2 text-[11px]",
                !emphasized &&
                  "border-border-default bg-surface text-fg-secondary hover:bg-surface-base",
              )
            : "h-11 gap-1.5 px-2.5 text-xs sm:h-7"
        }
        onClick={() => {
          setSection("details");
          setDraft(null);
          setOpen(true);
        }}
        data-testid="max-settings-open"
      >
        <Settings2 className="h-3.5 w-3.5" />
        <span className="min-w-0 truncate">
          {display === "panel" ? label : "Настройки"}
        </span>
      </Button>
      <Dialog
        open={open}
        onOpenChange={(next) => {
          setOpen(next);
          if (!next) setDraft(null);
        }}
      >
        <DialogContent
          data-product-shell
          data-max-studio
          className="max-settings-dialog flex max-h-[calc(100dvh-1rem)] flex-col gap-0 overflow-hidden border-border-default bg-surface p-0 text-fg-primary sm:max-h-[92dvh] sm:max-w-[760px] sm:p-0"
        >
          <DialogHeader className="shrink-0 px-5 pb-4 pr-16 pt-5 sm:px-7 sm:pb-5 sm:pr-14 sm:pt-7">
            <DialogTitle className="flex items-center gap-2 text-fg-primary">
              <FileCheck2 className="h-5 w-5 text-accent" />
              Данные приложения
            </DialogTitle>
            <DialogDescription className="text-fg-secondary">
              Заполните четыре раздела. Настройки сохраняются на сервере и
              применяются без повторной генерации.
            </DialogDescription>
          </DialogHeader>

          <div className="shrink-0 border-y border-border-default px-5 py-3 sm:px-7">
            <div className="flex gap-2 overflow-x-auto" role="tablist" aria-label="Разделы данных приложения">
              {SETUP_SECTIONS.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  role="tab"
                  id={`${tabsId}-${item.id}`}
                  aria-controls={`${tabsId}-panel`}
                  aria-selected={section === item.id}
                  tabIndex={section === item.id ? 0 : -1}
                  onClick={() => setSection(item.id)}
                  onKeyDown={(event) => {
                    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
                    event.preventDefault();
                    const index = SETUP_SECTIONS.findIndex(tab => tab.id === item.id);
                    const next = event.key === "Home" ? 0 : event.key === "End" ? SETUP_SECTIONS.length - 1 : (index + (event.key === "ArrowRight" ? 1 : -1) + SETUP_SECTIONS.length) % SETUP_SECTIONS.length;
                    setSection(SETUP_SECTIONS[next].id);
                    document.getElementById(`${tabsId}-${SETUP_SECTIONS[next].id}`)?.focus();
                  }}
                  className={cn(
                    "h-9 shrink-0 rounded-[8px] border px-3 text-xs font-medium transition-colors",
                    section === item.id
                      ? "border-accent bg-accent-subtle text-accent-secondary"
                      : "border-border-default bg-surface text-fg-secondary hover:bg-surface-base",
                  )}
                >
                  {item.label}
                </button>
              ))}
            </div>
          </div>

          {config.isError ? (
            <div role="alert" className="p-6 text-sm"><p>Не удалось загрузить данные приложения.</p><Button variant="outline" className="mt-3" onClick={() => void config.refetch()}>Повторить загрузку</Button></div>
          ) : config.isLoading || !current ? (
            <div className="flex min-h-44 flex-1 items-center justify-center">
              <Loader2 className="h-5 w-5 animate-spin text-accent" />
            </div>
          ) : (
            <>
              <div
                className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-5 pb-5 sm:px-7 sm:pb-7"
                role="tabpanel"
                id={`${tabsId}-panel`}
                aria-labelledby={`${tabsId}-${section}`}
                data-testid="max-settings-scroll-region"
              >
                <div className="space-y-4">
                  {section === "details" && (
                    <section className={sectionClass}>
                <div>
                  <h3 className="text-sm font-semibold">Продукт</h3>
                  <p className="mt-1 text-xs text-fg-tertiary">
                    Главный сценарий и тексты, которыми пользуются бот и приложение.
                  </p>
                </div>
                <div className="grid gap-4 sm:grid-cols-2">
                  <div className="space-y-2">
                    <Label htmlFor="max-config-name">Название</Label>
                    <Input
                      id="max-config-name"
                      className={inputClass}
                      value={current.app_name}
                      maxLength={100}
                      onChange={(event) =>
                        setDraft({ ...current, app_name: event.target.value })
                      }
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="max-config-action">Главное действие</Label>
                    <Input
                      id="max-config-action"
                      className={inputClass}
                      value={current.primary_action}
                      placeholder="Например, оформить заказ"
                      maxLength={200}
                      onChange={(event) =>
                        setDraft({ ...current, primary_action: event.target.value })
                      }
                    />
                  </div>
                  <div className="space-y-2 sm:col-span-2">
                    <Label htmlFor="max-config-summary">Описание сервиса</Label>
                    <Textarea
                      id="max-config-summary"
                      className="min-h-24 border-border-default bg-surface"
                      value={current.summary}
                      aria-describedby="max-config-summary-limit"
                      aria-invalid={Array.from(current.summary.trim()).length > MAX_BRIEF_LENGTH}
                      onChange={(event) =>
                        setDraft({ ...current, summary: event.target.value })
                      }
                    />
                    <p id="max-config-summary-limit" className="text-xs text-fg-secondary">
                      {Array.from(current.summary.trim()).length} / {MAX_BRIEF_LENGTH} символов. {Array.from(current.summary.trim()).length > MAX_BRIEF_LENGTH ? "Сократите описание перед сохранением — текст не обрезан." : "Описание сохранится целиком."}
                    </p>
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="max-config-type">Тип приложения</Label>
                    <select
                      id="max-config-type"
                      className="h-11 w-full rounded-md border border-border-default bg-surface px-3 text-sm sm:h-10"
                      value={current.app_type}
                      onChange={(event) =>
                        setDraft({
                          ...current,
                          app_type: event.target.value as MaxProjectConfigPayload["app_type"],
                        })
                      }
                    >
                      <option value="loyalty">Лояльность</option>
                      <option value="catalog">Каталог и заказы</option>
                      <option value="booking">Запись и бронирование</option>
                      <option value="event">Событие</option>
                      <option value="education">Обучение</option>
                      <option value="custom">Свой сценарий</option>
                    </select>
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="max-config-audience">Аудитория</Label>
                    <Input
                      id="max-config-audience"
                      className={inputClass}
                      value={current.audience}
                      onChange={(event) =>
                        setDraft({ ...current, audience: event.target.value })
                      }
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="max-config-colors">Цвета бренда</Label>
                    <Input
                      id="max-config-colors"
                      className={inputClass}
                      value={current.brand_colors}
                      placeholder="#4f81f7, #121519"
                      onChange={(event) =>
                        setDraft({ ...current, brand_colors: event.target.value })
                      }
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="max-config-style">Визуальный стиль</Label>
                    <select
                      id="max-config-style"
                      className="h-11 w-full rounded-md border border-border-default bg-surface px-3 text-sm sm:h-10"
                      value={current.style}
                      onChange={(event) =>
                        setDraft({
                          ...current,
                          style: event.target
                            .value as MaxProjectConfigPayload["style"],
                        })
                      }
                    >
                      <option value="brand">В цветах бренда</option>
                      <option value="clean">Чистый и спокойный</option>
                      <option value="bright">Яркий и акцентный</option>
                    </select>
                  </div>
                  <div className="space-y-2 sm:col-span-2">
                    <Label htmlFor="max-config-features">
                      Возможности приложения
                    </Label>
                    <Textarea
                      id="max-config-features"
                      className="min-h-20 border-border-default bg-surface"
                      value={current.features.join(", ")}
                      placeholder="Заказы, бонусы, запись, уведомления"
                      onChange={(event) =>
                        setDraft({
                          ...current,
                          features: event.target.value
                            .split(",")
                            .map((feature) => feature.trim())
                            .filter(Boolean)
                            .slice(0, 24),
                        })
                      }
                    />
                    <p className="text-[10px] leading-4 text-fg-tertiary">
                      Перечислите через запятую до 24 функций. Они попадут в
                      управляемую конфигурацию приложения без повторной генерации.
                    </p>
                  </div>
                </div>
                    </section>
                  )}

                  {section === "content" && (
                    <section className={sectionClass}>
                <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                  <div>
                    <h3 className="text-sm font-semibold">Каталог и контент</h3>
                    <p className="mt-1 text-xs text-fg-tertiary">
                      Товары, услуги, события, награды или уроки — без обращения к модели.
                    </p>
                  </div>
                  <Button
                    size="sm"
                    variant="secondary"
                    className="min-h-11 gap-1.5 sm:min-h-8"
                    onClick={() =>
                      setDraft({
                        ...current,
                        content: [
                          ...current.content,
                          {
                            id: `item-${Date.now()}`,
                            title: "Новый элемент",
                            description: "",
                            price: "",
                            action_label: "Открыть",
                            active: true,
                          },
                        ],
                      })
                    }
                  >
                    <Plus className="h-3.5 w-3.5" />
                    Добавить
                  </Button>
                </div>
                {current.content.length === 0 ? (
                  <p className="rounded-xl border border-dashed border-border-default p-4 text-center text-xs text-fg-tertiary">
                    Добавьте управляемые элементы, если приложению нужен каталог.
                  </p>
                ) : (
                  <div className="space-y-3">
                    {current.content.map((item, index) => (
                      <div
                        key={item.id}
                        className="grid gap-3 rounded-xl border border-border-default bg-surface p-3 sm:grid-cols-[minmax(0,1fr)_160px_auto]"
                      >
                        <div className="space-y-2">
                          <Input
                            aria-label={`Название элемента ${index + 1}`}
                            className={inputClass}
                            value={item.title}
                            onChange={(event) => {
                              const content = [...current.content];
                              content[index] = { ...item, title: event.target.value };
                              setDraft({ ...current, content });
                            }}
                          />
                          <Textarea
                            aria-label={`Описание элемента ${index + 1}`}
                            className="min-h-16 border-border-default bg-surface text-xs"
                            value={item.description}
                            placeholder="Описание"
                            onChange={(event) => {
                              const content = [...current.content];
                              content[index] = {
                                ...item,
                                description: event.target.value,
                              };
                              setDraft({ ...current, content });
                            }}
                          />
                        </div>
                        <div className="space-y-2">
                          <Input
                            aria-label={`Цена элемента ${index + 1}`}
                            className={inputClass}
                            value={item.price}
                            placeholder="Цена / подпись"
                            maxLength={80}
                            onChange={(event) => {
                              const content = [...current.content];
                              content[index] = {
                                ...item,
                                price: event.target.value,
                              };
                              setDraft({ ...current, content });
                            }}
                          />
                          <Input
                            aria-label={`Текст кнопки элемента ${index + 1}`}
                            className={inputClass}
                            value={item.action_label}
                            placeholder="Открыть"
                            maxLength={40}
                            onChange={(event) => {
                              const content = [...current.content];
                              content[index] = {
                                ...item,
                                action_label: event.target.value,
                              };
                              setDraft({ ...current, content });
                            }}
                          />
                        </div>
                        <div className="flex items-center justify-between gap-2 sm:flex-col sm:justify-start">
                          <button
                            type="button"
                            role="switch"
                            aria-checked={item.active}
                            className={cn(
                              "inline-flex min-h-11 items-center gap-2 rounded-lg px-3 text-xs sm:min-h-9",
                              item.active
                                ? "bg-[#248a4b]/10 text-success-fg"
                                : "bg-surface-base text-fg-tertiary",
                            )}
                            onClick={() => {
                              const content = [...current.content];
                              content[index] = {
                                ...item,
                                active: !item.active,
                              };
                              setDraft({ ...current, content });
                            }}
                          >
                            <span
                              className={cn(
                                "size-2 rounded-full",
                                item.active ? "bg-[#248a4b]" : "bg-[#828491]",
                              )}
                            />
                            {item.active ? "Показан" : "Скрыт"}
                          </button>
                          <button
                            type="button"
                            className="flex h-11 w-11 items-center justify-center rounded-lg text-fg-tertiary hover:bg-danger/10 hover:text-danger sm:h-9 sm:w-9"
                            aria-label={`Удалить ${item.title}`}
                            onClick={() =>
                              setDraft({
                                ...current,
                                content: current.content.filter(
                                  (_, itemIndex) => itemIndex !== index,
                                ),
                              })
                            }
                          >
                            <Trash2 className="h-4 w-4" />
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
                    </section>
                  )}

                  {section === "owner" && (
                    <section className={sectionClass}>
                <div>
                  <h3 className="text-sm font-semibold">Владелец и поддержка</h3>
                  <p className="mt-1 text-xs text-fg-tertiary">
                    MAX проверяет бизнес в своём кабинете. Здесь сведения не
                    проверяются повторно — они попадут в обязательные страницы
                    приложения: политику, условия и поддержку.
                  </p>
                </div>
                <div className="grid gap-4 sm:grid-cols-2">
                  <div className="space-y-2 sm:col-span-2">
                    <Label htmlFor="max-legal-name">ИП, ООО или ФИО владельца</Label>
                    <Input
                      id="max-legal-name"
                      className={inputClass}
                      value={current.operator.legal_name}
                      onChange={(event) =>
                        setDraft({
                          ...current,
                          operator: { ...current.operator, legal_name: event.target.value },
                        })
                      }
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="max-inn">ИНН</Label>
                    <Input
                      id="max-inn"
                      className={inputClass}
                      value={current.operator.inn}
                      onChange={(event) =>
                        setDraft({
                          ...current,
                          operator: { ...current.operator, inn: event.target.value },
                        })
                      }
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="max-ogrn">ОГРН / ОГРНИП</Label>
                    <Input
                      id="max-ogrn"
                      className={inputClass}
                      value={current.operator.ogrn}
                      onChange={(event) =>
                        setDraft({
                          ...current,
                          operator: { ...current.operator, ogrn: event.target.value },
                        })
                      }
                    />
                  </div>
                  <div className="space-y-2 sm:col-span-2">
                    <Label htmlFor="max-address">Адрес</Label>
                    <Input
                      id="max-address"
                      className={inputClass}
                      value={current.operator.address}
                      onChange={(event) =>
                        setDraft({
                          ...current,
                          operator: { ...current.operator, address: event.target.value },
                        })
                      }
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="max-support-email">Email поддержки</Label>
                    <Input
                      id="max-support-email"
                      type="email"
                      className={inputClass}
                      value={current.support.email ?? ""}
                      onChange={(event) =>
                        setDraft({
                          ...current,
                          support: {
                            ...current.support,
                            email: event.target.value || null,
                          },
                        })
                      }
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="max-support-phone">Телефон поддержки</Label>
                    <Input
                      id="max-support-phone"
                      className={inputClass}
                      value={current.support.phone}
                      onChange={(event) =>
                        setDraft({
                          ...current,
                          support: { ...current.support, phone: event.target.value },
                        })
                      }
                    />
                  </div>
                  <div className="space-y-2 sm:col-span-2">
                    <Label htmlFor="max-support-response-time">
                      Срок ответа поддержки
                    </Label>
                    <Input
                      id="max-support-response-time"
                      className={inputClass}
                      value={current.support.response_time}
                      maxLength={120}
                      placeholder="Ответим в течение 2 рабочих дней"
                      onChange={(event) =>
                        setDraft({
                          ...current,
                          support: {
                            ...current.support,
                            response_time: event.target.value,
                          },
                        })
                      }
                    />
                  </div>
                </div>
                    </section>
                  )}

                  {section === "policies" && (
                    <section className={sectionClass}>
                <div>
                  <h3 className="text-sm font-semibold">Политики MAX</h3>
                  <p className="mt-1 text-xs text-fg-tertiary">
                    Отметьте реальные функции — студия включит нужные правила.
                  </p>
                </div>
                <div className="grid gap-2 sm:grid-cols-3">
                  {CHECKS.map((item) => {
                    const checked = Boolean(current.legal[item.key]);
                    return (
                      <button
                        key={item.key}
                        type="button"
                        aria-pressed={checked}
                        className={cn(
                          "rounded-xl border p-3 text-left",
                          checked
                            ? "border-[#4f81f7] bg-accent/[.07]"
                            : "border-border-default bg-surface",
                        )}
                        onClick={() =>
                          setDraft({
                            ...current,
                            legal: { ...current.legal, [item.key]: !checked },
                          })
                        }
                      >
                        <span className="flex items-center gap-2 text-xs font-medium">
                          <span
                            className={cn(
                              "flex h-4 w-4 items-center justify-center rounded border",
                              checked
                                ? "border-[#4f81f7] bg-accent"
                                : "border-border-default",
                            )}
                          >
                            {checked && <Check className="h-3 w-3" />}
                          </span>
                          {item.label}
                        </span>
                        <span className="mt-2 block text-[10px] leading-4 text-fg-tertiary">
                          {item.description}
                        </span>
                      </button>
                    );
                  })}
                </div>
                <div className="space-y-2">
                  <Label htmlFor="max-age-rating">Возрастная маркировка</Label>
                  <select
                    id="max-age-rating"
                    className="h-11 w-full rounded-md border border-border-default bg-surface px-3 text-sm sm:h-10 sm:w-48"
                    value={current.legal.age_rating}
                    onChange={(event) =>
                      setDraft({
                        ...current,
                        legal: {
                          ...current.legal,
                          age_rating: event.target
                            .value as MaxProjectConfigPayload["legal"]["age_rating"],
                        },
                      })
                    }
                  >
                    {["0+", "6+", "12+", "16+", "18+"].map((rating) => (
                      <option key={rating} value={rating}>
                        {rating}
                      </option>
                    ))}
                  </select>
                </div>
                <label className="flex cursor-pointer items-start gap-3 rounded-xl border border-border-default bg-surface p-3">
                  <input
                    type="checkbox"
                    className="mt-0.5 h-4 w-4 accent-[#4f81f7]"
                    checked={current.legal.terms_accepted}
                    onChange={(event) =>
                      setDraft({
                        ...current,
                        legal: {
                          ...current.legal,
                          terms_accepted: event.target.checked,
                        },
                      })
                    }
                  />
                  <span>
                    <span className="block text-xs font-medium">
                      Подтверждаю корректность данных владельца
                    </span>
                    <span className="mt-1 block text-[10px] leading-4 text-fg-tertiary">
                      Автоматический комплект — основа. Владелец отвечает за
                      актуальность реквизитов и соответствие своей деятельности закону.
                    </span>
                  </span>
                </label>
                <label className="flex cursor-pointer items-start gap-3 rounded-xl border border-border-default bg-surface p-3">
                  <input
                    type="checkbox"
                    className="mt-0.5 h-4 w-4 accent-[#4f81f7]"
                    checked={current.legal.personal_data_consent}
                    onChange={(event) =>
                      setDraft({
                        ...current,
                        legal: {
                          ...current.legal,
                          personal_data_consent: event.target.checked,
                        },
                      })
                    }
                  />
                  <span>
                    <span className="block text-xs font-medium">
                      Запрашивать согласие на обработку персональных данных
                    </span>
                    <span className="mt-1 block text-[10px] leading-4 text-fg-tertiary">
                      Оставьте включённым, если приложение получает имя, телефон,
                      email, адрес или другие данные пользователя.
                    </span>
                  </span>
                </label>
                    </section>
                  )}
                </div>
              </div>

              <div
                className="flex shrink-0 flex-col gap-3 border-t border-border-default px-5 pb-[max(1rem,env(safe-area-inset-bottom))] pt-3 sm:flex-row sm:items-center sm:justify-between sm:px-7 sm:pt-4"
                data-testid="max-settings-footer"
              >
                <p className="text-xs text-fg-tertiary">
                  {changedSections > 0
                    ? `Изменено разделов: ${changedSections}`
                    : "Все изменения сохранены"}
                </p>
                <Button
                  className="h-11 w-full sm:w-auto sm:min-w-[220px]"
                  disabled={
                    save.isPending ||
                    (changedSections === 0 && !pendingApplication) ||
                    current.app_name.trim().length < 1 ||
                    current.summary.trim().length < 1 ||
                    Array.from(current.summary.trim()).length > MAX_BRIEF_LENGTH
                  }
                  onClick={() => save.mutate(current)}
                >
                  {save.isPending && (
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  )}
                  Сохранить и проверить
                </Button>
              </div>
            </>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}
