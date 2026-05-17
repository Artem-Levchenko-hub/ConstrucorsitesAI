"use client";

import { useEffect, useRef, useState } from "react";
import { Send, StopCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export function PromptInput({
  onSubmit,
  onCancel,
  isStreaming,
  className,
}: {
  onSubmit: (text: string) => void;
  onCancel: () => void;
  isStreaming: boolean;
  className?: string;
}) {
  const [value, setValue] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);

  // Auto-resize textarea up to a sensible max.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "0px";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [value]);

  // Cmd/Ctrl+Enter from anywhere submits.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter" && value.trim()) {
        send();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);

  const send = () => {
    const text = value.trim();
    if (!text || isStreaming) return;
    onSubmit(text);
    setValue("");
  };

  const onKeyDown: React.KeyboardEventHandler<HTMLTextAreaElement> = (e) => {
    if (e.key === "Enter" && !e.shiftKey && !isStreaming) {
      e.preventDefault();
      send();
    }
  };

  return (
    <div
      className={cn(
        "border-t border-border-default bg-surface-base p-3",
        className,
      )}
    >
      <div className="rounded-md border border-border-default bg-surface-input focus-within:border-accent transition-colors">
        <textarea
          ref={ref}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={
            isStreaming
              ? "Можно набирать — отправится, как закончится текущая генерация"
              : "Опишите, что изменить или добавить…"
          }
          rows={2}
          className="w-full bg-transparent px-3 py-2.5 text-sm text-fg-primary placeholder:text-fg-tertiary resize-none focus:outline-none"
        />

        <div className="flex items-center justify-between px-2 pb-2">
          <span className="text-[11px] font-mono text-fg-tertiary">
            <kbd className="px-1 rounded bg-surface-raised border border-border-subtle">
              Ctrl
            </kbd>{" "}
            +{" "}
            <kbd className="px-1 rounded bg-surface-raised border border-border-subtle">
              Enter
            </kbd>{" "}
            — отправить
          </span>

          {isStreaming ? (
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={onCancel}
              className="gap-1.5"
            >
              <StopCircle className="h-3.5 w-3.5" />
              Стоп
            </Button>
          ) : (
            <Button
              type="button"
              size="sm"
              onClick={send}
              disabled={!value.trim()}
              className="gap-1.5"
            >
              <Send className="h-3.5 w-3.5" />
              Отправить
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
