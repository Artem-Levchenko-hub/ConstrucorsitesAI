"use client";

import { useId, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, FileCheck2, Loader2, Settings2, CircleAlert, Circle } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { LearningButton } from "@/components/learning/ProductLearning";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { ApiError } from "@/lib/api/client";
import {
  getMaxProjectConfig,
  getMaxProjectContent,
  saveMaxProjectConfig,
} from "@/lib/api/max-studio";
import type { MaxContentDelivery, MaxProjectConfig, MaxProjectConfigPayload } from "@/lib/api/types";
import { MAX_BRIEF_LENGTH } from "@/lib/max-brief";
import { MAX_STATUS_COPY } from "@/lib/max-status-copy";
import { cn } from "@/lib/utils";
import "./max-studio.css";
import "./max-project-workspace.css";
import "./max-project-setup.css";
import { MaxProjectSetupSections, type SetupSection } from "./MaxProjectSetupSections";
import { MaxProjectDataApplyDialog } from "./MaxProjectDataApplyDialog";
import { useMaxWorkspaceDraftGuard } from "./use-max-workspace-draft-guard";

const SETUP_SECTIONS: Array<{ id: SetupSection; label: string }> = [
  { id: "details", label: "Основное" },
  { id: "content", label: "Контент" },
  { id: "owner", label: "Документы" },
  { id: "policies", label: "Политики" },
];
const PAGE_SETUP_SECTIONS = SETUP_SECTIONS.filter(item => item.id !== "content");
const deliveryCopy: Record<MaxContentDelivery["state"], string> = {
  applied: "Применение подтверждено",
  pending: "Применяются",
  unknown: "Применение не подтверждено",
  not_published: "Ещё не опубликовано",
  blocked: "Применение приостановлено",
  deferred: "Применятся после пробуждения",
  not_created: "Ещё не создан",
};
const deliveryTargets = [
  { id: "public", label: "Опубликованное приложение" },
  { id: "preview", label: "Предпросмотр" },
] as const;
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
type ConfigDraft = { baseline: MaxProjectConfig; payload: MaxProjectConfigPayload };

function rebaseFields<T extends object>(baseline: T, edited: T, latest: T) {
  const value = { ...latest };
  let overlaps = false;
  for (const key of Object.keys(edited) as Array<keyof T>) {
    if (same(baseline[key], edited[key])) continue;
    if (!same(latest[key], baseline[key]) && !same(latest[key], edited[key])) overlaps = true;
    value[key] = edited[key];
  }
  return { value, overlaps };
}

function rebaseDraft({ baseline, payload }: ConfigDraft, latest: MaxProjectConfigPayload) {
  const { operator: oldOperator, support: oldSupport, legal: oldLegal, ...oldRest } = baseline.config;
  const { operator, support, legal, ...editedRest } = payload;
  const { operator: latestOperator, support: latestSupport, legal: latestLegal, ...latestRest } = latest;
  const rest = rebaseFields(oldRest, editedRest, latestRest);
  const nextOperator = rebaseFields(oldOperator, operator, latestOperator);
  const nextSupport = rebaseFields(oldSupport, support, latestSupport);
  const nextLegal = rebaseFields(oldLegal, legal, latestLegal);
  return {
    payload: { ...latest, ...rest.value, operator: nextOperator.value, support: nextSupport.value, legal: nextLegal.value },
    overlaps: rest.overlaps || nextOperator.overlaps || nextSupport.overlaps || nextLegal.overlaps,
  };
}

function configConflict(error: unknown) {
  return error instanceof ApiError && error.status === 409 && error.code === "conflict" && error.details?.reason === "config_version_conflict";
}

function savedWithoutDelivery(error: unknown) {
  return error instanceof ApiError && (error.details?.saved === true || (
    error.code === "orchestrator_unavailable" && error.message.startsWith("Данные сохранены на сервере, но ещё не применены.")
  ));
}

function errorMessage(error: unknown) {
  return error instanceof ApiError ? error.message : "Попробуйте ещё раз";
}

function screenConfig(config: MaxProjectConfigPayload) {
  const { app_name, app_type, summary, audience, primary_action, features, style, brand_colors } = config;
  return { app_name, app_type, summary, audience, primary_action, features, style, brand_colors };
}

