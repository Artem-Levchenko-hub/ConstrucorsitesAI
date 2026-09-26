import { describe, expect, it } from "vitest";

import { CAPACITY_WAITING_COPY } from "@/lib/agent-transcript";
import type { AgentStep } from "@/lib/api/types";
import {
  collapseAgentSteps,
  mergeAgentStepsBySequence,
  restorePersistedAgentSteps,
} from "@/lib/agent-steps";

const persisted: AgentStep[] = [
  {
    step: 1,
    kind: "step",
    action: "Проверяю проект",
    path: "",
    tool: "runtime_check",
  },
];

describe("agent step history", () => {
  it("restores persisted steps when the observer created an empty cache first", () => {
    expect(restorePersistedAgentSteps([], persisted)).toEqual(persisted);
  });

  it("does not overwrite an active live transcript", () => {
    const live = [{ ...persisted[0], action: "Пишу страницу" }];
    expect(restorePersistedAgentSteps(live, persisted)).toBe(live);
  });

  it("keeps the cache unchanged when no persisted history exists", () => {
    expect(restorePersistedAgentSteps([], null)).toEqual([]);
    expect(restorePersistedAgentSteps(undefined, undefined)).toBeUndefined();
  });

  it("merges durable history over a shorter local prefix", () => {
    const rows = (start: number, end: number): AgentStep[] =>
      Array.from({ length: end - start + 1 }, (_, offset) => {
        const seq = start + offset;
        return {
          eventId: `event-${seq}`,
          runId: "00000000-0000-0000-0000-000000000001",
          seq,
          step: seq,
          kind: "step",
          action: "build",
          path: "",
        };
      });

    expect(
      mergeAgentStepsBySequence(rows(1, 51), rows(1, 130)).map(
        (step) => step.seq,
      ),
    ).toEqual(Array.from({ length: 130 }, (_, index) => index + 1));
  });
});

describe("повторяющееся ожидание мощности", () => {
  const waiting = (seq: number, detail: string): AgentStep => ({
    eventId: `w${seq}`,
    runId: "11111111-1111-1111-1111-111111111111",
    seq,
    step: null,
    kind: "step",
    action: CAPACITY_WAITING_COPY.title,
    path: "",
    detail,
  });

  it("оставляет одну строку вместо восемнадцати одинаковых", () => {
    // Владелец 26.09 показал скриншот пятиминутного ожидания: восемнадцать
    // одинаковых строк подряд читались как поломка, а не как ожидание.
    const repeated = Array.from({ length: 18 }, (_, index) =>
      waiting(index + 1, CAPACITY_WAITING_COPY.detail),
    );

    const merged = mergeAgentStepsBySequence([], repeated);

    expect(merged).toHaveLength(1);
    expect(merged[0].seq).toBe(18);
  });

  it("оставляет самую свежую строку: в ней актуальная позиция в очереди", () => {
    const merged = mergeAgentStepsBySequence(
      [],
      [waiting(1, "Вы 5-й в очереди"), waiting(2, "Вы 2-й в очереди")],
    );

    expect(merged.map((step) => step.detail)).toEqual(["Вы 2-й в очереди"]);
  });

  it("не склеивает ожидание с шагами сборки вокруг него", () => {
    const build = (seq: number, action: string): AgentStep => ({
      eventId: `b${seq}`,
      runId: "11111111-1111-1111-1111-111111111111",
      seq,
      step: seq,
      kind: "step",
      action,
      path: "src/app/page.tsx",
    });

    const merged = mergeAgentStepsBySequence(
      [],
      [
        waiting(1, CAPACITY_WAITING_COPY.detail),
        waiting(2, CAPACITY_WAITING_COPY.detail),
        build(3, "Пишу страницу"),
        waiting(4, CAPACITY_WAITING_COPY.detail),
      ],
    );

    expect(merged.map((step) => step.seq)).toEqual([2, 3, 4]);
  });

  it("настоящие повторы обычных шагов не трогает", () => {
    const step = (seq: number): AgentStep => ({
      eventId: `s${seq}`,
      runId: "11111111-1111-1111-1111-111111111111",
      seq,
      step: seq,
      kind: "step",
      action: "Пишу файл",
      path: "src/app/page.tsx",
    });

    const merged = mergeAgentStepsBySequence([], [step(1), step(2), step(3)]);

    expect(merged).toHaveLength(3);
  });
});

describe("лента без дублей: повтор действия — одна строка", () => {
  const row = (seq: number, action: string, path = "", detail = ""): AgentStep => ({
    eventId: `c${seq}`,
    runId: "22222222-2222-2222-2222-222222222222",
    seq,
    step: seq,
    kind: "step",
    action,
    path,
    detail,
  });

  it("считает повторы вместо того, чтобы множить строки", () => {
    const collapsed = collapseAgentSteps([
      row(1, CAPACITY_WAITING_COPY.title, "", "Вы 5-й в очереди"),
      row(2, CAPACITY_WAITING_COPY.title, "", "Вы 3-й в очереди"),
      row(3, CAPACITY_WAITING_COPY.title, "", "Вы 1-й в очереди"),
    ]);

    expect(collapsed).toHaveLength(1);
    expect(collapsed[0].repeats).toBe(3);
    // В строке остаётся самое свежее содержимое: позиция в очереди меняется.
    expect(collapsed[0].step.detail).toBe("Вы 1-й в очереди");
  });

  it("схлопывает любое повторяющееся действие, не только ожидание", () => {
    const collapsed = collapseAgentSteps([
      row(1, "Читаю", "src/app/page.tsx"),
      row(2, "Читаю", "src/app/page.tsx"),
      row(3, "Проверяю сборку"),
    ]);

    expect(collapsed.map(item => [item.step.action, item.repeats])).toEqual([
      ["Читаю", 2],
      ["Проверяю сборку", 1],
    ]);
  });

  it("одинаковое действие над разными файлами остаётся разными строками", () => {
    const collapsed = collapseAgentSteps([
      row(1, "Пишу", "src/app/page.tsx"),
      row(2, "Пишу", "src/app/orders/page.tsx"),
    ]);

    expect(collapsed).toHaveLength(2);
  });

  it("возврат к прежнему действию после другой работы — это новая строка", () => {
    const collapsed = collapseAgentSteps([
      row(1, "Проверяю сборку"),
      row(2, "Правлю", "src/app/page.tsx"),
      row(3, "Проверяю сборку"),
    ]);

    expect(collapsed.map(item => item.step.seq)).toEqual([1, 2, 3]);
  });

  it("ключ строки не меняется, пока действие повторяется", () => {
    const first = collapseAgentSteps([row(1, "Читаю", "src/app/page.tsx")]);
    const second = collapseAgentSteps([
      row(1, "Читаю", "src/app/page.tsx"),
      row(2, "Читаю", "src/app/page.tsx"),
    ]);

    expect(second[0].key).toBe(first[0].key);
  });
});
