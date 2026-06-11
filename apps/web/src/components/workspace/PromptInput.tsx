"use client";

import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Clock, Loader2, Mic, Send, Square, StopCircle, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useVoiceInput } from "@/hooks/useVoiceInput";
import { EASE_OUT } from "@/lib/motion";
import { cn } from "@/lib/utils";
import type { SelectedElement } from "@/lib/api/types";
import { useInspectorStore } from "@/store/inspector";
import { SelectedChips } from "./SelectedChips";

export function PromptInput({
  onSubmit,
  onCancel,
  onCancelPending,
  isStreaming,
  pendingPrompt,
  className,
  textareaRef,
}: {
  onSubmit: (text: string, selections: SelectedElement[]) => void;
  onCancel: () => void;
  onCancelPending: () => void;
  isStreaming: boolean;
  pendingPrompt: string | null;
  className?: string;
  // Optional lifted ref so the parent can focus the input — used by the
  // discovery "Другое" chip to hand the user the free-text field. Falls back to
  // an internal ref when omitted, so existing call sites are unchanged.
  textareaRef?: React.RefObject<HTMLTextAreaElement | null>;
}) {
  const [value, setValue] = useState("");
  const internalRef = useRef<HTMLTextAreaElement>(null);
  const ref = textareaRef ?? internalRef;

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
  }, [value]);

  // Cmd/Ctrl+Enter from anywhere submits — даже во время стрима (отправка
  // в этом случае ставится в очередь хуком).
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (
        (e.metaKey || e.ctrlKey) &&
        e.key === "Enter" &&
        (value.trim() || useInspectorStore.getState().selections.length)
      ) {
        send();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);

  const send = () => {
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
        : "");
    if (!finalText) return;
    onSubmit(finalText, wire);
    setValue("");
    clearSelections();
  };

  const onKeyDown: React.KeyboardEventHandler<HTMLTextAreaElement> = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  return (
    <div
      className={cn(
        "bg-surface-panel-dark p-3 space-y-2",
        className,
      )}
    >
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

      <div className="rounded-2xl border border-border-default bg-surface-input focus-within:border-[rgba(124,92,255,0.5)] focus-within:shadow-[0_0_0_4px_rgba(124,92,255,0.10)] transition-all">
        <textarea
          ref={ref}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={
            isStreaming
              ? "Можно писать следующий — отправится после текущей генерации"
              : selections.length
                ? "Общий комментарий (необязательно) — правки по элементам уже заданы…"
                : "Опишите, что изменить или добавить…"
          }
          rows={2}
          className="w-full bg-transparent px-3.5 py-3 text-sm text-fg-primary placeholder:text-fg-tertiary resize-none focus:outline-none"
        />

        <div className="flex items-center justify-between px-2.5 pb-2.5 gap-2">
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
          ) : (
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
          )}

          <div className="flex items-center gap-1.5 shrink-0">
            {voice.supported && (
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={voice.toggle}
                disabled={voice.state === "transcribing"}
                className={cn("px-2.5", voice.state === "recording" && "text-danger")}
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
                className="px-2.5"
                title="Прервать текущую генерацию"
                aria-label="Прервать генерацию"
              >
                <StopCircle className="h-3.5 w-3.5" />
              </Button>
            )}

            <Button
              type="button"
              size="sm"
              onClick={send}
              disabled={!value.trim() && selections.length === 0}
              className="gap-1.5 rounded-full px-3.5"
              title={
                isStreaming
                  ? "Будет отправлено после текущей генерации"
                  : "Отправить"
              }
            >
              <Send className="h-3.5 w-3.5" />
              <span>{isStreaming ? "В очередь" : "Отправить"}</span>
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
