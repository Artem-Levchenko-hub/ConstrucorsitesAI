import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { OnboardingSurvey } from "@/components/workspace/OnboardingSurvey";
import { usePromptStream } from "@/hooks/usePromptStream";
import { getLatestGeneration, sendPrompt } from "@/lib/api/messages";
import type { Message, SurveyQuestion, WsEvent } from "@/lib/api/types";

vi.mock("@/lib/api/messages", () => ({
  getLatestGeneration: vi.fn(), sendPrompt: vi.fn(), cancelGeneration: vi.fn(),
}));
vi.mock("@/lib/api/mocks", () => ({ USE_MOCKS: false }));

// This is the JSON-object schema emitted by the API publisher, not Python model reprs.
const questions: SurveyQuestion[] = [
  {
    message: "Что за продукт?", kind: "text", choices: ["Кофейня", "Магазин"],
    allow_custom: true, multi_select: false, options: [],
  },
  {
    message: "Что показать?", kind: "text", choices: ["Меню", "Адрес"],
    allow_custom: true, multi_select: true, options: [],
  },
  {
    message: "Выберите палитру", kind: "palette", choices: [],
    allow_custom: false, multi_select: false,
    options: [
      { id: "warm-coffee", name: "Тёплый кофе", one_liner: "Мягкий фон и тёплый акцент",
        bg: "#faf5ef", accent: "#b45309" },
      { id: "cool-slate", name: "Холодный сланец", one_liner: "Спокойные синие тона",
        bg: "#f8fafc", accent: "#2563eb" },
    ],
  },
];

class Socket {
  static OPEN = 1;
  static CONNECTING = 0;
  static instances: Socket[] = [];
  readyState = Socket.OPEN;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onopen: (() => void) | null = null;
  constructor() { Socket.instances.push(this); }
  send() {}
  close() { this.readyState = 3; }
  emit(event: WsEvent) { this.onmessage?.({ data: JSON.stringify(event) }); }
}

let client: QueryClient;
let root: Root;
let container: HTMLDivElement;
const done = vi.fn();
const skip = vi.fn();

function Consumer() {
  usePromptStream("p", "coffee");
  // Same cache subscription as ChatPanel; generation submission is deliberately absent.
  const { data } = useQuery<SurveyQuestion[] | null>({
    queryKey: ["onboarding-survey", "p"],
    queryFn: () => client.getQueryData<SurveyQuestion[]>(["onboarding-survey", "p"]) ?? null,
    staleTime: Infinity,
  });
  return data?.length ? <OnboardingSurvey questions={data} onDone={done} onSkip={skip} /> : null;
}

function button(text: string) {
  const found = [...container.querySelectorAll<HTMLButtonElement>("button")]
    .find((element) => element.textContent?.includes(text));
  expect(found, `button ${text}`).toBeDefined();
  return found!;
}

async function flush() {
  await act(async () => { await vi.advanceTimersByTimeAsync(25); });
}

beforeEach(async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.useFakeTimers();
  vi.stubGlobal("WebSocket", Socket);
  Socket.instances = [];
  vi.mocked(getLatestGeneration).mockResolvedValue({
    id: "run", project_id: "p", assistant_message_id: "a", status: "running",
    response_mode: "clarify", created_at: "2026-09-08T00:00:00Z",
    started_at: null, finished_at: null,
  });
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  client.setQueryData<Message[]>(["messages", "p"], [{
    id: "a", project_id: "p", role: "assistant", content: "Подбираю вопросы",
    model_id: "model", snapshot_id: null, tokens_in: null, tokens_out: null,
    generation_status: "running", created_at: "2026-09-08T00:00:00Z",
  }]);
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(
    <QueryClientProvider client={client}><Consumer /></QueryClientProvider>,
  ));
  await flush();
  expect(Socket.instances).toHaveLength(1);
  expect(container.querySelector("[role='dialog']")).toBeNull();
  act(() => Socket.instances[0].emit({
    type: "onboarding.survey",
    data: { message_id: "a", survey: questions, question_index: 1, question_total: 2, niche: "Кофейня" },
  }));
  await flush();
});

afterEach(async () => {
  await act(async () => root.unmount());
  client.clear();
  container.remove();
  vi.clearAllTimers();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

it("renders wire questions and submits selected answers with the palette identifier", async () => {
  expect(client.getQueryData(["onboarding-survey", "p"])).toEqual(questions);
  expect(client.getQueryData(["onboarding-survey", "other"])).toBeUndefined();
  expect(container.querySelector("[role='dialog']")?.getAttribute("aria-label"))
    .toBe("Уточняющие вопросы");
  for (const question of questions) expect(container.textContent).toContain(question.message);
  expect(container.textContent).toContain("Мягкий фон и тёплый акцент");
  const swatch = button("Тёплый кофе").querySelector<HTMLElement>("[aria-hidden='true']");
  expect(swatch?.style.background).toContain("linear-gradient");
  act(() => button("Магазин").click());
  act(() => button("Кофейня").click());
  expect(button("Магазин").getAttribute("aria-pressed")).toBe("false");
  for (const choice of ["Меню", "Адрес", "Тёплый кофе"]) act(() => button(choice).click());
  expect(button("Тёплый кофе").getAttribute("aria-pressed")).toBe("true");
  // The final stream event must not erase the answers or close the survey.
  act(() => Socket.instances[0].emit({
    type: "llm.done", data: { message_id: "a", tokens_in: 0, tokens_out: 0, cost_rub: 0 },
  }));
  await flush();
  act(() => button("Готово, собираем").click());
  expect(done).toHaveBeenCalledExactlyOnceWith(
    "Что за продукт? — Кофейня\nЧто показать? — Меню, Адрес\nПалитра — Тёплый кофе",
    "warm-coffee",
  );
  expect(skip).not.toHaveBeenCalled();
  expect(sendPrompt).not.toHaveBeenCalled();
});

it("preserves the explicit skip action without fabricating selected answers", () => {
  act(() => button("Пропустить — собрать сразу").click());
  expect(skip).toHaveBeenCalledOnce();
  expect(done).not.toHaveBeenCalled();
  expect(sendPrompt).not.toHaveBeenCalled();
});
