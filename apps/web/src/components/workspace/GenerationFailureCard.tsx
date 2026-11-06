"use client";

import { useRef, useState } from "react";
import { AlertCircle, ArrowLeft, Loader2, RotateCcw } from "lucide-react";
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
  return <div role="alert" className="generation-failure-card rounded-xl border border-danger/25 bg-danger/5 p-4 space-y-3">
    <p className="flex items-center gap-2 font-semibold text-danger-fg"><AlertCircle aria-hidden="true" className="size-5 shrink-0" />Генерация не завершена</p>
    <p>{failure.message}</p>
    {error && <p className="text-sm text-danger-fg">Не удалось запустить генерацию. Проверьте уведомление и попробуйте ещё раз.</p>}
    {canRetry ? <div className="space-y-2">
      {manualProviderRequest && <p className="text-sm">Повтор будет новым запросом и может использовать лимит генераций или правок.</p>}
      <Button type="button" disabled={pending} onClick={() => { void retry(); }} className="generation-failure-action gap-2">
        {pending ? <Loader2 aria-hidden="true" className="size-4 animate-spin" /> : <RotateCcw aria-hidden="true" className="size-4" />}
        {pending ? "Запускаю…" : manualProviderRequest ? "Повторить запрос" : "Повторить генерацию"}
      </Button>
    </div> : onReturnToPrompt && <Button type="button" variant="outline" onClick={onReturnToPrompt} className="generation-failure-action gap-2">
      <ArrowLeft aria-hidden="true" className="size-4" />Вернуться к запросу
    </Button>}
  </div>;
}
