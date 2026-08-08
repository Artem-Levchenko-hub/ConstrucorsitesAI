const PRIVATE_MODEL_LABELS = [
  /\banthropic\/claude-sonnet-5\b/gi,
  /\bclaude-sonnet-5\b/gi,
  /\b(?:Claude\s+)?Sonnet\s*5\b/giu,
] as const;

/** Keep provider routing private in current and persisted user-facing text. */
export function hidePrivateModelNames(value: string): string {
  return PRIVATE_MODEL_LABELS.reduce(
    (text, pattern) => text.replace(pattern, "AI"),
    value,
  );
}
