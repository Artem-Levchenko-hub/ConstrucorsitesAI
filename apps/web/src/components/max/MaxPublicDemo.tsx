"use client";

import {
  ArrowLeft,
  ArrowRight,
  CalendarDays,
  Check,
  ChevronLeft,
  CircleCheck,
  Gift,
  ShoppingBag,
  ShieldCheck,
  Sparkles,
  WandSparkles,
} from "lucide-react";
import Link from "next/link";
import { type FormEvent, useEffect, useState } from "react";

import { BrandMark } from "@/components/marketing/BrandMark";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  saveMaxDemoDraft,
  useMaxDemoDraft,
} from "@/hooks/useMaxDemoDraft";
import { createMaxDemoDraft, type MaxDemoDraft } from "@/lib/max-demo";
import { cn } from "@/lib/utils";

const EXAMPLES = [
  "Кофейня «Смена»: меню, заказ навынос и бонусы",
  "Салон красоты с записью к мастеру и напоминаниями",
  "Фитнес-клуб: расписание, баллы и награды участника",
] as const;

const GENERATION_STAGES = [
  "Определяем главный сценарий",
  "Собираем мобильные экраны",
  "Добавляем действия и тестовые данные",
] as const;

function DemoActionIcon({ draft }: { draft: MaxDemoDraft }) {
  if (draft.brief.appType === "booking") {
    return <CalendarDays className="size-4" />;
  }
  if (draft.brief.appType === "loyalty") {
    return <Gift className="size-4" />;
  }
  return <ShoppingBag className="size-4" />;
}

