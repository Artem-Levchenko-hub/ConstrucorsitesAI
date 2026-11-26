import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, StrictMode, type ComponentProps } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ChatMessage } from "@/components/workspace/ChatMessage";
import type { Message } from "@/lib/api/types";

vi.mock("@/components/workspace/AgentTranscript", () => ({ AgentTranscript: () => null }));

const legacyMessage: Message = {
  id: "build-message",
  project_id: "project-a",
  snapshot_id: "snapshot-a",
  role: "assistant",
  generation_status: "completed",
  content: "Приложение готово.",
  model_id: null,
  tokens_in: null,
  tokens_out: null,
  created_at: "2026-11-25T14:00:00Z",
};

type ChatProps = ComponentProps<typeof ChatMessage>;

let host: HTMLDivElement;
let root: Root;
let client: QueryClient;
let requests: { url: string; method: string; body: unknown }[];
let respond: (url: string) => Response | Promise<Response>;

beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  vi.stubEnv("NEXT_PUBLIC_API_URL", "");
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  client = new QueryClient({ defaultOptions: { queries: { retry: 1, retryDelay: 0 } } });
  requests = [];
  respond = () => Response.json({ changes: [] });
  vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
    requests.push({ url, method: init.method ?? "GET", body: init.body });
    return respond(url);
  });
});

