const ACTIVE_GENERATION_STATUSES = new Set([
  "pending",
  "running",
  "cancel_requested",
]);

export function isGenerationActive(status: string | null | undefined): boolean {
  return status != null && ACTIVE_GENERATION_STATUSES.has(status);
}

export function shouldDeferMaxRuntimeStart({
  generationQueryPending,
  generationStatus,
  hasGeneration,
  hasStarterHandoff,
  starterHandoffExpired,
}: {
  generationQueryPending: boolean;
  generationStatus: string | null | undefined;
  hasGeneration: boolean;
  hasStarterHandoff: boolean;
  starterHandoffExpired: boolean;
}): boolean {
  return (
    generationQueryPending ||
    isGenerationActive(generationStatus) ||
    (hasStarterHandoff && !starterHandoffExpired && !hasGeneration)
  );
}
