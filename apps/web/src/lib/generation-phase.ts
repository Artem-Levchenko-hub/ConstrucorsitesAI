import type { AgentStep, GenerationRunStatus } from "@/lib/api/types";
import { CAPACITY_WAITING_COPY, isPromptPreparationPending } from "@/lib/agent-transcript";
import { parseGenerationRecovery } from "@/lib/generation-recovery";

export type GenerationPhaseId = "workspace" | "prompt" | "creating" | "checking" | "preview"
  | "ready" | "queued" | "waiting" | "recovering" | "paused" | "stopping" | "stopped"
  | "unconfirmed" | "history" | "idle";

export type GenerationPhase = {
  id: GenerationPhaseId;
  title: string;
  explanation: string;
  /** Specific context that stays visible when routine active-phase copy is condensed. */
  detail?: string;
  active: boolean;
  milestones: string[];
};

const COPY: Record<GenerationPhaseId, [string, string]> = {
  workspace: ["Готовлю рабочее место", "Подготавливаю среду для вашего приложения."],
  prompt: ["Подготавливаю промпт", "Уточняю задачу и визуальное направление перед созданием приложения."],
  creating: ["Создаю приложение", "Собираю экраны и связываю нужные действия по вашему запросу."],
  checking: ["Проверяю приложение", "Проверяю сборку и работу изменений. Если нужно, исправляю замечания."],
  preview: ["Открываю превью", "Сохраняю результат и проверяю готовность новой версии."],
  ready: ["Изменения готовы", "Результат этого запроса подтверждён. Готовую версию можно посмотреть в превью."],
  queued: [CAPACITY_WAITING_COPY.title, CAPACITY_WAITING_COPY.detail],
  waiting: ["Ожидаю продолжения", "Работа сохранена. Ожидаю готовности нужного сервиса."],
  recovering: ["Дорабатываю приложение", "Устраняю замечания и продолжаю ту же задачу."],
  paused: ["Подготовка на паузе", "Работа остановилась. Подробности и доступные действия сохранены ниже."],
  stopping: ["Останавливаю работу", "Ожидаю подтверждения остановки текущего запроса."],
  stopped: ["Работа остановлена", "Запрос отменён. Сохранённая история доступна в подробностях."],
  unconfirmed: ["Проверка результата не подтверждена", "Уточняю сохранённый результат. Готовность новой версии пока не подтверждена."],
  history: ["Запрос завершён", "Это история предыдущего запроса, а не статус текущего превью."],
  idle: ["Ход работы", "Подробности сохранённой работы доступны ниже."],
};

/** WS phase aliases plus named tools/exact persisted titles; raw event UUIDs are not phases. */
function observedPhase(step: AgentStep): GenerationPhaseId | null {
  if (step.skipped) return null;
  const phase = step.eventId?.startsWith("phase:") ? step.eventId.split(":").at(-1) : null;
  if (step.tool === "platform_core" || phase === "prepare"
    || /^Подготавливаю среду(?: · завершено)?$/.test(step.action)) return "workspace";
  if (step.tool === "prompt_preparation") return isPromptPreparationPending(step) ? "prompt" : "creating";
  // A done attempt can be rejected by completion checks; only success starts preview handoff.
  if ((step.tool ?? step.action) === "done") return step.ok === true ? "preview" : "checking";
  if (["snapshot", "promote", "release", "complete"].includes(phase ?? "")
    || /^(Сохраняю версию|Подключаю новую версию|Проверяю готовность приложения|Завершаю проверку результата)(?: · завершено)?$/.test(step.action)) return "preview";
  if (["fast_check", "final_build", "runtime_probe", "finalization"].includes(phase ?? "")
    || /^(Проверяю исходники|Собираю приложение|Проверяю работу приложения|Финальные проверки)(?: · завершено)?$/.test(step.action)
    || ["build", "runtime_check", "probe", "verify_isolation", "read_logs"].includes(step.tool ?? step.action)) return "checking";
  if (["step", "tool", "retry", "stalled", "escalate", "provider_error"].includes(step.kind)
    || phase === "edit" || phase === "generation") return "creating";
  return null;
}

