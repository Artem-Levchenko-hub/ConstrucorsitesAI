"use client";

import { useCallback, useEffect, useId, useImperativeHandle, useRef, useState, type ComponentProps, type ReactNode, type Ref } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Clock, ImagePlus, Loader2, Mic, Send, Square, StopCircle, Type, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useVoiceInput } from "@/hooks/useVoiceInput";
import { EASE_OUT } from "@/lib/motion";
import { cn } from "@/lib/utils";
import type { MessageFontSelection, ReferenceImage, SelectedElement } from "@/lib/api/types";
import { useReferenceImages } from "@/hooks/useReferenceImages";
import { useFontAttachments } from "@/hooks/useFontAttachments";
import { FONT_LIMITS, isFontFile } from "@/lib/api/font-assets";
import { REFERENCE_IMAGE_ACCEPT, REFERENCE_IMAGE_LIMITS } from "@/lib/api/reference-images";
import { useInspectorStore } from "@/store/inspector";
import { SelectedChips } from "./SelectedChips";
import { FontAttachmentChips, FontAttachmentPanel } from "./FontAttachments";

export type PromptInputHandle = { insertDraft: (text: string) => boolean };
export type PromptReferenceSubmit = { referenceImages: ReferenceImage[]; fontSelections?: MessageFontSelection[]; idempotencyKey: string };
export type PromptInputSubmitResult = boolean | { accepted: false; reason: "reference_images_expired" };

export function PromptInput(props: ComponentProps<typeof ProjectPromptInput>) {
  // A project switch owns a new draft and upload lifecycle, including pending sends.
  return <ProjectPromptInput key={props.projectId ?? "no-project"} {...props} />;
}

