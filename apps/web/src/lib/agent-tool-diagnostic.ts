import type { AgentStep, AgentToolDiagnostic } from "@/lib/api/types";

const STATE_LABELS: Record<AgentToolDiagnostic["state"], string> = {
  started: "Начало вызова записано",
  succeeded: "Результат получен",
  failed: "Вызов завершился с ошибкой",
  skipped: "Вызов пропущен",
  cancelled: "Ожидание вызова отменено",
  unknown: "Результат вызова не подтверждён",
};

const REASON_LABELS: Record<string, string> = {
  executable_not_found: "Оболочка сообщила, что команда не найдена",
  nonzero_exit: "Команда вернула ненулевой код",
  timeout: "Истёк лимит времени",
  reader_unavailable: "Служба чтения файлов была недоступна",
  workspace_sync_failed: "Не удалось синхронизировать рабочие файлы",
  context_mismatch: "Состояние исходников не совпало с ожидаемым",
  missing_file: "Нужный файл не найден",
  discovery_pause: "Агент повторял чтение без изменений, поэтому дальнейший поиск приостановлен",
  done_blocked: "Для завершения нужны дополнительные проверки",
  replayed_result: "Использован сохранённый результат прошлого вызова",
  cached_result: "Использован результат из кэша",
  executor_error: "Ошибка среды выполнения",
  outcome_unknown: "Причина не подтверждена",
  cancelled: "Получен сигнал отмены ожидания",
  source_change_required: "Требуется изменить исходные файлы",
  source_repair_required: "Требуется исправить исходные файлы",
  repeat_guard: "Повторный вызов пропущен",
  build_required: "Требуется проверка сборки",
  recovery_contract: "Нужно выполнить условия восстановления",
  verification_deferred: "Проверка отложена",
  tool_failed: "Инструмент сообщил об ошибке",
  infrastructure_unavailable: "Среда выполнения была недоступна",
  dispatch_rejected: "Проверка перед запуском отклонила команду",
  empty_response: "Модель вернула пустой ответ",
  missing_action: "В ответе модели не указано действие",
  invalid_tool: "Указанный инструмент недоступен",
  invalid_tool_arguments: "Параметры вызова некорректны",
};

// Match the backend's allowlist. Arguments, paths and output are never labels.
const COMMAND_FAMILIES = new Set([
  "bash", "sh", "dash", "zsh", "busybox", "rg", "grep", "find", "ls", "head", "tail", "cat",
  "sed", "awk", "jq", "node", "npx", "npm", "pnpm", "yarn", "bun", "bunx", "deno", "tsc", "tsx",
  "python", "python3", "pip", "pip3", "uv", "pytest", "make", "git", "curl", "wget", "sqlite3",
  "psql", "docker", "cp", "mkdir", "mv", "rm", "touch", "sort", "wc", "timeout", "env",
]);

const TIMING_LABELS = {
  dispatch: "Запрос к контроллеру и ожидание ответа",
  workspace_sync: "Синхронизация файлов",
  reader_wait: "Ожидание готовности reader",
  total: "Общее время",
} as const;
const TIMING_KEYS = Object.keys(TIMING_LABELS) as (keyof typeof TIMING_LABELS)[];
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const CALL_ID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[45][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const UTC_TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)$/;
const EVENT_TIME = new Intl.DateTimeFormat("ru-RU", { dateStyle: "short", timeStyle: "medium" });

export function isTranscriptUuid(value: unknown): value is string {
  return typeof value === "string" && UUID_PATTERN.test(value);
}

export function isAgentCommand(tool?: string): boolean {
  return tool === "bash" || tool === "shell" || tool === "command";
}

function isToolCallId(value: unknown): value is string {
  return typeof value === "string" && CALL_ID_PATTERN.test(value);
}

function commandName(value: unknown): string | undefined {
  return typeof value === "string" && COMMAND_FAMILIES.has(value) ? value : undefined;
}

