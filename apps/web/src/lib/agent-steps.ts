import { CAPACITY_WAITING_COPY } from "@/lib/agent-transcript";
import type { AgentStep } from "@/lib/api/types";

/**
 * Ожидание мощности приходит повторяющимся событием (раз в 15 секунд и на
 * каждое изменение позиции в очереди). Владелец 26.09 показал скриншот, где
 * пятиминутное ожидание выглядело как восемнадцать одинаковых строк «Ожидаю
 * ресурсы сервера» подряд — это читается как поломка, а не как ожидание.
 *
 * Схлопываем ТОЛЬКО подряд идущие строки ожидания и оставляем последнюю: в ней
 * самая свежая позиция в очереди. Обычные шаги сборки не трогаем — там
 * повторение бывает осмысленным (два одинаковых действия над разными файлами
 * различаются путём, но встречаются и настоящие повторы).
 *
 * Выброшенные строки не исчезают бесследно: их число остаётся в `repeatedCount`
 * у выжившей. Без этого счётчик повторов в ленте всегда показывал бы «один
 * раз», хотя ожидание длилось восемнадцать событий.
 */
function collapseRepeatedWaiting(steps: AgentStep[]): AgentStep[] {
  const rows: AgentStep[] = [];
  let waitingRun = 0;
  for (const step of steps) {
    if (step.action !== CAPACITY_WAITING_COPY.title) {
      waitingRun = 0;
      rows.push(step);
      continue;
    }
    waitingRun += 1;
    const previous = rows.at(-1);
    if (waitingRun > 1 && previous?.action === CAPACITY_WAITING_COPY.title) rows.pop();
    rows.push(waitingRun > 1 ? { ...step, repeatedCount: waitingRun } : step);
  }
  return rows;
}

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
  const ordered = reconciled.sort((left, right) => {
    const leftSeq = left.seq ?? Number.MAX_SAFE_INTEGER;
    const rightSeq = right.seq ?? Number.MAX_SAFE_INTEGER;
    return leftSeq - rightSeq;
  });
  return collapseRepeatedWaiting(ordered);
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

/** Подряд идущие одинаковые шаги, слитые в одну строку со счётчиком повторов. */
export type CollapsedAgentStep = {
  step: AgentStep;
  /** Сколько одинаковых шагов подряд слилось в эту строку (1 — обычный шаг). */
  repeats: number;
  /** Устойчивый ключ строки для React: пережимает добавление новых повторов. */
  key: string;
  /** Индекс последнего исходного шага — по нему открывается его содержимое. */
  index: number;
};

function visibleIdentity(step: AgentStep): string {
  return JSON.stringify([step.kind, step.action, step.path ?? "", step.tool ?? ""]);
}

/**
 * Одинаковое действие подряд — это ОДНА строка, а не десять.
 *
 * Владелец 26.09 показал ленту, где ожидание мощности стояло десятью
 * одинаковыми строками: такое читается как поломка, а не как «идёт работа».
 * Правило общее для всех действий, а не только для ожидания: агент может
 * подряд перечитывать один и тот же файл или повторять проверку, и каждый
 * такой повтор — не новость, а продолжение того же самого.
 *
 * Схлопываем только ПОДРЯД идущие повторы: то же действие после другой работы
 * — это уже новый этап, и его видно отдельной строкой. Внутри строки остаётся
 * последний шаг: в нём самое свежее содержимое (например, актуальная позиция в
 * очереди), а счётчик показывает, сколько раз действие повторилось.
 */
export function collapseAgentSteps(steps: AgentStep[] = []): CollapsedAgentStep[] {
  const rows: CollapsedAgentStep[] = [];
  steps.forEach((step, index) => {
    const previous = rows.at(-1);
    if (previous && visibleIdentity(previous.step) === visibleIdentity(step)) {
      // Часть повторов могла схлопнуться ещё при сборке ленты — считаем и их.
      previous.repeats += step.repeatedCount ?? 1;
      previous.step = step;
      previous.index = index;
      return;
    }
    rows.push({
      step,
      repeats: step.repeatedCount ?? 1,
      key: step.eventId ?? `${step.runId ?? "local"}:${step.seq ?? index}`,
      index,
    });
  });
  return rows;
}
