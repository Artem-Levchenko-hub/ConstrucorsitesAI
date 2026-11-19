import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AgentTranscript } from "@/components/workspace/AgentTranscript";
import { GenerationPhaseCard } from "@/components/workspace/GenerationPhaseCard";
import type { GenerationPhaseId } from "@/lib/generation-phase";
import type { AgentStep, GenerationRun } from "@/lib/api/types";

const api = vi.hoisted(() => ({ latest: vi.fn() }));
vi.mock("@/lib/api/messages", () => ({ getLatestGeneration: api.latest }));
const run: GenerationRun = { id: "11111111-1111-1111-1111-111111111111", project_id: "p", assistant_message_id: "m", status: "running",
  response_mode: "build", created_at: new Date().toISOString(), started_at: new Date().toISOString(), finished_at: null };
let container: HTMLDivElement;
let root: Root;
let client: QueryClient;
let visibility = "visible";
let observed: IntersectionObserverCallback;
const disconnected = vi.fn();

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  visibility = "visible";
  vi.spyOn(document, "visibilityState", "get").mockImplementation(() => visibility as DocumentVisibilityState);
  vi.stubGlobal("IntersectionObserver", class {
    constructor(callback: IntersectionObserverCallback) { observed = callback; }
    observe() {}
    disconnect = disconnected;
  });
  api.latest.mockResolvedValue(run);
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  client.setQueryData(["generation", "p"], run);
  container = document.createElement("div"); document.body.append(container);
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount()); client.clear(); container.remove();
  vi.restoreAllMocks(); vi.unstubAllGlobals(); disconnected.mockClear();
});
async function mount(steps: AgentStep[] = []) {
  client.setQueryData(["agent-steps", "p", "m"], steps);
  await act(async () => root.render(<QueryClientProvider client={client}>
    <AgentTranscript projectId="p" messageId="m" initialSteps={steps} />
  </QueryClientProvider>));
}
const card = () => container.querySelector<HTMLElement>("[data-generation-phase]")!;

it("shows one real phase and keeps technical data/counters in closed native details", async () => {
  const steps: AgentStep[] = [{ runId: run.id, eventId: "22222222-2222-2222-2222-222222222222", seq: 37,
    step: 37, kind: "stalled", action: "Меняю подход",
    path: "", detail: "Technical detail preserved", actionCount: 37, ok: false }];
  await mount(steps);
  expect(container.querySelector("details")!.open).toBe(false);
  expect(container.querySelector("summary")!.textContent).toBe("Подробности работы");
  expect(container.querySelector("summary")!.textContent).not.toContain("37");
  expect(card().textContent).not.toContain("Меняю подход");
  expect(container.querySelector("details")!.textContent).toContain("37 шаг.");
  expect(client.getQueryData(["agent-steps", "p", "m"])).toEqual(steps);
  expect(container.querySelectorAll('[role="status"]')).toHaveLength(1);
  expect(container.querySelector('[role="progressbar"]')).toBeNull();
  await act(async () => { container.querySelector("summary")!.click(); });
  await vi.waitFor(() => expect(container.querySelector("details")!.open).toBe(true));
  await act(async () => container.querySelector<HTMLButtonElement>("ol button")!.click());
  expect(container.textContent).toContain("Technical detail preserved");
  expect(container.querySelector('[class*="text-red"]')).toBeNull();
});

it("keeps rejected done out of the preview announcement and revokes a shell-stale build without losing steps", async () => {
  const build: AgentStep = { runId: run.id, step: 1, kind: "step", tool: "build", action: "build", path: "", ok: true, actionCount: 1 };
  const command: AgentStep = { ...build, step: 2, tool: "command", action: "Выполняю команду", actionCount: 2 };
  const rejected: AgentStep = { ...build, step: 3, tool: "done", action: "done", ok: false, actionCount: 3 };
  const steps = [build, command, rejected];
  await mount(steps);
  expect(card().dataset.generationPhase).toBe("checking");
  expect(card().querySelector('[role="status"]')!.textContent).not.toContain("превью");
  expect(card().textContent).not.toContain("Сборка проверена");
  expect(container.querySelector("details")!.open).toBe(false);
  expect(container.querySelector("details")!.textContent).toContain("3 шаг.");
  expect(client.getQueryData(["agent-steps", "p", "m"])).toEqual(steps);
  await act(async () => {
    client.setQueryData(["agent-steps", "p", "m"], [...steps,
      { ...build, step: 4, actionCount: 4 }, { ...rejected, step: 5, ok: true, actionCount: 5 }]);
    await vi.waitFor(() => expect(card().dataset.generationPhase).toBe("preview"));
  });
  expect(card().textContent).toContain("Сборка проверена");
  expect(container.querySelector("details")!.textContent).toContain("5 шаг.");
});

