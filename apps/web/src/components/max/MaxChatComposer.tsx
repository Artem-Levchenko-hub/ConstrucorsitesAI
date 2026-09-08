"use client";

import { useEffect, useId, useRef, useState, type ComponentProps } from "react";
import { useQuery } from "@tanstack/react-query";
import { Lightbulb, RefreshCw, X } from "lucide-react";
import { PromptInput, type PromptInputHandle } from "@/components/workspace/PromptInput";
import { Button } from "@/components/ui/button";
import { requestProductAdvice, type ProductAdviceItem } from "@/lib/api/product-advice";
import { MaxProductAdvisor } from "./MaxProductAdvisor";
import { cn } from "@/lib/utils";
import "./max-chat.css";

export function MaxChatComposer({ projectId, snapshotId, contextVersion, ...props }: ComponentProps<typeof PromptInput> & {
  projectId: string;
  snapshotId: string | null;
  contextVersion?: string | number;
}) {
  const [open, setOpen] = useState(false);
  const draftRef = useRef<PromptInputHandle>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const panelId = useId();
  const available = !!snapshotId && !props.isStreaming;
  const advice = useQuery({
    queryKey: ["product-advice", projectId, snapshotId, contextVersion],
    queryFn: async () => {
      const result = await requestProductAdvice(projectId);
      if (result.project_id !== projectId || result.current_snapshot_id !== snapshotId || result.source === "fallback") {
        throw new Error("Advice does not describe the current application");
      }
      return result;
    },
    enabled: open && available,
    staleTime: 0,
    retry: false,
    refetchOnWindowFocus: false,
  });

  useEffect(() => {
    if (!open) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.stopPropagation();
      setOpen(false);
      triggerRef.current?.focus();
    };
    const closeOutside = (event: PointerEvent) => {
      if (event.target instanceof Node && !panelRef.current?.contains(event.target)) setOpen(false);
    };
    document.addEventListener("keydown", closeOnEscape);
    document.addEventListener("pointerdown", closeOutside);
    return () => {
      document.removeEventListener("keydown", closeOnEscape);
      document.removeEventListener("pointerdown", closeOutside);
    };
  }, [open]);

  const insert = (item: ProductAdviceItem) => {
    if (!available || advice.isFetching || advice.isError) return;
    if (draftRef.current?.insertDraft(item.prompt)) setOpen(false);
  };

  return <PromptInput {...props} className={cn(props.className, "max-chat-composer")} draftRef={draftRef} toolbarAction={
    <div className="max-chat-advice-anchor" ref={panelRef}>
      <Button ref={triggerRef} type="button" variant="secondary" size="sm"
        className="max-chat-advice-trigger" aria-expanded={open} aria-controls={open ? panelId : undefined}
        onClick={() => setOpen((value) => !value)}>
        <Lightbulb aria-hidden="true" /> Подсказки
      </Button>
      {open && <section id={panelId} role="region" aria-labelledby={`${panelId}-title`} className="max-chat-advice-panel">
        <header className="max-chat-advice-heading">
          <div><span className="max-chat-eyebrow">ИИ · ПО ВАШЕМУ ПРИЛОЖЕНИЮ</span>
            <h2 id={`${panelId}-title`}>Что улучшить дальше</h2></div>
          <Button type="button" variant="secondary" size="icon" aria-label="Закрыть подсказки"
            onClick={() => { setOpen(false); triggerRef.current?.focus(); }}><X aria-hidden="true" /></Button>
        </header>
        <div className="max-chat-advice-body">
          {!available ? <p className="max-chat-advice-notice">{props.isStreaming
            ? "Дождитесь завершения изменений — подсказки будут по обновлённому приложению."
            : "Создайте первую версию приложения — ИИ предложит, что улучшить именно в ней."}</p>
            : advice.isFetching || advice.isPending ? <div role="status" className="max-chat-advice-loading">
              <div className="max-chat-thinking" aria-hidden="true"><span /><span /><span /></div>
              <strong>Анализируем приложение</strong><p>Учитываем его возможности и последние изменения.</p>
              <div className="max-chat-advice-skeleton" aria-hidden="true"><span /><span /><span /></div>
            </div> : advice.isError ? <div role="alert" className="max-chat-advice-notice">
              <strong>Не удалось подготовить подсказки</strong><p>Можно повторить попытку или написать свой запрос в чат.</p>
              <Button type="button" variant="secondary" onClick={() => void advice.refetch()}><RefreshCw aria-hidden="true" /> Повторить</Button>
            </div> : advice.data?.items.length ? <MaxProductAdvisor items={advice.data.items} applyingId={null} onApply={insert} />
              : <p className="max-chat-advice-notice">Пока нет достаточно точных рекомендаций. Расскажите в чате, что хотите улучшить.</p>}
        </div>
        {available && !advice.isError && !advice.isPending && !advice.isFetching && !!advice.data?.items.length &&
          <footer>Выберите идею, отредактируйте запрос и отправьте его сами.</footer>}
      </section>}
    </div>
  } />;
}
