"use client";

import { useState } from "react";
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
import { cn } from "@/lib/utils";

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

function errorMessage(error: unknown) {
  return error instanceof ApiError ? error.message : "Попробуйте ещё раз";
}

export function MaxProjectSetupDialog({
  projectId,
  display = "panel",
}: {
  projectId: string;
  display?: "panel" | "toolbar";
}) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<MaxProjectConfigPayload | null>(null);
  const config = useQuery({
    queryKey: ["max-config", projectId],
    queryFn: () => getMaxProjectConfig(projectId),
    enabled: open,
  });

  const current = draft ?? config.data?.config ?? null;

  const save = useMutation({
    mutationFn: (payload: MaxProjectConfigPayload) =>
      saveMaxProjectConfig(projectId, payload),
    onSuccess: (data) => {
      qc.setQueryData(["max-config", projectId], data);
      void qc.invalidateQueries({ queryKey: ["max-readiness", projectId] });
      void qc.invalidateQueries({ queryKey: ["snapshots", projectId] });
      toast.success("Настройки применены без генерации", {
        description: "Контент и юридические страницы сохранены в новой версии.",
      });
      setOpen(false);
    },
    onError: (error) =>
      toast.error("Не удалось сохранить", { description: errorMessage(error) }),
  });

  const inputClass = "h-10 border-white/[0.1] bg-black/20";
  const sectionClass =
    "space-y-4 rounded-2xl border border-white/[0.08] bg-white/[0.025] p-4";

  return (
    <>
      <Button
        size="sm"
        variant="secondary"
        className={
          display === "panel"
            ? "h-10 w-full gap-2 rounded-xl border-white/[0.1] bg-white/[0.04] text-xs text-white hover:bg-white/[0.08]"
            : "h-7 gap-1.5 px-2.5 text-xs"
        }
        onClick={() => setOpen(true)}
        data-testid="max-settings-open"
      >
        <Settings2 className="h-3.5 w-3.5" />
        Настройки и готовность
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-h-[92vh] overflow-y-auto border-white/[0.1] bg-[#12141d] text-white sm:max-w-[760px]">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-white">
              <FileCheck2 className="h-5 w-5 text-[#8d83ff]" />
              Готовое приложение без разработчика
            </DialogTitle>
            <DialogDescription className="text-white/45">
              Эти данные обновляются без модели и без расходов. Студия версионирует
              контент, поддержку и обязательные юридические страницы.
            </DialogDescription>
          </DialogHeader>

          {config.isLoading || !current ? (
            <div className="flex min-h-44 items-center justify-center">
              <Loader2 className="h-5 w-5 animate-spin text-[#8d83ff]" />
            </div>
          ) : (
            <div className="space-y-4">
              <section className={sectionClass}>
                <div>
                  <h3 className="text-sm font-semibold">Продукт</h3>
                  <p className="mt-1 text-xs text-white/40">
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
                      className="min-h-24 border-white/[0.1] bg-black/20"
                      value={current.summary}
                      maxLength={1000}
                      onChange={(event) =>
                        setDraft({ ...current, summary: event.target.value })
                      }
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="max-config-type">Тип приложения</Label>
                    <select
                      id="max-config-type"
                      className="h-10 w-full rounded-md border border-white/[0.1] bg-[#0e1017] px-3 text-sm"
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
                      placeholder="#6d5dfc, #ffffff"
                      onChange={(event) =>
                        setDraft({ ...current, brand_colors: event.target.value })
                      }
                    />
                  </div>
                </div>
              </section>

              <section className={sectionClass}>
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <h3 className="text-sm font-semibold">Каталог и контент</h3>
                    <p className="mt-1 text-xs text-white/40">
                      Товары, услуги, события, награды или уроки — без обращения к модели.
                    </p>
                  </div>
                  <Button
                    size="sm"
                    variant="secondary"
                    className="gap-1.5"
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
                  <p className="rounded-xl border border-dashed border-white/[0.1] p-4 text-center text-xs text-white/35">
                    Добавьте управляемые элементы, если приложению нужен каталог.
                  </p>
                ) : (
                  <div className="space-y-3">
                    {current.content.map((item, index) => (
                      <div
                        key={item.id}
                        className="grid gap-3 rounded-xl border border-white/[0.08] bg-black/15 p-3 sm:grid-cols-[1fr_150px_36px]"
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
                            className="min-h-16 border-white/[0.1] bg-black/20 text-xs"
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
                        <Input
                          aria-label={`Цена элемента ${index + 1}`}
                          className={inputClass}
                          value={item.price}
                          placeholder="Цена / подпись"
                          onChange={(event) => {
                            const content = [...current.content];
                            content[index] = { ...item, price: event.target.value };
                            setDraft({ ...current, content });
                          }}
                        />
                        <button
                          type="button"
                          className="flex h-9 w-9 items-center justify-center rounded-lg text-white/35 hover:bg-danger/10 hover:text-danger"
                          aria-label={`Удалить ${item.title}`}
                          onClick={() =>
                            setDraft({
                              ...current,
                              content: current.content.filter((_, itemIndex) => itemIndex !== index),
                            })
                          }
                        >
                          <Trash2 className="h-4 w-4" />
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </section>

              <section className={sectionClass}>
                <div>
                  <h3 className="text-sm font-semibold">Владелец и поддержка</h3>
                  <p className="mt-1 text-xs text-white/40">
                    Эти сведения попадут в политику, условия и страницу поддержки.
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
                </div>
              </section>

              <section className={sectionClass}>
                <div>
                  <h3 className="text-sm font-semibold">Политики MAX</h3>
                  <p className="mt-1 text-xs text-white/40">
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
                            ? "border-[#7468ff] bg-[#7468ff]/10"
                            : "border-white/[0.08] bg-black/15",
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
                                ? "border-[#7468ff] bg-[#7468ff]"
                                : "border-white/20",
                            )}
                          >
                            {checked && <Check className="h-3 w-3" />}
                          </span>
                          {item.label}
                        </span>
                        <span className="mt-2 block text-[10px] leading-4 text-white/35">
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
                    className="h-10 w-full rounded-md border border-white/[0.1] bg-[#0e1017] px-3 text-sm sm:w-48"
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
                <label className="flex cursor-pointer items-start gap-3 rounded-xl border border-white/[0.08] bg-black/15 p-3">
                  <input
                    type="checkbox"
                    className="mt-0.5 h-4 w-4 accent-[#7468ff]"
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
                    <span className="mt-1 block text-[10px] leading-4 text-white/35">
                      Автоматический комплект — основа. Владелец отвечает за
                      актуальность реквизитов и соответствие своей деятельности закону.
                    </span>
                  </span>
                </label>
                <label className="flex cursor-pointer items-start gap-3 rounded-xl border border-white/[0.08] bg-black/15 p-3">
                  <input
                    type="checkbox"
                    className="mt-0.5 h-4 w-4 accent-[#7468ff]"
                    checked={current.max_url_attached}
                    onChange={(event) =>
                      setDraft({ ...current, max_url_attached: event.target.checked })
                    }
                  />
                  <span>
                    <span className="block text-xs font-medium">
                      HTTPS-адрес уже вставлен в кабинете MAX
                    </span>
                    <span className="mt-1 block text-[10px] leading-4 text-white/35">
                      MAX пока не даёт публичного API для этой операции, поэтому
                      требуется одно подтверждение.
                    </span>
                  </span>
                </label>
              </section>

              <Button
                className="h-11 w-full"
                disabled={
                  save.isPending ||
                  current.app_name.trim().length < 1 ||
                  current.summary.trim().length < 1
                }
                onClick={() => save.mutate(current)}
              >
                {save.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                Сохранить и применить без генерации
              </Button>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}
