import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { onlineManager, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ChatPanel } from "@/components/workspace/ChatPanel";
import { OWNER_PROFILE_QUERY_KEY } from "@/lib/owner-chat-quota";
import type { Message } from "@/lib/api/types";
import { useInspectorStore } from "@/store/inspector";

const mocks = vi.hoisted(() => ({
  submit: vi.fn(), cancel: vi.fn(), history: vi.fn(), profile: vi.fn(), info: vi.fn(),
}));
vi.mock("@/lib/api/mocks", () => ({ USE_MOCKS: false }));
vi.mock("@/lib/api/messages", () => ({ listMessages: mocks.history, getLatestGeneration: async () => null }));
vi.mock("@/lib/api/owner-profile", () => ({ getOwnerProfile: mocks.profile }));
vi.mock("@/lib/api/max-studio", () => ({ getMaxProjectConfig: async () => ({ config_version: 1 }) }));
vi.mock("@/hooks/usePromptStream", () => ({ usePromptStream: () => ({
  submit: mocks.submit, cancel: mocks.cancel, cancelPending: vi.fn(), pendingPrompt: null,
}) }));
vi.mock("@/components/workspace/AgentTranscript", () => ({
  AgentTranscript: ({ streaming }: { streaming: boolean }) => streaming ? <span>Сборка продолжается</span> : null,
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("sonner", () => ({ toast: { info: mocks.info, success: vi.fn(), error: vi.fn() } }));

const projectId = "p";
const question = "заполнил анкету а меню нет";
const answer = "Сборка ещё идёт. Дождитесь результата в этом чате.";
const receipt = { answered: true, user_message_id: "question", assistant_message_id: "answer", answer };
const profile = (remaining: number) => ({
  id: "owner", user_chat_messages_limit: 1, user_chat_messages_remaining: remaining,
});
const message = (overrides: Partial<Message> = {}): Message => ({
  id: "existing-build", project_id: projectId, role: "assistant", content: "Собираю приложение",
  model_id: "model", snapshot_id: null, tokens_in: 0, tokens_out: null,
  created_at: "2026-11-26T00:00:00Z", ...overrides,
});

let host: HTMLDivElement;
let root: Root;
let client: QueryClient;
let requests: { url: string; body: Record<string, unknown> }[];
let respond: (body: Record<string, unknown>) => Promise<Response>;
let serverHistory: Message[];

function mountRoot() {
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  root = createRoot(host);
}

async function render(remaining = 0) {
  mocks.profile.mockResolvedValue(profile(remaining));
  await act(async () => root.render(<QueryClientProvider client={client}>
    <ChatPanel projectId={projectId} projectSlug={projectId} />
  </QueryClientProvider>));
  await act(async () => { await vi.waitFor(() => {
    expect(client.getQueryData(OWNER_PROFILE_QUERY_KEY)).toEqual(profile(remaining));
    expect(client.getQueryState(["messages", projectId])?.status).toBe("success");
  }); });
}

async function typeDraft(text: string) {
  const input = host.querySelector("textarea")!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(input, text);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  return input;
}

const send = () => host.querySelector<HTMLButtonElement>(".prompt-input-send")!;
const stop = () => host.querySelector<HTMLButtonElement>('[aria-label="Прервать генерацию"]');
async function deliverAnswer(body: Record<string, unknown>) {
  if (!serverHistory.some(row => row.id === receipt.user_message_id)) {
    serverHistory = [...serverHistory,
      message({ id: receipt.user_message_id, role: "user", content: String(body.prompt), chat_question: true }),
      message({ id: receipt.assistant_message_id, content: receipt.answer, tokens_out: 0, chat_question: true }),
    ];
  }
  return Response.json(receipt);
}

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.resetAllMocks();
  window.history.replaceState(null, "", "/max/p");
  host = document.createElement("div");
  document.body.append(host);
  mountRoot();
  requests = [];
  serverHistory = [];
  respond = deliverAnswer;
  mocks.history.mockImplementation(async () => serverHistory);
  mocks.submit.mockResolvedValue(true);
  vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
    const body = JSON.parse(String(init.body)) as Record<string, unknown>;
    requests.push({ url, body });
    return respond(body);
  });
});
afterEach(async () => {
  await act(async () => root.unmount());
  client.clear();
  host.remove();
  useInspectorStore.getState().clear();
  vi.unstubAllGlobals();
  onlineManager.setOnline(true);
});