afterEach(async () => {
  await act(async () => root.unmount());
  client.clear();
  host.remove();
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

async function render(...overrides: Partial<ChatProps>[]) {
  await act(async () => root.render(
    <StrictMode>
      <QueryClientProvider client={client}>
        {overrides.map(props => (
          <ChatMessage key={props.message?.id ?? legacyMessage.id} message={legacyMessage}
            projectId="project-a" presentation="studio" {...props} />
        ))}
      </QueryClientProvider>
    </StrictMode>,
  ));
}

function disclosures() {
  return [...host.querySelectorAll("summary")].filter(summary => summary.textContent === "Что изменилось в этой версии");
}

async function waitFor(check: () => void) {
  await act(async () => { await vi.waitFor(check); });
}

async function clickDisclosure(index = 0) {
  const summary = disclosures()[index];
  expect(summary).toBeDefined();
  await act(async () => summary.click());
  return summary.closest("details")!;
}

it("loads only the selected version after opening its history disclosure", async () => {
  let resolveChanges!: (response: Response) => void;
  respond = () => new Promise(resolve => { resolveChanges = resolve; });
  await render({}, { message: { ...legacyMessage, id: "build-two", snapshot_id: "snapshot-b" } });
  expect(disclosures()).toHaveLength(2);
  expect(host.textContent).toContain("Приложение готово.");
  expect(requests).toEqual([]);

  const details = await clickDisclosure(1);
  expect(details.open).toBe(true);
  await waitFor(() => expect(requests).toEqual([
    { url: "/api/projects/project-a/snapshots/snapshot-b/changes", method: "GET", body: undefined },
  ]));
  expect(details.querySelector('[role="status"]')?.textContent).toContain("Загружаем изменения");

  await act(async () => resolveChanges(Response.json({ changes: ["Добавлена форма записи", "Обновлён календарь"] })));
  await waitFor(() => expect([...details.querySelectorAll("li")].map(item => item.textContent)).toEqual([
    "Добавлена форма записи", "Обновлён календарь",
  ]));

  await clickDisclosure(1);
  expect(details.open).toBe(false);
  await clickDisclosure(1);
  await waitFor(() => expect(details.textContent).toContain("Обновлён календарь"));
  expect(requests).toHaveLength(1);
});

it("keeps state and cached changes scoped to both the project and the snapshot", async () => {
  const results: Record<string, string> = {
    "/api/projects/project-a/snapshots/snapshot-a/changes": "Форма записи",
    "/api/projects/project-a/snapshots/snapshot-b/changes": "Календарь",
    "/api/projects/project-b/snapshots/snapshot-b/changes": "Корзина",
  };
  respond = url => Response.json({ changes: [results[url]] });
  await render({});
  await clickDisclosure();
  await waitFor(() => expect(host.textContent).toContain("Форма записи"));

  const nextSnapshot = { ...legacyMessage, snapshot_id: "snapshot-b" };
  await render({ message: nextSnapshot });
  expect(disclosures()[0].closest("details")!.open).toBe(false);
  expect(host.textContent).not.toContain("Форма записи");
  expect(requests).toHaveLength(1);
  await clickDisclosure();
  await waitFor(() => expect(host.textContent).toContain("Календарь"));

  await render({ projectId: "project-b", message: { ...nextSnapshot, project_id: "project-b" } });
  expect(disclosures()[0].closest("details")!.open).toBe(false);
  expect(host.textContent).not.toContain("Календарь");
  expect(requests).toHaveLength(2);
  await clickDisclosure();
  await waitFor(() => expect(host.textContent).toContain("Корзина"));
  expect(requests.map(request => request.url)).toEqual(Object.keys(results));
});

it("shows a retry action after a failed read and an empty state after retry", async () => {
  respond = () => Response.json({ error: { code: "internal_error", message: "Unavailable" } }, { status: 503 });
  await render({});
  const details = await clickDisclosure();
  await waitFor(() => expect(details.querySelector('[role="alert"]')?.textContent).toContain("Не удалось загрузить изменения"));
  expect(requests).toHaveLength(1);

  const retry = [...details.querySelectorAll("button")].find(button => button.textContent === "Повторить загрузку")!;
  expect(retry).toBeDefined();
  respond = () => Response.json({ changes: [] });
  await act(async () => {
    retry.focus();
    retry.click();
  });
  await waitFor(() => expect(details.querySelector('[role="status"]')?.textContent).toContain("Для этой версии описания изменений нет"));
  expect(document.activeElement).toBe(details.querySelector("summary"));
  expect(requests).toHaveLength(2);
});

it.each([
  "Готово — приложение собрано и проверено.",
  "Готово — правка применена и проверена.",
  "Готово — приложение собрано.",
  "Готово — правка применена.",
  "Готово — приложение собрано. Запуск превью проверен.",
  "Готово — правка применена. Сборка и запуск превью проверены.",
])("offers changes for the historical completion %s", async content => {
  await render({ message: { ...legacyMessage, content } });
  expect(disclosures()).toHaveLength(1);
  expect(host.textContent).toContain(content);
  expect(requests).toEqual([]);
});

const ineligible: { reason: string; props?: Partial<ChatProps>; row?: Partial<Message> }[] = [
  { reason: "outside MAX studio", props: { presentation: "default" } },
  { reason: "a different project", props: { projectId: "project-b" } },
  { reason: "no project context", props: { projectId: undefined } },
  { reason: "a streaming message", props: { streaming: true } },
  { reason: "a project question answer", row: { chat_question: true } },
  { reason: "a user message", row: { role: "user" } },
  { reason: "a system message", row: { role: "system" } },
  { reason: "an unavailable snapshot", row: { snapshot_id: null } },
  { reason: "a missing generation outcome", row: { generation_status: null } },
  { reason: "a failed generation", row: { generation_status: "failed" } },
  { reason: "a cancelled generation", row: { generation_status: "cancelled" } },
  { reason: "a restored version", row: { content: "Готово — версия адаптирована к текущим данным и восстановлена." } },
  { reason: "a success with its own remaining checks", row: { content: "Приложение готово.\n\nОсталось проверить отправку заявки." } },
];

it.each(ineligible)("leaves the disclosure out for $reason", async ({ props, row }) => {
  await render({ ...props, message: { ...legacyMessage, ...row } });
  expect(disclosures()).toEqual([]);
  expect(requests).toEqual([]);
});

it.each(["Приложение готово.", "Версия сохранена."])("renders the report for %s inline once", async title => {
  await render({ message: { ...legacyMessage, content: `${title}\n\n**Что изменилось**\n\n- Добавлена форма записи` } });
  expect(host.querySelector("strong")?.textContent).toBe("Что изменилось");
  expect(host.querySelector("li")?.textContent).toBe("Добавлена форма записи");
  expect(disclosures()).toEqual([]);
  expect(requests).toEqual([]);
});
