"use client";

import { useRef, useState } from "react";
import { ArrowLeft, ArrowRight, Loader2, Pause } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { Message } from "@/lib/api/types";

export function GenerationFailureCard({ failure, onRetry, onReturnToPrompt, continueFromDraft = false }: {
  failure: NonNullable<Message["generation_failure"]>;
  /** Explicit new user request through normal admission, never an automatic replay. */
  onRetry?: () => Promise<boolean>;
  continueFromDraft?: boolean;
  onReturnToPrompt?: () => void;
}) {
  const submitting = useRef(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState(false);
  // Provider retryable:false stops automatic retries. The owner may explicitly
  // submit a new request; the usual server quota/access/recovery checks still own it.
  const manualProviderRequest = !failure.retryable
    && (failure.code === "provider_unavailable" || failure.code === "provider_access");
  const remaining = failure.remaining_work ?? [];
  const title = continueFromDraft ? "Продолжим сборку"
    : failure.code === "product_work_remaining" ? "Осталось доделать" : "Что остановило работу";
  const canRetry = Boolean(onRetry && (continueFromDraft || failure.retryable || manualProviderRequest));
  const retry = async () => {
    if (!canRetry || !onRetry || submitting.current) return;
    submitting.current = true;
    setPending(true);
    setError(false);
    let accepted = false;
    try {
      accepted = await onRetry();
      if (!accepted) setError(true);
    }
    catch { setError(true); }
    finally {
      if (!accepted) { submitting.current = false; setPending(false); }
    }
  };
  return <div role="status" className="generation-failure-card rounded-xl border border-border-default bg-surface-raised p-4 space-y-3">
    <p className="flex items-center gap-2 font-medium text-fg-primary"><Pause aria-hidden="true" className="size-5 shrink-0 text-fg-secondary" />{title}</p>
    <p className="text-sm text-fg-secondary">{failure.message}</p>
    {remaining.length > 0 && <ul aria-label="Что осталось сделать" className="list-disc space-y-1 pl-5 text-sm text-fg-primary">
      {remaining.map(item => <li key={item}>{item}</li>)}
    </ul>}
    {continueFromDraft && <p className="text-sm text-fg-secondary">Продолжим с сохранённого черновика.</p>}
    {!continueFromDraft && failure.draft_saved && <p className="text-sm text-fg-secondary">Черновик этого запуска сохранён.</p>}
    {error && <p className="text-sm text-fg-secondary">Запрос пока не принят. Попробуйте продолжить чуть позже.</p>}
    {canRetry ? <div className="space-y-2">
      {manualProviderRequest && <p className="text-sm text-fg-secondary">{continueFromDraft
        ? "Продолжение использует лимит генераций или правок."
        : "Повтор отправляется как новый запрос и использует лимит генераций или правок."}</p>}
      <Button type="button" disabled={pending} onClick={() => { void retry(); }} className="generation-failure-action gap-2">
        {pending ? <Loader2 aria-hidden="true" className="size-4 animate-spin" /> : <ArrowRight aria-hidden="true" className="size-4" />}
        {pending ? (continueFromDraft ? "Продолжаю…" : "Отправляю…") : (continueFromDraft ? "Продолжить сборку" : "Повторить запрос")}
      </Button>
      {failure.code === "request_too_large" && onReturnToPrompt &&
        <Button type="button" variant="outline" disabled={pending} onClick={onReturnToPrompt}>
          Уточнить задачу в чате
        </Button>}
    </div> : onReturnToPrompt && <Button type="button" variant="outline" onClick={onReturnToPrompt} className="generation-failure-action gap-2">
      <ArrowLeft aria-hidden="true" className="size-4" />Вернуться к запросу
    </Button>}
  </div>;
}
