"use client";

import { useRef, useState } from "react";
import type { Message } from "@/lib/api/types";

export function GenerationFailureCard({ failure, onRetry }: {
  failure: NonNullable<Message["generation_failure"]>;
  onRetry?: () => Promise<boolean>;
}) {
  const submitting = useRef(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState(false);
  const retry = async () => {
    if (!onRetry || submitting.current) return;
    submitting.current = true;
    setPending(true);
    setError(false);
    let accepted = false;
    try { accepted = await onRetry(); }
    catch { setError(true); }
    finally {
      if (!accepted) { submitting.current = false; setPending(false); }
    }
  };
  return <div role="alert" className="rounded-xl border border-border-default bg-surface-overlay p-4 space-y-2">
    <p className="font-medium">Генерация не завершена</p>
    <p>{failure.message}</p>
    {error && <p>Не удалось запустить генерацию. Попробуйте ещё раз.</p>}
    {failure.retryable && onRetry && <button type="button" disabled={pending}
      onClick={() => { void retry(); }} className="rounded-lg px-3 py-2 bg-accent-subtle text-accent disabled:opacity-50">
      {pending ? "Запускаю…" : "Повторить генерацию"}
    </button>}
  </div>;
}
