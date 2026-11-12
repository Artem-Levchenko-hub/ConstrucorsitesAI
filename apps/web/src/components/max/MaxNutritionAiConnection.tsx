"use client";

import { useId, useRef, useState } from "react";
import type { FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { setNutritionAiKey } from "@/lib/api/app-integrations";
import { describeApiError } from "@/lib/api/errors";

export function MaxNutritionAiConnection({ projectId, enabled, onChange }: {
  projectId: string;
  enabled: boolean;
  onChange: (enabled: boolean) => void;
}) {
  const keyId = useId();
  const submitting = useRef(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function update(key: string | null) {
    if (submitting.current) return;
    submitting.current = true;
    setBusy(true);
    setError("");
    try {
      onChange((await setNutritionAiKey(projectId, key)).enabled);
    } catch (err) {
      setError(describeApiError(err, "Не удалось изменить подключение AITunnel."));
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting.current) return;
    const key = new FormData(event.currentTarget).get("api_key");
    if (typeof key !== "string" || !key.trim()) return;
    event.currentTarget.reset();
    void update(key.trim());
  }

  return (
    <details className="max-integration-explainer">
      <summary>AITunnel для оценки КБЖУ{enabled ? " · подключён" : ""}</summary>
      <p>Ключ хранится в настройках этого приложения. Лимит составляет 100 оценок в день на приложение.</p>
      <form onSubmit={submit} className="mt-3 flex flex-wrap items-end gap-3">
        <div className="min-w-0 flex-1 basis-52 space-y-1">
          <Label htmlFor={keyId}>Ключ AITunnel</Label>
          <Input id={keyId} name="api_key" type="password" autoComplete="off"
            placeholder="sk-aitunnel-…" maxLength={220} required disabled={busy} />
        </div>
        <Button type="submit" disabled={busy}>
          {enabled ? "Заменить ключ AITunnel" : "Подключить AITunnel"}
        </Button>
        {enabled && (
          <Button type="button" variant="outline" disabled={busy} onClick={() => void update(null)}>
            Отключить AITunnel
          </Button>
        )}
      </form>
      {error && <p role="alert" className="text-danger-fg">{error}</p>}
    </details>
  );
}
