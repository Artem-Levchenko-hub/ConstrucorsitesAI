"use client";

import { useId } from "react";
import { isGenerationEngine, type GenerationEngine } from "@/lib/generation-engine";

export function GenerationEngineSelect({ value, onChange, disabled }: {
  value: GenerationEngine;
  onChange: (value: GenerationEngine) => void;
  disabled: boolean;
}) {
  const id = useId();
  return <div className="flex flex-wrap items-center gap-2 px-3 pb-1 text-xs text-fg-secondary">
    <label htmlFor={id}>Следующий запрос · QA</label>
    <select id={id} value={value} disabled={disabled} onChange={event => {
      if (isGenerationEngine(event.target.value)) onChange(event.target.value);
    }} className="rounded-lg border border-border-subtle bg-surface-panel px-2 py-1.5 text-fg-primary disabled:opacity-60">
      <option value="claude">Claude Sonnet 5.5</option>
      <option value="sol">GPT-6.2 Sol</option>
    </select>
  </div>;
}
