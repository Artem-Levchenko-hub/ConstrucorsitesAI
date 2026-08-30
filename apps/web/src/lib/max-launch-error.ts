const MAX_LAUNCH_ERROR_CHARS = 360;

export function getMaxLaunchErrorDescription(error: unknown): string {
  const raw = error instanceof Error ? error.message : "Повторите запуск";
  if (/prod build timed out/i.test(raw)) {
    return (
      "Production-сборка превысила лимит времени. Повторите публикацию; " +
      "если это повторится, откройте детали проекта."
    );
  }
  if (/prod build failed|docker build/i.test(raw)) {
    return (
      "Production-сборка не завершилась. Omnia уже повторила её автоматически. " +
      "Повторите публикацию; если сбой повторится, откройте детали проекта."
    );
  }

  const compact = raw.replace(/\s+/g, " ").trim();
  return compact.length > MAX_LAUNCH_ERROR_CHARS
    ? `${compact.slice(0, MAX_LAUNCH_ERROR_CHARS - 1)}…`
    : compact;
}
