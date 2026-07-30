import type { AgentStep } from "@/lib/api/types";

export function restorePersistedAgentSteps(
  current: AgentStep[] | undefined,
  persisted: AgentStep[] | null | undefined,
): AgentStep[] | undefined {
  if (current?.length || !persisted?.length) return current;
  return persisted;
}
