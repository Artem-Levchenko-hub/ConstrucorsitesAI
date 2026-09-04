import type { AgentStep } from "@/lib/api/types";

function identity(step: AgentStep, index: number): string {
  if (step.eventId) return step.eventId;
  if (step.runId && typeof step.seq === "number") {
    return `${step.runId}:${step.seq}`;
  }
  return `legacy:${index}:${step.kind}:${step.action}:${step.path}:${step.detail ?? ""}`;
}

function semanticIdentity(step: AgentStep): string {
  return JSON.stringify([
    step.step,
    step.kind,
    step.action,
    step.path,
    step.tool,
    step.detail,
    step.ok,
    step.operationId,
  ]);
}

export function mergeAgentStepsBySequence(
  current: AgentStep[] = [],
  incoming: AgentStep[] = [],
): AgentStep[] {
  const byId = new Map<string, AgentStep>();
  [...current, ...incoming].forEach((step, index) => {
    byId.set(identity(step, index), step);
  });
  const rows = [...byId.values()];
  const durableCounts = new Map<string, number>();
  for (const step of rows) {
    if (step.eventId || (step.runId && typeof step.seq === "number")) {
      const key = semanticIdentity(step);
      durableCounts.set(key, (durableCounts.get(key) ?? 0) + 1);
    }
  }
  const reconciled = rows.filter((step) => {
    if (step.eventId || (step.runId && typeof step.seq === "number")) {
      return true;
    }
    const key = semanticIdentity(step);
    const remaining = durableCounts.get(key) ?? 0;
    if (remaining === 0) return true;
    durableCounts.set(key, remaining - 1);
    return false;
  });
  return reconciled.sort((left, right) => {
    const leftSeq = left.seq ?? Number.MAX_SAFE_INTEGER;
    const rightSeq = right.seq ?? Number.MAX_SAFE_INTEGER;
    return leftSeq - rightSeq;
  });
}

export function restorePersistedAgentSteps(
  current: AgentStep[] | undefined,
  persisted: AgentStep[] | null | undefined,
): AgentStep[] | undefined {
  if (!persisted?.length) return current;
  if (!current?.length) return persisted;
  const sequenced = [...current, ...persisted].some(
    (step) => step.eventId && typeof step.seq === "number",
  );
  return sequenced ? mergeAgentStepsBySequence(current, persisted) : current;
}
