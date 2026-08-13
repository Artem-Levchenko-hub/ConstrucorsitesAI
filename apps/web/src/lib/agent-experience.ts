import type { AgentStep } from "@/lib/api/types";

export const CREATIVE_PHASES = [
  { id: "intent", label: "Замысел", description: "Сценарий и характер" },
  { id: "craft", label: "Интерфейс", description: "Экраны и детали" },
  { id: "proof", label: "Проверка", description: "Сборка и сценарии" },
  { id: "polish", label: "Полировка", description: "Взгляд пользователя" },
] as const;

export type CreativePhaseStatus = "complete" | "active" | "upcoming" | "issue";

export type CreativePhaseState = (typeof CREATIVE_PHASES)[number] & {
  status: CreativePhaseStatus;
};

const PHASE_BY_TOOL: Record<string, number> = {
  plan_task: 0,
  read_skill: 0,
  discover_capabilities: 0,
  call_capability: 0,
  list_dir: 0,
  read_file: 0,
  grep: 0,
  write_file: 1,
  edit_file: 1,
  generate_media: 1,
  bash: 1,
  build: 2,
  read_logs: 2,
  runtime_check: 2,
  probe: 2,
  verify_isolation: 2,
  see: 3,
  done: 3,
};

function toolOf(step: AgentStep | undefined): string {
  return (step?.tool ?? step?.action ?? "").trim().toLowerCase();
}

export function creativePhaseIndex(steps: AgentStep[]): number {
  return steps.reduce(
    (furthest, step) => Math.max(furthest, PHASE_BY_TOOL[toolOf(step)] ?? 0),
    0,
  );
}

export function creativePhaseStates(
  steps: AgentStep[],
  streaming: boolean,
): CreativePhaseState[] {
  const current = creativePhaseIndex(steps);
  const lastFailed = steps.at(-1)?.ok === false;
  const finished = steps.some((step) => toolOf(step) === "done" && step.ok !== false);

  return CREATIVE_PHASES.map((phase, index) => {
    let status: CreativePhaseStatus = "upcoming";
    if (finished || index < current || (!streaming && index <= current && !lastFailed)) {
      status = "complete";
    } else if (index === current) {
      status = lastFailed ? "issue" : "active";
    }
    return { ...phase, status };
  });
}

export function creativeNarration(steps: AgentStep[], streaming: boolean): string {
  const last = steps.at(-1);
  if (!last) return streaming ? "Настраиваю творческую мастерскую" : "История сборки";
  if (last.tool === "provider_resume") {
    return "Восстанавливаю ответ AI-провайдера";
  }
  if (last.ok === false) return "Исправляю найденную проблему";
  if (last.kind === "retry" || last.kind === "stalled") {
    return "Меняю подход и продолжаю";
  }

  const tool = toolOf(last);
  if (tool === "plan_task" || tool === "read_skill") {
    return "Выбираю характер и логику продукта";
  }
  if (tool === "discover_capabilities" || tool === "call_capability") {
    return "Сверяюсь с актуальными возможностями";
  }
  if (["list_dir", "read_file", "grep"].includes(tool)) {
    return "Изучаю основу, чтобы сохранить рабочее ядро";
  }
  if (tool === "generate_media") return "Создаю фирменный визуальный акцент";
  if (tool === "write_file" || tool === "edit_file") {
    const path = last.path.toLowerCase();
    if (path.endsWith(".css")) return "Настраиваю типографику, ритм и детали";
    return "Собираю интерфейс вокруг главного действия";
  }
  if (tool === "build") return "Проверяю целостность приложения";
  if (["runtime_check", "probe", "verify_isolation"].includes(tool)) {
    return "Запускаю реальные пользовательские сценарии";
  }
  if (tool === "see") return "Смотрю глазами пользователя и полирую";
  if (tool === "done") return "Приложение собрано и проверено";
  return streaming ? "Продолжаю собирать продукт" : "История сборки";
}