function DemoPhone({
  draft,
  stage,
}: {
  draft: MaxDemoDraft | null;
  stage: number | null;
}) {
  const [selected, setSelected] = useState<number | null>(null);
  const [complete, setComplete] = useState(false);

  if (!draft) {
    return (
      <div className="mx-auto w-full max-w-[360px] rounded-[38px] border-[7px] border-fg-primary bg-surface-raised p-3 shadow-[0_24px_60px_rgba(23,23,22,.18)]">
        <div className="mx-auto h-1 w-16 rounded-full bg-border-strong" />
        <div className="mt-3 min-h-[650px] overflow-hidden rounded-[24px] bg-surface-overlay p-5">
          <div className="flex items-center justify-between">
            <div className="h-3 w-20 animate-pulse rounded-full bg-border-subtle" />
            <div className="size-8 animate-pulse rounded-full bg-border-subtle" />
          </div>
          <div className="mt-16">
            <div className="h-3 w-28 animate-pulse rounded-full bg-accent-subtle" />
            <div className="mt-4 h-8 w-4/5 animate-pulse rounded-md bg-border-subtle" />
            <div className="mt-3 h-3 w-3/5 animate-pulse rounded-full bg-border-subtle" />
          </div>
          <div className="mt-10 space-y-3">
            {GENERATION_STAGES.map((label, index) => {
              const done = stage !== null && stage > index;
              const active = stage === index;
              return (
                <div
                  key={label}
                  className={cn(
                    "flex min-h-16 items-center gap-3 rounded-[12px] border p-4 transition-colors",
                    done
                      ? "border-success/25 bg-success/[.05]"
                      : active
                        ? "border-accent/35 bg-accent-subtle"
                        : "border-border-subtle",
                  )}
                >
                  <span
                    className={cn(
                      "grid size-7 shrink-0 place-items-center rounded-full border text-xs",
                      done
                        ? "border-success bg-success text-white"
                        : active
                          ? "border-accent text-accent"
                          : "border-border-default text-fg-secondary",
                    )}
                  >
                    {done ? <Check className="size-3.5" /> : index + 1}
                  </span>
                  <span className={cn("text-xs", active || done ? "text-fg-primary" : "text-fg-secondary")}>{label}</span>
                </div>
              );
            })}
          </div>
          <div className="mt-8 rounded-[12px] border border-dashed border-border-default p-5 text-center">
            <WandSparkles className="mx-auto size-5 text-accent" />
            <p className="mt-3 text-xs leading-5 text-fg-secondary">
              Опишите бизнес — здесь появится приложение, которое можно нажимать.
            </p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto w-full max-w-[360px] rounded-[38px] border-[7px] border-fg-primary bg-surface-raised p-3 shadow-[0_24px_60px_rgba(23,23,22,.18)]">
      <div className="mx-auto h-1 w-16 rounded-full bg-border-strong" />
      <div className="mt-3 flex min-h-[650px] flex-col overflow-hidden rounded-[24px] bg-surface-overlay">
        <div className="border-b border-border-subtle px-5 py-4">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-[10px] text-fg-secondary">Открыто в MAX</p>
              <p className="mt-0.5 text-sm font-semibold">{draft.brief.name}</p>
            </div>
            <span className="grid size-9 place-items-center rounded-full bg-accent-subtle text-accent">
              <DemoActionIcon draft={draft} />
            </span>
          </div>
        </div>

        {complete ? (
          <div className="grid flex-1 place-items-center px-7 text-center">
            <div>
              <span className="mx-auto grid size-14 place-items-center rounded-full bg-success/[.09] text-success">
                <CircleCheck className="size-7" />
              </span>
              <p className="mt-5 text-xl font-semibold">Действие выполнено</p>
              <p className="mt-2 text-sm leading-6 text-fg-secondary">
                В рабочем проекте пользователь получит подтверждение, а бизнес — событие и уведомление.
              </p>
              <button
                type="button"
                onClick={() => setComplete(false)}
                className="mt-6 inline-flex items-center gap-2 text-sm font-medium text-accent hover:text-accent-hover"
              >
                <ChevronLeft className="size-4" /> Вернуться в приложение
              </button>
            </div>
          </div>
        ) : (
          <>
            <div className="flex-1 px-5 py-6">
              <p className="text-[10px] font-medium uppercase tracking-[.14em] text-accent">{draft.preview.eyebrow}</p>
              <h2 className="mt-3 text-[28px] font-semibold leading-[1.04] tracking-[-.04em]">{draft.preview.headline}</h2>
              <p className="mt-2 text-xs leading-5 text-fg-secondary">{draft.preview.subline}</p>
              <p className="mt-7 text-[10px] text-fg-secondary">Нажмите на подходящий вариант</p>
              <div className="mt-2 space-y-2.5">
                {draft.preview.items.map((item, index) => (
                  <button
                    key={item.title}
                    type="button"
                    onClick={() => setSelected(index)}
                    className={cn(
                      "w-full rounded-[12px] border p-4 text-left transition-colors",
                      selected === index
                        ? "border-accent bg-accent-subtle"
                        : "border-border-subtle hover:border-border-strong",
                    )}
                  >
                    <span className="flex items-start justify-between gap-3">
                      <span>
                        <span className="block text-sm font-semibold">{item.title}</span>
                        <span className="mt-1 block text-[10px] leading-4 text-fg-secondary">{item.meta}</span>
                      </span>
                      <span className="shrink-0 text-xs font-medium text-accent">{item.value}</span>
                    </span>
                  </button>
                ))}
              </div>
            </div>
            <div className="border-t border-border-subtle p-4">
              <button
                type="button"
                disabled={selected === null}
                onClick={() => setComplete(true)}
                className="flex min-h-12 w-full items-center justify-center gap-2 rounded-[10px] bg-accent px-4 text-sm font-semibold text-white transition-colors hover:bg-accent-hover disabled:cursor-not-allowed disabled:opacity-40"
              >
                {draft.preview.action} <ArrowRight className="size-4" />
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

export function MaxPublicDemo() {
  const storedDraft = useMaxDemoDraft();
  const [descriptionOverride, setDescriptionOverride] = useState<string | null>(null);
  const [pendingDraft, setPendingDraft] = useState<MaxDemoDraft | null>(null);
  const [stage, setStage] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const description = descriptionOverride ?? storedDraft?.description ?? "";
  const draft = pendingDraft ? null : storedDraft;

  useEffect(() => {
    if (!pendingDraft) return;
    const timers = [
      window.setTimeout(() => setStage(1), 420),
      window.setTimeout(() => setStage(2), 840),
      window.setTimeout(() => setStage(3), 1_260),
      window.setTimeout(() => {
        saveMaxDemoDraft(pendingDraft);
        setPendingDraft(null);
        setStage(null);
      }, 1_520),
    ];
    return () => timers.forEach((timer) => window.clearTimeout(timer));
  }, [pendingDraft]);

  function generate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    try {
      const next = createMaxDemoDraft(description);
      setError(null);
      setStage(0);
      setPendingDraft(next);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Не удалось собрать демо");
    }
  }

  const generating = pendingDraft !== null;

  return (
    <main data-light-shell className="min-h-screen bg-surface-base text-fg-primary">
      <header className="border-b border-border-default bg-surface-raised">
        <div className="mx-auto flex h-16 max-w-[1280px] items-center justify-between px-5 sm:px-8">
          <div className="flex items-center gap-3">
            <BrandMark href="/max/product" />
            <span className="h-5 w-px bg-border-default" />
            <span className="text-sm text-fg-secondary">MAX Studio</span>
          </div>
          <div className="flex items-center gap-3">
            <span className="hidden items-center gap-2 text-xs text-fg-secondary sm:flex">
              <ShieldCheck className="size-3.5 text-success" /> Без регистрации
            </span>
            <Link href="/login?next=/max" className="text-sm text-accent hover:text-accent-hover">Войти</Link>
          </div>
        </div>
      </header>

      <div className="mx-auto grid max-w-[1280px] gap-12 px-5 py-10 sm:px-8 lg:grid-cols-[minmax(0,1fr)_440px] lg:items-start lg:gap-20 lg:py-16">
        <section className="lg:sticky lg:top-8">
          <Link href="/max/product" className="inline-flex items-center gap-2 text-xs text-fg-secondary hover:text-fg-primary">
            <ArrowLeft className="size-3.5" /> О продукте
          </Link>
          <p className="omnia-kicker mt-10 text-accent">Первый результат до аккаунта</p>
          <h1 className="mt-4 max-w-[720px] text-[42px] font-semibold leading-[.98] tracking-[-.055em] sm:text-[62px]">
            Опишите бизнес. Получите приложение.
          </h1>
          <p className="mt-5 max-w-[620px] text-base leading-7 text-fg-secondary">
            Покажем рабочий сценарий на тестовых данных. Ничего регистрировать и подключать пока не нужно.
          </p>

          <form onSubmit={generate} className="mt-8 max-w-[720px] rounded-[14px] border border-border-default bg-surface-raised p-5 sm:p-6">
            <label htmlFor="max-demo-description" className="text-sm font-medium">
              Что должен уметь ваш MAX-сервис?
            </label>
            <Textarea
              id="max-demo-description"
              value={description}
              onChange={(event) => setDescriptionOverride(event.target.value)}
              disabled={generating}
              maxLength={600}
              placeholder="Например: кофейня «Смена» — меню, заказ навынос и бонусы"
              className="mt-3 min-h-32 resize-none border-border-default bg-surface-input text-base leading-6"
            />
            <div className="mt-3 flex flex-wrap gap-2">
              {EXAMPLES.map((example, index) => (
                <button
                  key={example}
                  type="button"
                  disabled={generating}
                  onClick={() => setDescriptionOverride(example)}
                  className="rounded-full border border-border-default px-3 py-1.5 text-left text-[11px] text-fg-secondary transition-colors hover:border-border-strong hover:text-fg-primary disabled:opacity-50"
                >
                  Пример {index + 1}
                </button>
              ))}
            </div>
            {error && <p role="alert" className="mt-4 text-sm text-danger">{error}</p>}
            <p className="mt-4 text-[11px] leading-5 text-fg-secondary">
              Не вводите пароли, токены и данные клиентов. Здесь достаточно описания бизнеса и желаемых действий.
            </p>
            <Button disabled={generating || description.trim().length < 10} className="mt-5 min-h-12 w-full sm:w-auto">
              <Sparkles className="size-4" />
              {generating ? "Собираем демо…" : draft ? "Пересобрать демо" : "Показать моё приложение"}
            </Button>
          </form>

          {draft && (
            <section className="mt-5 max-w-[720px] rounded-[14px] border border-accent/30 bg-accent-subtle p-5 sm:flex sm:items-center sm:justify-between sm:gap-6 sm:p-6">
              <div>
                <p className="text-sm font-semibold">Демо сохранено в этом браузере</p>
                <p className="mt-1 text-xs leading-5 text-fg-primary/80">
                  После проверки Studio даст пробные реальные сборки и код. Подписка откроет дальнейшие правки, публикацию и интеграции.
                </p>
              </div>
              <Link href="/max/register?from=demo" className="omnia-button omnia-button-primary mt-4 min-h-11 shrink-0 px-5 sm:mt-0">
                Забрать проект <ArrowRight className="size-4" />
              </Link>
            </section>
          )}

          <div className="mt-7 grid max-w-[720px] gap-3 text-xs text-fg-secondary sm:grid-cols-3">
            {[
              [WandSparkles, "Интерактивный результат, не картинка"],
              [ShieldCheck, "Демо не расходует пробные сборки"],
              [Check, "Код — после регистрации и первой сборки"],
            ].map(([Icon, text]) => {
              const ItemIcon = Icon as typeof Check;
              return <p key={String(text)} className="flex items-start gap-2"><ItemIcon className="mt-0.5 size-3.5 shrink-0 text-accent" />{String(text)}</p>;
            })}
          </div>
        </section>

        <section aria-label="Предпросмотр MAX-приложения">
          <div className="mb-4 flex items-center justify-between text-xs text-fg-secondary">
            <span>Интерактивный предпросмотр</span>
            {draft && <span className="rounded-full bg-success/[.08] px-2.5 py-1 font-medium text-fg-primary">Готово</span>}
          </div>
          <DemoPhone key={draft?.createdAt ?? "empty"} draft={draft} stage={stage} />
          <p className="mx-auto mt-4 max-w-[360px] text-center text-[10px] leading-4 text-fg-secondary">
            Демо показывает будущий пользовательский сценарий. Реальные платежи, заказы и данные подключаются после создания аккаунта.
          </p>
        </section>
      </div>
    </main>
  );
}