export function agentToolCallKey(step: AgentStep): string | undefined {
  const callId = step.tool_diagnostic?.call_id;
  return isToolCallId(callId) ? `agent-tool:${step.runId ?? "legacy"}:${callId}` : undefined;
}

/** Standalone call observations carry the same persisted shape as live heartbeats. */
export function isAgentToolObservation(step: AgentStep): boolean {
  return step.kind === "heartbeat" && isToolCallId(step.tool_diagnostic?.call_id);
}

function safeInteger(value: unknown, minimum = 0, maximum = Number.MAX_SAFE_INTEGER): number | undefined {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= minimum && value <= maximum
    ? value : undefined;
}

export function readAgentToolDiagnostic(value: unknown): AgentToolDiagnostic | undefined {
  if (!value || typeof value !== "object") return undefined;
  const raw = value as Partial<AgentToolDiagnostic>;
  if (!isToolCallId(raw.call_id)) return undefined;
  const timings: NonNullable<AgentToolDiagnostic["timings_ms"]> = {};
  for (const key of TIMING_KEYS) {
    const duration = safeInteger(raw.timings_ms?.[key]);
    if (duration !== undefined) timings[key] = duration;
  }
  return {
    call_id: raw.call_id,
    state: typeof raw.state === "string" && Object.hasOwn(STATE_LABELS, raw.state) ? raw.state : "unknown",
    reason_code: typeof raw.reason_code === "string" && Object.hasOwn(REASON_LABELS, raw.reason_code)
      ? raw.reason_code : undefined,
    command_family: commandName(raw.command_family),
    missing_executable: raw.reason_code === "executable_not_found" ? commandName(raw.missing_executable) : undefined,
    elapsed_ms: safeInteger(raw.elapsed_ms),
    exit_code: safeInteger(raw.exit_code, -(2 ** 31), 2 ** 31 - 1),
    timings_ms: timings,
    reconciled: raw.reconciled === true ? true : undefined,
  };
}

export function agentToolStateLabel(state: AgentToolDiagnostic["state"] | undefined): string {
  return state && Object.hasOwn(STATE_LABELS, state) ? STATE_LABELS[state] : STATE_LABELS.unknown;
}

export function agentToolReasonLabel(reason?: string): string {
  return reason && Object.hasOwn(REASON_LABELS, reason) ? REASON_LABELS[reason] : REASON_LABELS.outcome_unknown;
}

export function agentToolDetails(diagnostic: AgentToolDiagnostic, recordedAt?: string): [string, string][] {
  const { state, reason_code, command_family, exit_code, elapsed_ms, timings_ms, reconciled } = diagnostic;
  const rows: [string, string][] = [["Состояние", agentToolStateLabel(state)]];
  const eventTime = typeof recordedAt === "string" && UTC_TIMESTAMP.test(recordedAt) ? Date.parse(recordedAt) : NaN;
  if (Number.isFinite(eventTime)) rows.push(["Время события", EVENT_TIME.format(eventTime)]);
  if (reconciled && state !== "started") {
    rows.push(["Восстановление", state === "unknown" ? "Сверка после восстановления" : "Результат восстановлен из журнала"]);
  }
  if (reason_code || (state !== "started" && state !== "succeeded")) {
    const missing = reason_code === "executable_not_found" && commandName(diagnostic.missing_executable);
    rows.push(["Причина", missing ? `Оболочка не нашла команду ${missing}` : agentToolReasonLabel(reason_code)]);
  }
  if (command_family) rows.push(["Семейство команды", command_family]);
  if (exit_code !== undefined) rows.push(["Код возврата", String(exit_code)]);
  if (elapsed_ms !== undefined) rows.push(["Длительность", `${elapsed_ms} мс`]);
  for (const key of TIMING_KEYS) {
    const duration = timings_ms?.[key];
    if (duration === undefined || (key === "total" && duration === elapsed_ms)) continue;
    rows.push([TIMING_LABELS[key], `${duration} мс`]);
  }
  return rows;
}