it("answers a plain-language question with zero edits and loads the confirmed answer after F5", async () => {
  let finish!: () => void;
  const checking = new Promise<void>(resolve => { finish = resolve; });
  respond = async body => { await checking; return deliverAnswer(body); };
  await render();
  const input = await typeDraft(question);
  expect(send().disabled).toBe(false);
  await act(async () => send().click());
  expect(host.querySelector(".max-chat-footer p[role=status]")?.textContent).toContain("Проверяю запрос");
  expect(requests).toEqual([{ url: "/api/projects/p/chat/question",
    body: { prompt: question, idempotency_key: expect.any(String) } }]);
  expect(input.value).toBe(question);
  await act(async () => finish());
  expect(host.textContent).toContain(answer);
  expect(input.value).toBe("");
  expect(mocks.submit).not.toHaveBeenCalled();
  expect(client.getQueryData(OWNER_PROFILE_QUERY_KEY)).toEqual(profile(0));

  await act(async () => root.unmount());
  client.clear();
  mountRoot();
  await render();
  await vi.waitFor(async () => {
    await act(async () => {});
    expect(host.textContent).toContain(answer);
    expect(host.textContent).toContain(question);
  });
  expect(requests).toHaveLength(1);
});

it.each(["disconnected", "unexpected response", "offline"])("retains the draft and exact key after %s", async failure => {
  await render(1);
  const input = await typeDraft(question);
  if (failure === "offline") onlineManager.setOnline(false);
  respond = async body => {
    if (failure === "disconnected") {
      await deliverAnswer(body);
      throw new Error("Connection lost after commit");
    }
    if (failure === "offline") throw new Error("Offline");
    return Response.json({ answered: "false" });
  };
  await act(async () => send().click());
  expect(mocks.submit).not.toHaveBeenCalled();
  expect(host.querySelector("[role=alert]")?.textContent).toContain("повторите попытку");
  expect(input.value).toBe(question);
  await act(async () => {
    if (failure === "offline") onlineManager.setOnline(true);
    if (failure === "disconnected") await client.refetchQueries({ queryKey: ["messages", projectId] });
  });
  expect(requests).toHaveLength(1);
  respond = deliverAnswer;
  await act(async () => send().click());
  expect(requests[1]).toEqual(requests[0]);
  expect(mocks.submit).not.toHaveBeenCalled();
  expect(host.textContent).toContain(answer);
  expect(Array.from(host.querySelectorAll('[data-chat-role="assistant"]')).filter(row => row.textContent?.includes(answer))).toHaveLength(1);
  expect(input.value).toBe("");
});

