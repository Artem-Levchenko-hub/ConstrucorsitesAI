"use client";

import { useEffect, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { MessagesSquare, Sparkles } from "lucide-react";
import { listMessages } from "@/lib/api/messages";
import { ChatMessage } from "./ChatMessage";
import { PromptInput } from "./PromptInput";
import { usePromptStream } from "@/hooks/usePromptStream";
import { useWorkspaceStore } from "@/store/workspace";
import { Skeleton } from "@/components/ui/skeleton";

/**
 * Example prompts shown in the empty state. Decorative — clicks could be wired
 * to populate the PromptInput, but to keep this purely a UI refresh (UX
 * unchanged) they are non-interactive hints. Users still type into the input.
 */
const EXAMPLE_PROMPTS = [
  "Лендинг пиццерии с меню и формой заказа",
  "Портфолио фотографа с галереей",
  "Сайт мероприятия с регистрацией",
];

export function ChatPanel({
  projectId,
  projectSlug,
}: {
  projectId: string;
  projectSlug: string;
}) {
  const modelId = useWorkspaceStore((s) => s.selectedModelId);
  const { submit, cancel, cancelPending, pendingPrompt } = usePromptStream(
    projectId,
    projectSlug,
  );
  const scrollRef = useRef<HTMLDivElement>(null);

  const { data: messages, isPending } = useQuery({
    queryKey: ["messages", projectId],
    queryFn: () => listMessages(projectId),
  });

  // Determine streaming state from data: an assistant message with
  // tokens_out === null is mid-stream.
  const last = messages?.[messages.length - 1];
  const isStreaming =
    last?.role === "assistant" && last.tokens_out === null;
  const streamingId = isStreaming ? last?.id : null;

  // Auto-scroll on new messages / chunks.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [messages?.length, last?.content]);

  return (
    // h-full + min-h-0 нужны чтобы в grid-cell flex-колонка получила фиксированную
    // высоту и `flex-1 + overflow-y-auto` ниже реально срабатывал, а не растягивал
    // родителя (раньше из-за двойного скролла внутри ScrollArea инпут уезжал вниз).
    <div className="flex flex-col h-full min-h-0 bg-surface-panel-dark">
      <div className="shrink-0 px-4 h-10 flex items-center gap-2 border-b border-border-subtle">
        <MessagesSquare className="h-3.5 w-3.5 text-accent/80" aria-hidden="true" />
        <span className="text-xs font-mono text-fg-tertiary uppercase tracking-wider">
          Чат
        </span>
        {/* Hair-thin gradient line to the right of the label — visual cadence
            with the TopBar accent line. */}
        <div
          aria-hidden="true"
          className="flex-1 h-px bg-gradient-to-r from-border-subtle to-transparent ml-1"
        />
      </div>

      <div
        ref={scrollRef}
        className="flex-1 min-h-0 overflow-y-auto overscroll-contain"
      >
        {isPending && (
          <div className="p-4 space-y-3">
            <Skeleton className="h-16" />
            <Skeleton className="h-24" />
          </div>
        )}

        {!isPending && messages && messages.length === 0 && (
          <div className="relative flex flex-col items-center text-center px-6 pt-12 pb-8 gap-5">
            {/* Ambient orb — radial gradient circle that breathes behind the
                headline. Pure decoration, pointer-events: none. */}
            <div
              aria-hidden="true"
              className="absolute top-2 left-1/2 -translate-x-1/2 h-32 w-32 rounded-full pointer-events-none animate-breathe-glow"
              style={{
                background:
                  "radial-gradient(circle, rgb(124 92 255 / 0.35) 0%, rgb(124 92 255 / 0.08) 45%, transparent 70%)",
              }}
            />

            <div className="relative flex h-11 w-11 items-center justify-center rounded-full border border-accent/30 bg-surface-raised/60 backdrop-blur-sm shadow-glow-accent">
              <Sparkles
                className="h-5 w-5 text-accent"
                strokeWidth={1.75}
                aria-hidden="true"
              />
            </div>

            <div className="space-y-1.5">
              <h2 className="text-base font-semibold tracking-tight text-gradient-accent">
                Поговорим о вашем сайте
              </h2>
              <p className="text-xs text-fg-tertiary leading-5 max-w-[260px] mx-auto">
                Опишите, что хотите создать. AI соберёт страницу с дизайном,
                контентом и интерактивом в один промпт.
              </p>
            </div>

            <div className="w-full space-y-1.5 mt-1">
              <div className="text-[10px] font-mono uppercase tracking-wider text-fg-muted">
                Например
              </div>
              <ul className="space-y-1.5 text-left">
                {EXAMPLE_PROMPTS.map((p) => (
                  <li
                    key={p}
                    className="text-xs text-fg-secondary leading-5 px-2.5 py-1.5 rounded-md border border-border-subtle bg-surface-raised/40 hover:bg-surface-raised hover:border-border-default transition-colors"
                  >
                    «{p}»
                  </li>
                ))}
              </ul>
            </div>
          </div>
        )}

        {messages?.map((m) => (
          <ChatMessage
            key={m.id}
            message={m}
            streaming={m.id === streamingId}
          />
        ))}
      </div>

      <div className="shrink-0">
        <PromptInput
          onSubmit={(text, selections) => submit(text, modelId, selections)}
          onCancel={cancel}
          onCancelPending={cancelPending}
          isStreaming={isStreaming}
          pendingPrompt={pendingPrompt}
        />
      </div>
    </div>
  );
}
