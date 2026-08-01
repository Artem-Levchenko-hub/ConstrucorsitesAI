/** Return a stable elapsed duration from durable generation timestamps. */
export function agentElapsedSeconds(
  startedAt: string | null | undefined,
  finishedAt: string | null | undefined,
  nowMs: number = Date.now(),
): number {
  if (!startedAt) return 0;
  const startMs = Date.parse(startedAt);
  if (!Number.isFinite(startMs)) return 0;

  const parsedFinish = finishedAt ? Date.parse(finishedAt) : Number.NaN;
  const endMs = Number.isFinite(parsedFinish) ? parsedFinish : nowMs;
  return Math.max(0, Math.floor((endMs - startMs) / 1000));
}
