import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { AgentTranscript } from "@/components/workspace/AgentTranscript";
import { CAPACITY_WAITING_COPY } from "@/lib/agent-transcript";
import { humanPath, stepCopy } from "@/lib/agent-step-copy";
import type { AgentStep } from "@/lib/api/types";

const waiting = (seq: number): AgentStep => ({
  eventId: `w${seq}`,
  runId: "33333333-3333-3333-3333-333333333333",
  seq,
  step: null,
  kind: "step",
  action: CAPACITY_WAITING_COPY.title,
  path: "",
});

function render(steps: AgentStep[]) {
  return renderToStaticMarkup(
    <QueryClientProvider client={new QueryClient()}>
      <AgentTranscript
        projectId="00000000-0000-0000-0000-000000000001"
        messageId="00000000-0000-0000-0000-000000000002"
        streaming
        initialSteps={steps}
      />
    </QueryClientProvider>,
  );
}

describe("лента работы агента", () => {
  it("десять одинаковых ожиданий — одна пульсирующая строка со счётчиком", () => {
    // Владелец 26.09 прислал скриншот: десять одинаковых строк подряд читаются
    // как поломка. Повтор должен выглядеть как продолжающееся действие.
    const html = render(Array.from({ length: 10 }, (_, index) => waiting(index + 1)));

    // Строка ровно одна: заголовок встречается в ней и в подсказке, поэтому
    // считаем сами строки списка, а не вхождения текста.
    expect(html.split("<li").length - 1).toBe(1);
    expect(html).toContain(CAPACITY_WAITING_COPY.title);
    expect(html).toContain("×10");
    expect(html).toContain("agent-step-pulse");
    expect(html).toContain("1 шаг.");
  });

  it("объясняет текущий шаг словами, понятными без знания кода", () => {
    const html = render([
      {
        eventId: "b1",
        runId: "33333333-3333-3333-3333-333333333333",
        seq: 1,
        step: 1,
        kind: "step",
        action: "Проверяю сборку",
        path: "",
        tool: "build",
      },
    ]);

    expect(html).toContain("ошибка находится до того");
  });

  it("вместо пути к файлу показывает, что это за место в приложении", () => {
    const html = render([
      {
        eventId: "w1",
        runId: "33333333-3333-3333-3333-333333333333",
        seq: 1,
        step: 1,
        kind: "step",
        action: "Пишу главную страницу",
        path: "src/app/page.tsx",
        tool: "write_file",
      },
    ]);

    expect(html).toContain("главный экран");
    // Полный путь остаётся в подсказке строки — он нужен при разборе проблемы.
    expect(html).toContain("src/app/page.tsx");
  });
});

describe("словарь действий", () => {
  it("переводит пути в человеческие имена мест", () => {
    expect(humanPath("src/app/page.tsx")).toBe("главный экран");
    expect(humanPath("src/app/orders/page.tsx")).toBe("экран «orders»");
    expect(humanPath("src/lib/db/schema.ts")).toBe("структура данных");
    expect(humanPath("src/app/api/orders/route.ts")).toBe("серверный обработчик");
    expect(humanPath("")).toBe("");
  });

  it("у каждого разряда шага есть объяснение, а у неудачи — свой тон", () => {
    const failed = stepCopy({
      step: 1,
      kind: "step",
      action: "Проверяю сборку",
      path: "",
      tool: "build",
      ok: false,
    });
    expect(failed.tone).toBe("fail");

    const escalate = stepCopy({
      step: 2,
      kind: "escalate",
      action: "усиливаю модель → opus",
      path: "",
    });
    expect(escalate.hint).toContain("мощную модель");
  });
});