function ProjectPromptInput({
  onSubmit,
  onCancel,
  onCancelPending,
  isStreaming,
  pendingPrompt,
  className,
  textareaRef,
  placeholder,
  ariaLabel = "Опишите изменение проекта",
  draftRef,
  toolbarAction,
  sendDisabled = false,
  quotaNotice,
  projectId,
}: {
  onSubmit: (
    text: string,
    selections: SelectedElement[],
    references?: PromptReferenceSubmit,
  ) => PromptInputSubmitResult | Promise<PromptInputSubmitResult>;
  onCancel: () => void;
  onCancelPending: () => void;
  isStreaming: boolean;
  pendingPrompt: string | null;
  className?: string;
  placeholder?: string;
  ariaLabel?: string;
  // Optional lifted ref so the parent can focus the input — used by the
  // discovery "Другое" chip to hand the user the free-text field. Falls back to
  // an internal ref when omitted, so existing call sites are unchanged.
  textareaRef?: React.RefObject<HTMLTextAreaElement | null>;
  draftRef?: Ref<PromptInputHandle>;
  toolbarAction?: ReactNode;
  sendDisabled?: boolean;
  quotaNotice?: ReactNode;
  projectId?: string;
}) {
  const [value, setValue] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const internalRef = useRef<HTMLTextAreaElement>(null);
  const ref = textareaRef ?? internalRef;
  // Synchronous per-gesture latch. React state updates after the event, so
  // without this a duplicated key/click event can call onSubmit twice with the
  // same still-captured textarea value.
  const sendLockRef = useRef(false);
  const filesRef = useRef<HTMLInputElement>(null);
  const references = useReferenceImages(projectId);
  const fonts = useFontAttachments(projectId);
  const [fontPanelOpen, setFontPanelOpen] = useState(false);
  const fontPanelId = useId();
  const submissionEpoch = useRef(0);
  const retryIdentity = useRef<{ signature: string; key: string } | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const attachmentsBlocked = references.busy || fonts.busy
    || references.items.some(item => !item.image) || fonts.items.some(item => !item.asset);
  const hasAttachments = references.items.length > 0 || fonts.items.length > 0;

  useEffect(() => () => { submissionEpoch.current += 1; }, []);

  const addAttachments = (files: File[]) => {
    if (isSubmitting) return;
    const fontFiles = files.filter(isFontFile);
    const imageFiles = files.filter(file => !isFontFile(file));
    if (imageFiles.length) void references.add(imageFiles);
    if (fontFiles.length) void fonts.addFiles(fontFiles);
  };

  useImperativeHandle(draftRef, () => ({
    insertDraft(text) {
      if (sendLockRef.current || !text.trim()) return false;
      setValue((draft) => draft.trim() ? `${draft}\n\n${text.trim()}` : text.trim());
      ref.current?.focus();
      return true;
    },
  }), [ref]);

  // Voice dictation → drop the transcript into the box (review-first), append to
  // anything already typed, then focus so the user can edit and send.
  const voice = useVoiceInput((text) => {
    setValue((v) => (v.trim() ? `${v.trim()} ${text}` : text));
    requestAnimationFrame(() => ref.current?.focus());
  });

  const selections = useInspectorStore((s) => s.selections);
  const setComment = useInspectorStore((s) => s.setComment);
  const removeSelection = useInspectorStore((s) => s.removeSelection);
  const clearSelections = useInspectorStore((s) => s.clear);

  // Auto-resize textarea up to a sensible max.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "0px";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [value, ref]);

  const send = useCallback(async () => {
    if (sendLockRef.current || sendDisabled || attachmentsBlocked) return;
    const text = value.trim();
    // Fresh from the store — the global Ctrl+Enter handler can be a stale closure.
    const picks = useInspectorStore.getState().selections;
    const wire: SelectedElement[] = picks.map(({ id: _id, ...rest }) => rest);
    // Allow sending with only picks: the per-element comments carry the intent,
    // so synthesize a prompt (backend requires non-empty text + reads naturally).
    const finalText =
      text ||
      (wire.length
        ? "Внеси правки по выделенным элементам — что сделать, написано в комментарии к каждому."
        : fonts.items.length
          ? references.items.length ? "Примени приложенные шрифты и используй изображения как визуальный референс приложения." : "Примени приложенные шрифты к приложению."
          : references.items.length ? "Используй прикреплённые изображения как визуальный референс для приложения." : "");
    if (!finalText) return;
    sendLockRef.current = true;
    setIsSubmitting(true);
    setSubmitError(null);
    const epoch = submissionEpoch.current;
    const images = references.items.flatMap((item) => item.image ? [item.image] : []);
    const fontSelections = fonts.items.flatMap(item => item.asset ? [{ ...item.asset, role: item.role }] : []);
    const signature = JSON.stringify([finalText, wire, images.map((image) => image.id), fontSelections.map(font => [font.id, font.role])]);
    if (retryIdentity.current?.signature !== signature) retryIdentity.current = { signature, key: crypto.randomUUID() };
    try {
      const submitted = await onSubmit(finalText, wire, images.length || fontSelections.length ? {
        referenceImages: images,
        ...(fontSelections.length ? { fontSelections } : {}),
        idempotencyKey: retryIdentity.current.key,
      } : undefined);
      if (submissionEpoch.current !== epoch) return;
      if (submitted === true) {
        setValue("");
        clearSelections();
        references.clear();
        fonts.clear();
        retryIdentity.current = null;
      } else if (submitted && submitted.reason === "reference_images_expired") {
        // Never renew IDs for timeouts/network failures: the old request may
        // already exist. This typed refusal alone permits a new identity.
        retryIdentity.current = null;
        await references.reuploadExpired();
        setSubmitError("Срок хранения вложений истёк. Проверьте повторную загрузку и отправьте запрос ещё раз.");
      }
    } catch {
      if (submissionEpoch.current === epoch) setSubmitError("Не удалось подтвердить отправку. Повторите попытку — вложения сохранены.");
    } finally {
      if (submissionEpoch.current === epoch) setIsSubmitting(false);
      // Keep the latch through the current browser event turn. A legitimate
      // later submit uses the freshly-rendered value and is not blocked.
      queueMicrotask(() => {
        if (submissionEpoch.current === epoch) sendLockRef.current = false;
      });
    }
  }, [attachmentsBlocked, clearSelections, onSubmit, sendDisabled, value, references, fonts]);

  // Cmd/Ctrl+Enter from anywhere submits — даже во время стрима (отправка
  // в этом случае ставится в очередь хуком).
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const target = e.target;
      // The textarea owns Enter/Ctrl+Enter itself. Letting this window listener
      // handle the same bubbling event used to create a second identical submit.
      if (
        target instanceof HTMLTextAreaElement ||
        target instanceof HTMLInputElement ||
        (target instanceof HTMLElement && target.isContentEditable)
      ) {
        return;
      }
      if (
        (e.metaKey || e.ctrlKey) &&
        e.key === "Enter" &&
        (value.trim() || hasAttachments || useInspectorStore.getState().selections.length)
      ) {
        void send();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [send, value, hasAttachments]);

  const onKeyDown: React.KeyboardEventHandler<HTMLTextAreaElement> = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      e.stopPropagation();
      void send();
    }
  };

  return (
    <div
      aria-busy={isSubmitting}
      onDragOver={(event) => { if (projectId && event.dataTransfer.types.includes("Files")) event.preventDefault(); }}
      onDrop={(event) => {
        if (!projectId || !event.dataTransfer.files.length) return;
        event.preventDefault(); event.stopPropagation();
        addAttachments(Array.from(event.dataTransfer.files));
      }}
      className={cn(
        "bg-surface-panel-dark p-3 space-y-2",
        className,
      )}
    >
      {quotaNotice}
      {(references.error || fonts.error || submitError) && <p role="alert" className="text-xs text-danger">{references.error || fonts.error || submitError}</p>}
      {projectId && fontPanelOpen && <div id={fontPanelId}><FontAttachmentPanel key={projectId} fonts={fonts} disabled={isSubmitting} /></div>}
      <FontAttachmentChips fonts={fonts} disabled={isSubmitting} />
      {!!references.items.length && <ul aria-label="Изображения к запросу" className="flex flex-wrap gap-2">
        {references.items.map((item) => <li key={item.key} className="relative w-28 rounded-lg border border-border-subtle p-1">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={item.preview} alt={item.file.name} className="h-20 w-full rounded object-cover" />
          <button type="button" aria-label={`Удалить ${item.file.name}`} disabled={isSubmitting || references.busy}
            onClick={() => references.remove(item.key)} className="absolute right-0 top-0 flex h-11 w-11 items-center justify-center rounded-full bg-surface-panel-dark"><X className="h-4 w-4" /></button>
          <p className="truncate text-xs" title={item.file.name}>{item.file.name}</p>
          {item.error ? <><p role="alert" className="text-xs text-danger">{item.error}</p><button type="button" disabled={references.busy || isSubmitting} onClick={() => void references.retry(item.key)} className="min-h-11 text-xs underline">Повторить загрузку</button></>
            : !item.image && <span role="status" className="text-xs">Загрузка…</span>}
        </li>)}
      </ul>}
      <AnimatePresence initial={false}>
        {pendingPrompt && (
          <motion.div
            initial={{ opacity: 0, height: 0, y: -4 }}
            animate={{ opacity: 1, height: "auto", y: 0 }}
            exit={{ opacity: 0, height: 0, y: -4 }}
            transition={{ duration: 0.22, ease: EASE_OUT }}
            className="overflow-hidden"
          >
            <div className="flex items-center gap-2 rounded-md border border-border-subtle bg-surface-raised px-2.5 py-1.5 text-xs">
              <Clock className="h-3.5 w-3.5 text-fg-tertiary shrink-0" />
              <span className="text-fg-secondary shrink-0">В очереди:</span>
              <span className="text-fg-primary truncate flex-1 min-w-0">
                {pendingPrompt}
              </span>
              <button
                type="button"
                onClick={onCancelPending}
                title="Убрать из очереди"
                className="text-fg-tertiary hover:text-fg-primary transition-colors shrink-0"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      <SelectedChips
        items={selections}
        onComment={setComment}
        onRemove={removeSelection}
      />

      <div className="rounded-lg border border-border-default bg-surface-input transition-all focus-within:border-accent focus-within:shadow-[0_0_0_3px_rgba(79,129,247,0.13)]">
        <textarea
          ref={ref}
          aria-label={ariaLabel}
          value={value}
          disabled={isSubmitting}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={onKeyDown}
          onPaste={(event) => {
            if (!projectId) return;
            const files = Array.from(event.clipboardData.items).filter(item => item.kind === "file")
              .flatMap(item => { const file = item.getAsFile(); return file && (file.type.startsWith("image/") || isFontFile(file)) ? [file] : []; });
            if (!files.length) return;
            event.preventDefault(); event.stopPropagation();
            addAttachments(files);
          }}
          placeholder={
            isStreaming
              ? "Можно писать следующий — отправится после текущей генерации"
              : selections.length
                ? "Общий комментарий (необязательно) — правки по элементам уже заданы…"
                : placeholder ?? "Опишите, что изменить или добавить…"
          }
          rows={2}
          className="w-full bg-transparent px-3.5 py-3 text-sm text-fg-primary placeholder:text-fg-tertiary resize-none focus:outline-none"
        />

        <div className="flex items-center justify-between px-2.5 pb-2.5 gap-2">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
          {projectId && <><input ref={filesRef} type="file" multiple accept={REFERENCE_IMAGE_ACCEPT} className="sr-only" tabIndex={-1} aria-label="Выбрать изображения"
            onChange={(event) => { const files = Array.from(event.target.files ?? []); event.target.value = ""; void references.add(files); }} />
            <Button type="button" variant="secondary" size="sm" aria-label="Прикрепить изображения" title={`PNG, JPEG, WebP · до ${REFERENCE_IMAGE_LIMITS.count} файлов по ${REFERENCE_IMAGE_LIMITS.bytes / 1024 / 1024} МБ`} className="h-11 px-2.5 sm:h-8"
              disabled={isSubmitting || references.busy || references.items.length >= REFERENCE_IMAGE_LIMITS.count} onClick={() => filesRef.current?.click()}>
              {references.busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <ImagePlus className="h-4 w-4" />}
            </Button></>}
          {projectId && <Button type="button" variant="secondary" size="sm" aria-label="Прикрепить шрифт" title="Выбрать шрифт или загрузить файл"
            aria-expanded={fontPanelOpen} aria-controls={fontPanelOpen ? fontPanelId : undefined}
            disabled={isSubmitting || fonts.busy || fonts.items.length >= FONT_LIMITS.count}
            onClick={() => setFontPanelOpen(open => !open)} className="h-11 px-2.5 sm:h-8"><Type aria-hidden="true" className="h-4 w-4" /></Button>}
          {toolbarAction}
          {voice.state === "recording" ? (
            <span className="flex min-w-0 items-center gap-1.5 text-[11px] text-danger">
              <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-danger animate-pulse" />
              Запись… нажмите стоп
            </span>
          ) : voice.state === "transcribing" ? (
            <span className="flex min-w-0 items-center gap-1.5 text-[11px] text-fg-tertiary">
              <Loader2 className="h-3 w-3 shrink-0 animate-spin" />
              Распознаю речь…
            </span>
          ) : voice.error ? (
            <span className="min-w-0 truncate text-[11px] text-danger" title={voice.error}>
              {voice.error}
            </span>
          ) : !toolbarAction ? (
            <span
              className="text-[11px] font-mono text-fg-tertiary min-w-0 truncate"
              title="Ctrl + Enter — отправить"
            >
              <kbd className="px-1 rounded bg-surface-raised border border-border-subtle">
                Ctrl
              </kbd>
              <span className="mx-0.5">+</span>
              <kbd className="px-1 rounded bg-surface-raised border border-border-subtle">
                ↵
              </kbd>
            </span>
          ) : null}
          </div>

          <div className="flex items-center gap-1.5 shrink-0">
            {voice.supported && (
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={voice.toggle}
                disabled={isSubmitting || voice.state === "transcribing"}
                className={cn("h-11 px-2.5 sm:h-8", voice.state === "recording" && "text-danger")}
                title={
                  voice.state === "recording"
                    ? "Остановить запись"
                    : "Надиктовать промпт голосом"
                }
                aria-label="Голосовой ввод"
              >
                {voice.state === "transcribing" ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : voice.state === "recording" ? (
                  <Square className="h-3.5 w-3.5 fill-current animate-pulse" />
                ) : (
                  <Mic className="h-3.5 w-3.5" />
                )}
              </Button>
            )}

            {isStreaming && (
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={onCancel}
                className="h-11 px-2.5 sm:h-8"
                title="Прервать текущую генерацию"
                aria-label="Прервать генерацию"
              >
                <StopCircle className="h-3.5 w-3.5" />
              </Button>
            )}

            <Button
              type="button"
              size="sm"
              onClick={() => {
                void send();
              }}
              disabled={
                sendDisabled || isSubmitting || attachmentsBlocked || (!value.trim() && selections.length === 0 && !hasAttachments)
              }
              className="h-11 gap-1.5 rounded-full px-3.5 sm:h-8"
              title={
                isStreaming
                  ? "Будет отправлено после текущей генерации"
                  : "Отправить"
              }
            >
              {isSubmitting ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Send className="h-3.5 w-3.5" />
              )}
              <span>
                {isSubmitting
                  ? "Отправляю…"
                  : isStreaming
                    ? "В очередь"
                    : "Отправить"}
              </span>
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