it("starts and stops its sole work loop with intersection, visibility and actual status", async () => {
  await mount();
  expect(card().dataset.generationPhase).toBe("workspace");
  expect(card().dataset.motion).toBe("paused");
  act(() => observed([{ isIntersecting: true } as IntersectionObserverEntry], {} as IntersectionObserver));
  expect(card().dataset.motion).toBe("running");
  act(() => { visibility = "hidden"; document.dispatchEvent(new Event("visibilitychange")); });
  expect(card().dataset.motion).toBe("paused");
  act(() => { visibility = "visible"; document.dispatchEvent(new Event("visibilitychange")); });
  expect(card().dataset.motion).toBe("running");
  act(() => observed([{ isIntersecting: false } as IntersectionObserverEntry], {} as IntersectionObserver));
  expect(card().dataset.motion).toBe("paused");
  await act(async () => { client.setQueryData(["generation", "p"], { ...run, status: "cancel_requested" });
    await vi.waitFor(() => expect(card().dataset.generationPhase).toBe("stopping")); });
  expect(card().dataset.motion).toBe("paused");
  expect(disconnected).toHaveBeenCalled();
});

it("keeps the live announcement stable when only technical steps/counters advance", async () => {
  const steps: AgentStep[] = [{ runId: run.id, step: 1, kind: "step", tool: "write_file", action: "Пишу экран", path: "page.tsx" }];
  await mount(steps);
  const status = card().querySelector('[role="status"]')!;
  const announced = status.textContent;
  await act(async () => {
    client.setQueryData(["agent-steps", "p", "m"], [...steps, { ...steps[0], step: 2, actionCount: 2 }]);
    await vi.waitFor(() => expect(container.textContent).toContain("2 шаг."));
  });
  expect(status.textContent).toBe(announced);
  expect(container.querySelector("details")!.open).toBe(false);
});

it("runs the elapsed interval only for visible, open details and clears it when hidden", async () => {
  const timer = vi.spyOn(globalThis, "setInterval");
  const clear = vi.spyOn(globalThis, "clearInterval");
  await mount();
  const elapsedCalls = () => timer.mock.calls.filter(([, delay]) => delay === 1000);
  expect(elapsedCalls()).toHaveLength(0);
  act(() => observed([{ isIntersecting: true } as IntersectionObserverEntry], {} as IntersectionObserver));
  expect(elapsedCalls()).toHaveLength(0);
  await act(async () => container.querySelector("summary")!.click());
  await vi.waitFor(() => expect(elapsedCalls()).toHaveLength(1));
  const index = timer.mock.calls.findIndex(([, delay]) => delay === 1000);
  const timerId = timer.mock.results[index].value;
  act(() => { visibility = "hidden"; document.dispatchEvent(new Event("visibilitychange")); });
  expect(clear).toHaveBeenCalledWith(timerId);
  expect(card().dataset.motion).toBe("paused");
});

it.each([
  ["workspace", true], ["prompt", true], ["creating", true], ["checking", true],
  ["preview", true], ["recovering", true], ["queued", false], ["waiting", false],
] as const)("explains background work quietly during %s", async (id, active) => {
  await act(async () => root.render(<GenerationPhaseCard
    phase={{ id, active, title: "Current phase", explanation: "Current explanation", milestones: [] }} motionActive={false} />));
  const notice = container.querySelector("[data-generation-background-notice]")!;
  expect(notice.textContent).toContain("20–30 минут, иногда дольше");
  expect(notice.textContent).toContain("Можно закрыть вкладку");
  expect(notice.textContent).toContain(active ? "работа продолжится на сервере" : "задача сохранена на сервере");
  expect(notice.textContent).toContain("результат");
  expect(notice.querySelectorAll("p")).toHaveLength(2);
  expect(notice.querySelectorAll(":scope > p")).toHaveLength(2);
  expect(notice.querySelector("p")!.textContent).toBe("Создание может занять 20–30 минут, иногда дольше.");
  expect(notice.closest('[aria-live], [role="status"]')).toBeNull();
  expect(card().dataset.motion).toBe("paused");
  expect(container.querySelector('[role="progressbar"]')).toBeNull();
});

it.each<GenerationPhaseId>(["ready", "history", "paused", "stopping", "stopped", "unconfirmed", "idle"])(
  "does not invite leaving during %s", async id => {
    await act(async () => root.render(<GenerationPhaseCard
      phase={{ id, active: false, title: "Current phase", explanation: "Current explanation", milestones: [] }} motionActive={false} />));
    expect(container.querySelector("[data-generation-background-notice]")).toBeNull();
  },
);

