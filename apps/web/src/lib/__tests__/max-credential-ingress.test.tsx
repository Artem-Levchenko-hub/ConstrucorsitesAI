import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ChatPanel } from "@/components/workspace/ChatPanel";
import type { IntegrationCatalog, Message } from "@/lib/api/types";

const mocks = vi.hoisted(() => ({ connect: vi.fn(), submit: vi.fn(), history: vi.fn() }));
const catalog: IntegrationCatalog = {
  providers: [{
    key: "aitunnel", name: "AITUNNEL", category: "ai", description: "ИИ", capabilities: [],
    fields: [{ key: "api_key", label: "API-ключ", placeholder: "", help: "", secret: true, required: true }],
    available: true, recommended: false, requirement: null, docs_url: "",
    oauth_supported: false, oauth_available: false, connection_mode: "credentials",
  }],
  connections: [],
  recommended_pack: { key: "qa", title: "", description: "", provider_keys: [], bound_count: 0, reusable_count: 0 },
};
vi.mock("@/lib/api/app-integrations", () => ({
  getIntegrationCatalog: async () => catalog, connectAppIntegration: mocks.connect,
}));
vi.mock("@/hooks/usePromptStream", () => ({ usePromptStream: () => ({
  submit: mocks.submit, pendingPrompt: null,
}) }));
vi.mock("@/lib/api/messages", () => ({ listMessages: mocks.history, getLatestGeneration: vi.fn() }));
vi.mock("@/lib/api/owner-profile", () => ({
  getOwnerProfile: async () => ({ id: "owner", email: "qa@example.test", user_chat_messages_limit: null, user_chat_messages_remaining: null }),
}));
vi.mock("@/lib/api/max-studio", () => ({ getMaxProjectConfig: async () => ({ config_version: 1 }) }));
vi.mock("@/components/workspace/AgentTranscript", () => ({ AgentTranscript: () => null }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("sonner", () => ({ toast: { info: vi.fn(), success: vi.fn(), warning: vi.fn(), error: vi.fn() } }));

const token = "sk-" + "e".repeat(24);
const prompt = `Подключи AITUNNEL для помощника: ${token}`;
const starterKey = "omnia:max:starter:p";
let client: QueryClient, root: Root, host: HTMLDivElement;

beforeEach(() => {
  vi.resetAllMocks();
  mocks.connect.mockResolvedValue(undefined);
  mocks.submit.mockResolvedValue(true);
  mocks.history.mockResolvedValue([]);
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("Unexpected external request")));
  window.history.replaceState(null, "", "/max/p");
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
});

afterEach(async () => {
  await act(async () => root.unmount());
  client.clear(); host.remove(); vi.unstubAllGlobals();
  sessionStorage.clear(); window.history.replaceState(null, "", "/");
});

async function render() {
  await act(async () => root.render(<QueryClientProvider client={client}><ChatPanel projectId="p" projectSlug="p" /></QueryClientProvider>));
}

const button = (name: string) => [...host.querySelectorAll("button")].find(candidate =>
  candidate.textContent?.trim() === name || candidate.getAttribute("aria-label") === name);

async function enterPrompt(input: HTMLInputElement | HTMLTextAreaElement) {
  await act(async () => {
    const prototype = input instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(prototype, "value")!.set!.call(input, prompt);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

async function click(name: string) {
  const send = button(name);
  expect(send?.disabled).toBe(false);
  await act(async () => send!.click());
}

async function sendFromComposer() {
  const input = host.querySelector("textarea")!;
  await enterPrompt(input);
  await click("Отправить");
  return input;
}

function expectRedactedSubmission() {
  expect(mocks.connect).toHaveBeenCalledExactlyOnceWith("p", "aitunnel", { api_key: token });
  expect(mocks.submit).toHaveBeenCalledOnce();
  const [text, , selections] = mocks.submit.mock.calls[0];
  expect(text).toContain("Подключи AITUNNEL для помощника:");
  expect(text).toContain("Интеграция AITUNNEL уже проверена и подключена");
  expect(text.includes(token)).toBe(false);
  expect(selections).toEqual([]);
}

it("verifies a composer credential before generation and clears the accepted draft", async () => {
  let finish!: () => void;
  mocks.connect.mockImplementationOnce(() => new Promise<void>(resolve => { finish = resolve; }));
  await render();
  const input = await sendFromComposer();
  expect(mocks.connect).toHaveBeenCalledOnce();
  expect(mocks.submit).not.toHaveBeenCalled();
  expect(input.value === prompt).toBe(true);
  await act(async () => finish());
  expectRedactedSubmission();
  expect(input.value).toBe("");
});

it("keeps the credential draft and never submits it if verification fails", async () => {
  mocks.connect.mockRejectedValueOnce(new Error("Ключ не прошёл проверку"));
  await render();
  const input = await sendFromComposer();
  expect(mocks.connect).toHaveBeenCalledOnce();
  expect(mocks.submit).not.toHaveBeenCalled();
  expect(input.value === prompt).toBe(true);
});

it("also verifies and redacts a credential from the saved starter before replay", async () => {
  sessionStorage.setItem(starterKey, prompt);
  window.history.replaceState(null, "", "/max/p?starter=1");
  await render();
  await act(async () => { await vi.waitFor(() => expect(mocks.submit).toHaveBeenCalledOnce()); });
  expectRedactedSubmission();
  expect(mocks.submit.mock.calls[0][3]).toEqual({ skipClarify: true, idempotencyKey: "max-starter-p" });
  expect(sessionStorage.getItem(starterKey)).toBeNull();
});

it("verifies and redacts a credential typed into an inline discovery answer", async () => {
  const question: Message = {
    id: "q", project_id: "p", role: "assistant", content: "Что подключить?", snapshot_id: null,
    model_id: "model", tokens_in: 0, tokens_out: 1, created_at: "2026-11-06T04:00:00Z",
  };
  mocks.history.mockResolvedValue([question]);
  client.setQueryData(["messages", "p"], [question]);
  client.setQueryData(["discovery-choices", "p", "q"], {
    choices: ["Пропустить"], allowCustom: true, multiSelect: false,
  });
  await render();
  await click("Другое");
  await enterPrompt(host.querySelector<HTMLInputElement>('input[placeholder="Впишите свой ответ"]')!);
  await click("Отправить ответ");
  expectRedactedSubmission();
});
