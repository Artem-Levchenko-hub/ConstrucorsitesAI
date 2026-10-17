import type { AgentStep, GenerationEventEnvelope } from "@/lib/api/types";

const PHASE_TITLE: Record<string, string> = {
  prepare: "Подготавливаю среду",
  edit: "Подготавливаю изменения",
  generation: "Подготавливаю изменения",
  fast_check: "Проверяю исходники",
  final_build: "Собираю приложение",
  runtime_probe: "Проверяю работу приложения",
  snapshot: "Сохраняю версию",
  promote: "Подключаю новую версию",
  release: "Проверяю готовность приложения",
  command: "Выполняю команду",
  tool: "Выполняю проверку",
  finalization: "Финальные проверки",
  complete: "Завершаю проверку результата",
};

/** Adapt durable observations; never infer acceptance or elapsed from a heartbeat. */
export function generationEventStep(envelope: GenerationEventEnvelope): AgentStep | null {
  const { data } = envelope;
  const base = { runId: envelope.run_id, seq: envelope.seq, path: "", step: null };
  if (envelope.type === "agent.step") {
    const kind = data.kind === "escalate" || data.kind === "stalled" || data.kind === "retry"
      ? data.kind : "step";
    return {
      ...base, eventId: envelope.event_id,
      step: typeof data.step === "number" ? data.step : null, kind,
      action: typeof data.action === "string" ? data.action : "",
      path: typeof data.path === "string" ? data.path : "",
      tool: typeof data.tool === "string" ? data.tool : undefined,
      detail: typeof data.detail === "string" ? data.detail : undefined,
      ok: typeof data.ok === "boolean" ? data.ok : null,
      skipped: data.skipped === true,
    };
  }
  const lifecycle = envelope.type === "tool.started" ? "started"
    : envelope.type === "tool.heartbeat" ? "heartbeat"
    : envelope.type === "tool.finished" ? "finished" : undefined;
  if (!lifecycle && envelope.type !== "generation.phase") return null;
  const phase = typeof data.phase === "string" && data.phase.trim() ? data.phase : "tool";
  const title = PHASE_TITLE[phase] ?? "Проверяю сохранённую работу";
  const operationId = typeof data.operation_id === "string" && data.operation_id
    ? data.operation_id
    : typeof data.call_id === "string" && data.call_id ? data.call_id : undefined;
  const ok = typeof data.ok === "boolean" ? data.ok
    : data.state === "completed" ? true
    : data.state === "failed" || data.state === "cancelled" || data.state === "timed_out" ? false
    : null;
  return {
    ...base,
    eventId: lifecycle
      ? operationId ? `operation:${envelope.run_id}:${operationId}` : envelope.event_id
      : `phase:${envelope.run_id}:${phase}`,
    kind: "heartbeat",
    action: lifecycle === "finished" && ok === true ? `${title} · завершено` : title,
    operationId: lifecycle ? operationId : undefined,
    toolLifecycle: lifecycle,
    detail: typeof data.detail === "string" ? data.detail
      : typeof data.log_bytes === "number" && Number.isFinite(data.log_bytes) && data.log_bytes >= 0
        ? `Обработано ${data.log_bytes} байт журнала` : undefined,
    ok,
  };
}