it("keeps quota after a definite edit classification, including retry and the keyboard shortcut", async () => {
  respond = async () => Response.json({ answered: false });
  await render();
  const input = await typeDraft("Удали меню");
  await act(async () => send().click());
  await act(async () => window.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", ctrlKey: true, bubbles: true })));
  expect(requests).toHaveLength(2);
  expect(requests[1]).toEqual(requests[0]);
  expect(mocks.submit).not.toHaveBeenCalled();
  expect(mocks.info).toHaveBeenCalledWith(expect.stringContaining("лимит исчерпан"));
  expect(input.value).toBe("Удали меню");
});

it("passes an ordinary edit to generation once classified, and reuses the paid key on transport retry", async () => {
  respond = async () => Response.json({ answered: false });
  mocks.submit.mockResolvedValueOnce(false);
  await render(1);
  const input = await typeDraft("Удали меню");
  await act(async () => send().click());
  expect(requests).toHaveLength(1);
  expect(mocks.submit).toHaveBeenCalledWith("Удали меню", "topmix-v1", [], {
    idempotencyKey: requests[0].body.idempotency_key, generationEngine: "sol",
  });
  expect(input.value).toBe("Удали меню");
  await act(async () => { client.setQueryData(OWNER_PROFILE_QUERY_KEY, profile(0)); });
  await act(async () => send().click());
  expect(requests).toHaveLength(1);
  expect(mocks.submit.mock.calls[1]).toEqual(mocks.submit.mock.calls[0]);
  expect(input.value).toBe("");
});

it("accepts status questions during a build and keeps streaming and stop bound to the build", async () => {
  serverHistory = [message({ generation_status: "running" })];
  await render();
  await typeDraft("Как идёт сборка?");
  expect(send().disabled).toBe(false);
  expect(send().textContent).toBe("Отправить");
  expect(stop()).not.toBeNull();
  await act(async () => send().click());
  expect(host.textContent).toContain(answer);
  expect(host.textContent).toContain("Сборка продолжается");
  expect(stop()).not.toBeNull();
  expect(mocks.cancel).not.toHaveBeenCalled();
  expect(mocks.submit).not.toHaveBeenCalled();
  const draft = await typeDraft("Удали меню");
  const press = new MouseEvent("pointerdown", { bubbles: true, cancelable: true });
  await act(async () => {
    draft.focus();
    stop()!.dispatchEvent(press);
  });
  expect(press.defaultPrevented).toBe(true);
  expect(document.activeElement).toBe(draft);
  await act(async () => stop()!.click());
  expect(mocks.cancel).toHaveBeenCalledOnce();
});

it("answers a typed status question beside discovery choices and submits a chosen answer directly", async () => {
  const interview = message({ id: "interview", content: "Кто будет пользоваться приложением?", tokens_out: 12 });
  const choice = "Участники клуба";
  const typedQuestion = "Как идёт сборка?";
  const choiceButton = () => Array.from(host.querySelectorAll("button")).find(button => button.textContent?.trim() === choice);
  serverHistory = [interview];
  client.setQueryDefaults(["discovery-choices", projectId], { gcTime: Infinity });
  client.setQueryData(["discovery-choices", projectId, interview.id], {
    choices: [choice], allowCustom: true, multiSelect: false,
  });
  await render(1);
  await vi.waitFor(async () => {
    await act(async () => {});
    expect(choiceButton()).toBeDefined();
  });

  await typeDraft(typedQuestion);
  await act(async () => send().click());
  expect(mocks.submit).not.toHaveBeenCalled();
  expect(requests).toEqual([{ url: "/api/projects/p/chat/question",
    body: { prompt: typedQuestion, idempotency_key: expect.any(String) } }]);
  expect(host.textContent).toContain(answer);
  expect(choiceButton()).toBeDefined();

  await act(async () => choiceButton()!.click());
  expect(requests).toHaveLength(1);
  expect(mocks.submit).toHaveBeenCalledExactlyOnceWith(choice, "topmix-v1", [], { generationEngine: "sol" });
});

it("keeps inspector metadata on the edit path", async () => {
  await render(1);
  await act(async () => useInspectorStore.getState().addSelection({
    id: "pick", selector: ".menu", label: "div", text: "Menu",
  }));
  await typeDraft("Как работает меню?");
  await act(async () => send().click());
  expect(requests).toEqual([]);
  expect(mocks.submit).toHaveBeenCalledWith("Как работает меню?", "topmix-v1",
    [expect.objectContaining({ selector: ".menu" })], expect.any(Object));
});

it("runs direct settings and typed credentials through their established endpoints", async () => {
  respond = async () => Response.json({ enabled: true });
  await render(1);
  await typeDraft("Включи AITunnel для ИИ приложения");
  await act(async () => send().click());
  expect(mocks.submit).not.toHaveBeenCalled();

  const syntheticKey = "sk-aitunnel-" + "e".repeat(24);
  await typeDraft(`Подключи AITUNNEL для помощника: ${syntheticKey}`);
  await act(async () => send().click());
  expect(requests.map(request => request.url)).toEqual([
    "/api/projects/p/aitunnel-ai", "/api/projects/p/nutrition-ai",
  ]);
  expect(mocks.submit).toHaveBeenCalledOnce();
  expect(mocks.submit.mock.calls[0][0]).not.toContain(syntheticKey);
});

it("asks message history to retain generation context outside the recent window", async () => {
  const { listMessages } = await vi.importActual<typeof import("@/lib/api/messages")>("@/lib/api/messages");
  const historyFetch = vi.fn(async () => Response.json([]));
  vi.stubGlobal("fetch", historyFetch);
  await listMessages(projectId);
  expect(historyFetch).toHaveBeenCalledExactlyOnceWith(
    "/api/projects/p/messages?include_generation_context=true",
    expect.objectContaining({ credentials: "include" }),
  );
});