it("provides a static reduced-motion path and inherits light/dark shell tokens", () => {
  const css = readFileSync(resolve(process.cwd(), "src/components/workspace/GenerationPhaseCard.module.css"), "utf8");
  expect(css).toContain("prefers-reduced-motion: reduce");
  expect(css.match(/@keyframes\s/g)).toHaveLength(1);
  expect(css).toContain('animation-play-state: paused');
  expect(css).toContain('[data-motion="running"]');
  expect(css).toContain('var(--color-surface)');
  expect(css).toContain('minmax(0, 1fr)');
  expect(css).toContain('container: generation-phase / inline-size');
  expect(css).toContain('@container generation-phase (min-width: 660px)');
  expect(css).toContain('@container generation-phase (max-width: 479px)');
  expect(css).toMatch(/\.cursor\s*\{\s*animation: none/);
  expect(css).not.toMatch(/#[a-fA-F0-9]{3,8}\b|gradient\(|box-shadow/);
});


it("renders only observed facts even when real phase order goes backwards", async () => {
  await act(async () => root.render(<GenerationPhaseCard phase={{ id: "workspace", active: true,
    title: "Готовлю рабочую среду", explanation: "Текущая фаза", milestones: ["Промпт подготовлен"] }} motionActive />));
  expect([...container.querySelectorAll("li")].map(item => item.textContent)).toEqual(["Промпт подготовлен"]);
  expect(card().textContent).not.toContain("Среда подготовлена");
  const status = card().querySelector('[role="status"]')!;
  expect(status.textContent).not.toContain("Промпт подготовлен");
  await act(async () => root.render(<GenerationPhaseCard phase={{ id: "creating", active: true,
    title: "Создаю приложение", explanation: "Работа продолжается после проверки", milestones: [] }} motionActive />));
  expect(card().dataset.generationPhase).toBe("creating");
  expect(container.querySelector('[aria-label="Подтверждённые этапы"]')).toBeNull();
  expect(card().textContent).not.toContain("Сборка проверена");
  expect(container.querySelector('[role="progressbar"]')).toBeNull();
});

it("keeps observed fact updates outside the single stable live announcement", async () => {
  const phase = { id: "creating" as const, active: true, title: "Создаю приложение",
    explanation: "Собираю экраны и действия", milestones: [] as string[] };
  await act(async () => root.render(<GenerationPhaseCard phase={phase} motionActive />));
  const status = card().querySelector('[role="status"]')!;
  const copy = status.textContent;
  await act(async () => root.render(<GenerationPhaseCard
    phase={{ ...phase, milestones: ["Промпт подготовлен"] }} motionActive />));
  expect(card().querySelector('[role="status"]')).toBe(status);
  expect(status.textContent).toBe(copy);
  expect(status.getAttribute("aria-live")).toBe("polite");
  expect(container.querySelectorAll('[role="status"]')).toHaveLength(1);
  expect(container.querySelector("li")!.closest('[aria-live]')).toBeNull();
  const facts = container.querySelector('[aria-label="Подтверждённые этапы"]')!;
  expect(facts.parentElement).toBe(status.parentElement!.parentElement);
  expect(container.querySelector('[data-generation-indicator]')!.getAttribute("aria-hidden")).toBe("true");
});

it("uses one substantial fitted app pane without turning its geometry into progress", async () => {
  const phase = { id: "creating" as const, active: true, title: "Создаю приложение",
    explanation: "Собираю экраны и действия", milestones: [] as string[] };
  await act(async () => root.render(<GenerationPhaseCard phase={phase} motionActive />));
  const mark = container.querySelector('[data-generation-indicator]')!;
  const fittedPane = mark.querySelector("g > rect")!;
  expect(fittedPane.getAttribute("width")).toBe("36");
  expect(fittedPane.getAttribute("height")).toBe("20");
  expect(mark.querySelectorAll("g")).toHaveLength(1);
  expect(mark.querySelector("text, title, [role=progressbar]")).toBeNull();
  expect(container.querySelector('[aria-label="Подтверждённые этапы"]')).toBeNull();
  await act(async () => root.render(<GenerationPhaseCard phase={{ ...phase, id: "ready", active: false,
    title: "Приложение готово" }} motionActive />));
  expect(card().dataset.motion).toBe("paused");
  expect(mark.querySelector("g")).toBeNull();
  expect(container.querySelector("[data-generation-background-notice]")).toBeNull();
});
