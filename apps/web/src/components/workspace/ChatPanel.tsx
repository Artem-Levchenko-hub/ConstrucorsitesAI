"use client";

import { useEffect, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { listMessages } from "@/lib/api/messages";
import { ChatMessage } from "./ChatMessage";
import { PromptInput } from "./PromptInput";
import { usePromptStream } from "@/hooks/usePromptStream";
import { useWorkspaceStore } from "@/store/workspace";
import { Skeleton } from "@/components/ui/skeleton";

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
      <div className="shrink-0 px-4 h-10 flex items-center border-b border-border-subtle">
        <span className="text-xs font-mono text-fg-tertiary uppercase tracking-wider">
          Чат
        </span>
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
          <div className="p-6 text-center space-y-2">
            <div className="text-sm text-fg-secondary">
              Поговорим о вашем сайте.
            </div>
            <div className="text-xs text-fg-tertiary leading-5">
              Опишите, что хотите создать. Например:
              <br />
              «Сделай лендинг для пиццерии с меню и формой заказа».
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