export function MaxProjectSetupDialog({
  projectId,
  display = "panel",
  emphasized = false,
  label = "Данные приложения",
  open: controlledOpen,
  onOpenChange,
  initialSection = "details",
}: {
  projectId: string;
  display?: "panel" | "toolbar" | "header" | "page";
  emphasized?: boolean;
  label?: string;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  initialSection?: SetupSection;
}) {
  const qc = useQueryClient();
  const tabsId = useId();
  const isPage = display === "page";
  const sections = isPage ? PAGE_SETUP_SECTIONS : SETUP_SECTIONS;
  const entrySection = isPage && initialSection === "content" ? "details" : initialSection;
  const scrollRegion = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const pageApplyTrigger = useRef<HTMLButtonElement>(null);
  const [localOpen, setLocalOpen] = useState(false);
  const open = isPage || (controlledOpen ?? localOpen);
  const setOpen = (next: boolean) => {
    if (isPage) return;
    setLocalOpen(next);
    onOpenChange?.(next);
  };
  const [draft, setDraft] = useState<ConfigDraft | null>(null);
  const [latest, setLatest] = useState<MaxProjectConfig | null>(null);
  const [aiConfig, setAiConfig] = useState<MaxProjectConfig | null>(null);
  const openAiAfterSave = useRef(false);
  const [section, setSection] = useState<SetupSection>(entrySection);
  const [entry, setEntry] = useState({ open, entrySection, projectId });
  if (entry.open !== open || entry.entrySection !== entrySection || entry.projectId !== projectId) {
    setEntry({ open, entrySection, projectId });
    if (entry.projectId !== projectId) {
      setDraft(null);
      setLatest(null);
      setAiConfig(null);
    }
    if (open) {
      setSection(entrySection);
      setAiConfig(null);
    }
    else if (entry.open) { setDraft(null); setLatest(null); }
  }
  const config = useQuery({
    queryKey: ["max-config", projectId],
    queryFn: () => getMaxProjectConfig(projectId),
    enabled: open,
  });

  const current = draft?.payload ?? config.data?.config ?? null;
  const version = config.data?.config_version;
  const delivery = useQuery({
    queryKey: ["max-content", projectId], queryFn: () => getMaxProjectContent(projectId),
    enabled: isPage && version !== undefined && version > 0, retry: false,
    refetchInterval: query => isPage && deliveryTargets.some(({ id }) =>
      ["pending", "unknown"].includes(query.state.data?.[id].state ?? "")) ? 5_000 : false,
  });
  const deliveryStates = deliveryTargets.map(({ id, label }) => {
    const status = delivery.isSuccess && delivery.data.config_version === version ? delivery.data[id] : undefined;
    const state = status && status.desired_config_version === version &&
      (status.state !== "applied" || status.confirmed_config_version === version) ? status.state : "unknown";
    return { id, label, state };
  });
  const pendingApplication = config.data?.application_mode === "runtime"
    && config.data.config_version > 0 && !config.data.synced_snapshot_id;

  const save = useMutation({
    mutationFn: ({ payload, expectedVersion }: { payload: MaxProjectConfigPayload; expectedVersion: number }) =>
      saveMaxProjectConfig(projectId, payload, expectedVersion),
    onSuccess: async (data) => {
      await qc.cancelQueries({ queryKey: ["max-config", projectId] });
      qc.setQueryData(["max-config", projectId], data);
      void qc.invalidateQueries({ queryKey: ["max-readiness", projectId] });
      void qc.invalidateQueries({ queryKey: ["snapshots", projectId] });
      void qc.invalidateQueries({ queryKey: ["max-preview-session", projectId] });
      void qc.invalidateQueries({ queryKey: ["max-content", projectId] });
      toast.success("Данные сохранены", {
        id: `max-config-save-${projectId}`,
        description: data.application_mode === "runtime"
          ? (data.synced_snapshot_id
            ? "Доставка данных в предпросмотр подтверждена. Подключённые экраны получат изменения. Статус публикации показан в разделе «Данные приложения». ИИ не запускался."
            : "Данные сохранены. Доставка в предпросмотр ещё не подтверждена. ИИ не запускался.")
          : "Данные записаны в исходники. Проверьте предпросмотр. ИИ не запускался.",
      });
      if (openAiAfterSave.current) setAiConfig(data);
      openAiAfterSave.current = false;
      setOpen(false);
      setDraft(null);
      setLatest(null);
    },
    onError: async (error, attempt) => {
      openAiAfterSave.current = false;
      if (error instanceof ApiError && error.code === "generation_draining") {
        toast.info("Сервис обновляется", { id: `max-config-save-${projectId}`, description: "Подождите несколько минут и повторите сохранение. Введённые данные останутся в форме.", duration: 8000 });
        return;
      }
      if (savedWithoutDelivery(error)) {
        toast.info("Данные сохранены", { id: `max-config-save-${projectId}`, description: errorMessage(error), duration: 8000 });
        if (isPage) {
          const result = await config.refetch();
          if (result.isSuccess && (!draft || same(rebaseDraft({ ...draft, payload: attempt.payload }, result.data.config).payload, result.data.config))) {
            setDraft(null);
            setLatest(null);
          }
          void qc.invalidateQueries({ queryKey: ["max-content", projectId] });
        }
        return;
      }
      if (error instanceof ApiError && error.status === 409 && error.message.includes("Дождитесь завершения сборки")) {
        toast.info("Сборка ещё выполняется", { id: `max-config-save-${projectId}`, description: error.message, duration: 8000 });
        return;
      }
      toast.error("Не удалось сохранить", { id: `max-config-save-${projectId}`, description: errorMessage(error), duration: 8000 });
    },
  });
  // Публикацию блокирует ровно одно поле данных — подтверждение документов.
  const termsAccepted = Boolean(current?.legal.terms_accepted);
  const saved = draft?.baseline.config ?? config.data?.config;
  const changedSections = current && saved
    ? [
        !same(screenConfig(current), screenConfig(saved)),
        !same(current.content, saved.content),
        !same({ operator: current.operator, support: current.support }, { operator: saved.operator, support: saved.support }),
        !same(current.legal, saved.legal),
      ].filter(Boolean).length
    : 0;
  const reapply = isPage && changedSections === 0 && version !== undefined && version > 0
    && !delivery.isPending && deliveryStates.some(({ state }) => state === "unknown" || state === "pending" || state === "blocked" || state === "deferred");
  const conflict = isPage && configConflict(save.error);
  const rebased = latest && draft ? rebaseDraft(draft, latest.config) : null;
  useMaxWorkspaceDraftGuard(isPage && changedSections > 0, isPage && save.isPending);

  const saveIssue = current && (!current.app_name.trim() || !current.summary.trim())
    ? "Для сохранения заполните название и описание в разделе «Основное»."
    : current && Array.from(current.summary.trim()).length > MAX_BRIEF_LENGTH
      ? "Сократите описание в разделе «Основное» до 20 000 символов."
      : null;
  const selectSection = (next: SetupSection) => {
    setSection(next);
    if (scrollRegion.current) scrollRegion.current.scrollTop = 0;
  };
  const submit = (withAi: boolean) => {
    if (!config.data || save.isPending || conflict) return;
    const activeDraft = changedSections ? draft : null;
    const payload = activeDraft?.payload ?? config.data.config;
    openAiAfterSave.current = withAi;
    save.mutate({
      payload: isPage && activeDraft ? {
        ...payload, app_name: payload.app_name.trim(), summary: payload.summary.trim(),
        support: { ...payload.support, email: payload.support.email?.trim() || null },
      } : payload,
      expectedVersion: activeDraft?.baseline.config_version ?? config.data.config_version,
    });
  };
  const requestScreenChanges = () => {
    if (changedSections || pendingApplication || !config.data?.config_version || config.data.application_mode !== "runtime") {
      submit(true);
    } else if (config.data) {
      setOpen(false);
      setAiConfig(config.data);
    }
  };
  const loadLatest = async () => {
    const result = await config.refetch();
    if (result.isSuccess) {
      if (draft && changedSections) setLatest(result.data);
      else { setDraft(null); save.reset(); }
    }
    else toast.error("Не удалось загрузить актуальные данные", { description: "Правки остались в форме. Попробуйте ещё раз." });
  };

  const description = termsAccepted
    ? "Всё, что нужно для публикации, заполнено. Остальные разделы — по желанию: они делают приложение подробнее, но не блокируют запуск."
    : "Для публикации нужно одно: подтвердить документы приложения во вкладке «Политики». Реквизиты не нужны — их проверяет MAX, остальные разделы можно заполнить позже.";

  const body = (
    <>
      <div className="max-setup-tabs">
        <div role="tablist" aria-label="Разделы данных приложения">
          {sections.map((item) => (
            <button
              key={item.id}
              type="button"
              role="tab"
              id={`${tabsId}-${item.id}`}
              aria-controls={`${tabsId}-panel`}
              aria-selected={section === item.id}
              tabIndex={section === item.id ? 0 : -1}
              onClick={() => selectSection(item.id)}
              onKeyDown={(event) => {
                if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
                event.preventDefault();
                const index = sections.findIndex(tab => tab.id === item.id);
                const next = event.key === "Home" ? 0 : event.key === "End" ? sections.length - 1 : (index + (event.key === "ArrowRight" ? 1 : -1) + sections.length) % sections.length;
                selectSection(sections[next].id);
                document.getElementById(`${tabsId}-${sections[next].id}`)?.focus();
              }}
              className="max-setup-tab"
              data-required={item.id === "policies" ? (termsAccepted ? "done" : "pending") : undefined}
            >
              {item.label}
              {item.id === "policies" && (
                <span
                  className="max-setup-tab-mark"
                  data-state={termsAccepted ? "done" : "pending"}
                  aria-label={termsAccepted ? "Документы подтверждены" : "Нужно для публикации"}
                  title={termsAccepted ? "Документы подтверждены" : "Нужно для публикации"}
                >
                  {termsAccepted ? <Check className="size-3" aria-hidden="true" /> : null}
                </span>
              )}
            </button>
          ))}
        </div>
      </div>

      {config.isError && !current ? (
        <div role="alert" className="p-6 text-sm"><p><strong>{MAX_STATUS_COPY.config.title}.</strong> {MAX_STATUS_COPY.config.hint}</p><Button variant="outline" className="mt-3" onClick={() => void config.refetch()}>{MAX_STATUS_COPY.config.retry}</Button></div>
      ) : config.isLoading || !current ? (
        <div role="status" className="max-setup-loading">
          <Loader2 aria-hidden="true" className="h-5 w-5 animate-spin text-accent" />
          {config.fetchStatus === "paused" ? "Нет соединения. Подключитесь к интернету, чтобы открыть данные." : "Загружаем данные приложения…"}
        </div>
      ) : (
        <>
          <div
            className="max-setup-body"
            ref={scrollRegion}
            role="tabpanel"
            id={`${tabsId}-panel`}
            aria-labelledby={`${tabsId}-${section}`}
            data-testid="max-settings-scroll-region"
          >
            <fieldset className="max-setup-form-fields" disabled={save.isPending}>
              <MaxProjectSetupSections key={section} section={section} projectId={projectId} current={current} showCatalogLink={isPage} onChange={next => {
                const baseline = config.data;
                if (!baseline || save.isPending) return;
                // Capture only on first edit: a background GET cannot rebase a dirty draft.
                setDraft(previous => ({ baseline: previous?.baseline ?? baseline, payload: next }));
              }} />
            </fieldset>
          </div>

          <div
            className="max-setup-footer"
            data-testid="max-settings-footer"
          >
            {isPage && version !== undefined && version > 0 && <div className="max-setup-delivery" role="status" aria-label="Доставка данных">
              <p><Check aria-hidden="true" /> Данные сохранены в Yleum. Версия {version}.</p>
              <dl>{deliveryStates.map(({ id, label, state }) => <div key={id} data-state={state}>
                <dt>{label}</dt><dd>{delivery.isPending ? "Проверяем доставку…" : deliveryCopy[state]}</dd>
              </div>)}</dl>
            </div>}
            {conflict && <div role="alert" className="max-setup-conflict">
              <p>{rebased
                ? (rebased.overlaps ? "Некоторые поля изменены в другом окне. Если перенесёте свои правки, проверьте их перед сохранением."
                  : "Актуальная версия загружена. Можно перенести в неё только изменённые вами поля.")
                : "Данные изменились в другом окне. Черновик остался в форме. Загрузите актуальную версию."}</p>
              {latest && rebased ? <Button type="button" variant="outline" className="max-setup-save" onClick={() => {
                setDraft({ baseline: latest, payload: rebased.payload }); setLatest(null); save.reset();
              }}>Перенести мои правки</Button> : <Button type="button" variant="outline" className="max-setup-save" disabled={config.isFetching}
                onClick={() => void loadLatest()}>Загрузить актуальные данные</Button>}
            </div>}
            {isPage && save.isError && !conflict && (!savedWithoutDelivery(save.error) || reapply || delivery.isPending || changedSections > 0) && <p role="alert" className="max-setup-conflict">
              {savedWithoutDelivery(save.error) ? "Данные сохранены. Доставка ещё не подтверждена." : errorMessage(save.error)}
            </p>}
            <p id={`${tabsId}-save-status`} className="max-setup-save-status" role="status" data-state={saveIssue ? "invalid" : changedSections ? "changed" : "saved"}>
              {saveIssue ? <CircleAlert aria-hidden="true" /> : changedSections ? <Circle aria-hidden="true" /> : <Check aria-hidden="true" />}
              <span>{saveIssue ?? (save.isPending ? "Сохраняем изменения…" : changedSections > 0
                ? `Есть несохранённые изменения · разделов: ${changedSections}`
                : !isPage && pendingApplication ? "Данные сохранены. Доставка в предпросмотр ещё не подтверждена." : "Все изменения сохранены")}</span>
            </p>
            <Button
              className="max-setup-save"
              aria-describedby={`${tabsId}-save-status`}
              disabled={
                save.isPending ||
                conflict ||
                (changedSections === 0 && (isPage ? !reapply : !pendingApplication)) ||
                Boolean(saveIssue)
              }
              onClick={() => submit(false)}
            >
              {save.isPending && (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              )}
              {reapply ? "Повторить применение" : "Сохранить изменения"}
            </Button>
            {isPage && changedSections > 0 && <Button type="button" variant="ghost" className="max-setup-save" disabled={save.isPending} onClick={() => {
              setDraft(null); setLatest(null); save.reset();
              if (conflict && !latest) void config.refetch();
              document.getElementById(`${tabsId}-${section}`)?.focus();
            }}>Отменить правки</Button>}
            {(section === "details" || section === "content") && <Button ref={isPage ? pageApplyTrigger : undefined} variant="outline" className="max-setup-save" disabled={save.isPending || conflict || Boolean(saveIssue)} onClick={requestScreenChanges}>Изменить экраны с ИИ</Button>}
          </div>
        </>
      )}
    </>
  );

  const applyDialog = aiConfig && (
    <MaxProjectDataApplyDialog config={aiConfig} onClose={() => setAiConfig(null)}
      onReturnFocus={() => {
        const target = isPage ? pageApplyTrigger.current ?? document.getElementById(`${tabsId}-${section}`) : trigger.current;
        target?.focus();
      }} />
  );

  if (isPage) {
    return (
      <>
        <section className="max-project-setup max-project-setup-page" aria-label="Редактирование данных приложения">
          <p className="max-setup-page-context">{description}</p>
          {body}
        </section>
        {applyDialog}
      </>
    );
  }

  return (
    <>
      <Dialog open={open} onOpenChange={(next) => {
        setOpen(next);
        if (!next) setDraft(null);
      }}>
      <DialogTrigger asChild>
      <Button
        ref={trigger}
        size="sm"
        variant={emphasized ? "primary" : "outline"}
        aria-label={label}
        className={
          display === "header" ? "max-editor-data-button" : display === "panel"
            ? cn(
                "h-11 min-w-0 w-full gap-1.5 overflow-hidden rounded-lg px-2 text-[11px]",
                !emphasized &&
                  "border-border-default bg-surface text-fg-secondary hover:bg-surface-base",
              )
            : "h-11 gap-1.5 px-2.5 text-xs sm:h-7"
        }
        onClick={() => {
          setSection(initialSection);
          setDraft(null);
        }}
        data-testid="max-settings-open"
      >
        <Settings2 className="h-3.5 w-3.5" />
        <span className="min-w-0 truncate">
          {display === "header" ? "Данные" : display === "panel" ? label : "Настройки"}
        </span>
      </Button>
      </DialogTrigger>
        <DialogContent
          data-product-shell
          data-max-studio
          data-learning-context="details"
          className="max-settings-dialog max-project-setup"
        >
          <DialogHeader className="max-setup-header">
            <DialogTitle className="max-setup-title">
              <FileCheck2 className="h-5 w-5 text-accent" />
              Данные приложения
            </DialogTitle>
            <LearningButton className="self-start" />
            <DialogDescription className="max-setup-description">{description}</DialogDescription>
          </DialogHeader>

          {body}
        </DialogContent>
      </Dialog>
      {applyDialog}
    </>
  );
}