function isBuildObservation(step: AgentStep): boolean {
  return !step.skipped && ((step.tool ?? step.action) === "build"
    || (step.eventId?.startsWith("phase:") === true && step.eventId.endsWith(":final_build"))
    || (!step.tool && step.kind === "heartbeat" && /^Собираю приложение(?: · завершено)?$/.test(step.action)));
}

function isSourceChangeObservation(step: AgentStep): boolean {
  if (step.skipped) return false;
  const tool = step.tool ?? step.action;
  // Failed or still-running shell commands may already have changed source.
  return ["write_file", "edit_file", "bash", "shell", "command"].includes(tool);
}

export function projectGenerationPhase({ steps, runId, status, streaming, recovery: rawRecovery,
  recoveryUnconfirmed = false, currentRequestComplete = false, historical = false }: {
  steps: AgentStep[];
  runId?: string;
  status?: GenerationRunStatus | null;
  streaming: boolean;
  recovery?: unknown;
  recoveryUnconfirmed?: boolean;
  currentRequestComplete?: boolean;
  historical?: boolean;
}): GenerationPhase {
  const own = steps.filter(step => !runId || !step.runId || step.runId === runId);
  const recovery = parseGenerationRecovery(rawRecovery);
  // These are observations, not an inferred completed prefix of a six-stage strip.
  const milestones: string[] = [];
  if (own.some(step => step.ok === true && !step.skipped && step.tool !== "platform_core"
    && observedPhase(step) === "workspace")) {
    milestones.push("Среда подготовлена");
  }
  const prompt = own.filter(step => step.tool === "prompt_preparation" && !step.skipped).at(-1);
  if (prompt?.ok === true) {
    milestones.push("Промпт подготовлен");
  }
  const buildIndex = own.findLastIndex(isBuildObservation);
  const changedIndex = own.findLastIndex(isSourceChangeObservation);
  const checked = buildIndex >= 0
    ? own[buildIndex].ok === true && buildIndex > changedIndex
    : changedIndex < 0 && recovery?.last_verified_action === "build_verified";
  if (checked) {
    milestones.push("Сборка проверена");
  }
  const active = streaming || status === "pending" || status === "running";
  const view = (id: GenerationPhaseId, working = false, detail?: string): GenerationPhase => ({
    id, title: COPY[id][0], explanation: detail ?? COPY[id][1], detail, active: working, milestones,
  });
  if (status === "completed") {
    if (historical) return view("history");
    return view(currentRequestComplete ? "ready" : "unconfirmed");
  }
  if (status === "cancel_requested") return view("stopping");
  if (status === "cancelled") return view("stopped");
  if (status === "failed") return view("paused");
  if (historical) return view("idle", false, "Это сохранённая работа предыдущего запроса. Его текущий статус здесь не подтверждён.");
  if (status === "queued_for_capacity") return view("queued");
  if (recoveryUnconfirmed || (rawRecovery != null && recovery === null)) return view("unconfirmed");
  if (recovery?.phase === "needs_input") return view("paused", false,
    "Нужно уточнение, чтобы продолжить. Можно остановить работу и отправить уточнённый запрос.");
  if (recovery?.phase === "waiting_dependency") return view("waiting");
  if (recovery?.phase === "reconciling") return view("unconfirmed");
  if (!active) return view(own.at(-1)?.ok === false ? "paused" : "idle");
  if (recovery?.phase === "repairing") return view("recovering", true);
  if (recovery?.phase === "verifying") return view("checking", true);
  if (own.at(-1)?.tool === "capacity_wait" || own.at(-1)?.action === CAPACITY_WAITING_COPY.title) return view("queued");
  const last = [...own].reverse().find(step => observedPhase(step) !== null);
  const phase = last ? observedPhase(last)! : "workspace";
  const fallback = last?.tool === "prompt_preparation" && last.ok === false;
  return view(phase, true, fallback ? "Создаю приложение по вашему исходному запросу." : undefined);
}
