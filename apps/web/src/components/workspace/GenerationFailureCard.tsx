"use client";

import { useRef, useState } from "react";
import { ArrowLeft, ArrowRight, Loader2, Pause } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { Message } from "@/lib/api/types";

export function GenerationFailureCard({ failure, onRetry, onReturnToPrompt }: {
  failure: NonNullable<Message["generation_failure"]>;
  /** Explicit new user request through normal admission, never an automatic replay. */
  onRetry?: () => Promise<boolean>;
  onReturnToPrompt?: () => void;
}) {
  const submitting = useRef(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState(false);
  // Provider retryable:false stops automatic retries. The owner may explicitly
  // submit a new request; the usual server quota/access/recovery checks still own it.
  const manualProviderRequest = !failure.retryable
    && (failure.code === "provider_unavailable" || failure.code === "provider_access");
  const canRetry = Boolean(onRetry && (failure.retryable || manualProviderRequest));
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
    <p className="flex items-center gap-2 font-medium text-fg-primary"><Pause aria-hidden="true" className="size-5 shrink-0 text-fg-secondary" />Продолжим сборку</p>
    <p className="text-sm text-fg-secondary">{canRetry
      ? "Работу над приложением можно продолжить, когда будете готовы."
      : "Можно вернуться к запросу и уточнить следующий шаг."}</p>
    {error && <p className="text-sm text-fg-secondary">Запрос пока не принят. Попробуйте продолжить чуть позже.</p>}
    {canRetry ? <div className="space-y-2">
      {manualProviderRequest && <p className="text-sm text-fg-secondary">Продолжение отправляется как новый запрос и использует лимит генераций или правок.</p>}
      <Button type="button" disabled={pending} onClick={() => { void retry(); }} className="generation-failure-action gap-2">
        {pending ? <Loader2 aria-hidden="true" className="size-4 animate-spin" /> : <ArrowRight aria-hidden="true" className="size-4" />}
        {pending ? "Продолжаю…" : "Продолжить сборку"}
      </Button>
    </div> : onReturnToPrompt && <Button type="button" variant="outline" onClick={onReturnToPrompt} className="generation-failure-action gap-2">
      <ArrowLeft aria-hidden="true" className="size-4" />Вернуться к запросу
    </Button>}
    <details className="text-sm text-fg-secondary">
      <summary className="cursor-pointer">Подробнее</summary>
      <p className="mt-2">{failure.message}</p>
    </details>
  </div>;
}
