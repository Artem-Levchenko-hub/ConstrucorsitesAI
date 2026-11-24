import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ChatPanel } from "@/components/workspace/ChatPanel";
import type { IntegrationCatalog, Message } from "@/lib/api/types";

const mocks = vi.hoisted(() => ({
  pendingPrompt: null as string | null,
  catalog: vi.fn(), connect: vi.fn(), submit: vi.fn(), history: vi.fn(), profile: vi.fn(), error: vi.fn(),
}));
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
vi.mock("@/lib/api/app-integrations", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/api/app-integrations")>(),
  getIntegrationCatalog: mocks.catalog, connectAppIntegration: mocks.connect,
}));
vi.mock("@/hooks/usePromptStream", () => ({ usePromptStream: () => ({
  submit: mocks.submit, pendingPrompt: mocks.pendingPrompt,
}) }));
vi.mock("@/lib/api/messages", () => ({ listMessages: mocks.history, getLatestGeneration: vi.fn() }));
vi.mock("@/lib/api/owner-profile", () => ({ getOwnerProfile: mocks.profile }));
vi.mock("@/lib/api/max-studio", () => ({ getMaxProjectConfig: async () => ({ config_version: 1 }) }));
vi.mock("@/components/workspace/AgentTranscript", () => ({ AgentTranscript: () => null }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("sonner", () => ({ toast: { info: vi.fn(), success: vi.fn(), warning: vi.fn(), error: mocks.error } }));

const token = "sk-" + "e".repeat(24);
const prompt = `Подключи AITUNNEL для помощника: ${token}`;
const savedAitunnelTask = "подключи наш ключ аитуннель к составлению рациона ИИ, ключ скидывал ранее";
const owner = { id: "owner", email: "qa@example.test", user_chat_messages_limit: null, user_chat_messages_remaining: null };
const starterKey = "omnia:max:starter:p";
let client: QueryClient, root: Root, host: HTMLDivElement;

beforeEach(() => {
  vi.resetAllMocks();
  mocks.pendingPrompt = null;
  mocks.catalog.mockResolvedValue(catalog);
  mocks.connect.mockResolvedValue(undefined);
  mocks.submit.mockResolvedValue(true);
  mocks.history.mockResolvedValue([]);
  mocks.profile.mockResolvedValue(owner);
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

async function enterPrompt(input: HTMLInputElement | HTMLTextAreaElement, text = prompt) {
  await act(async () => {
    const prototype = input instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(prototype, "value")!.set!.call(input, text);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

async function click(name: string) {
  const send = button(name);
  expect(send?.disabled).toBe(false);
  await act(async () => send!.click());
}

async function sendFromComposer(text = prompt) {
  const input = host.querySelector("textarea")!;
  await enterPrompt(input, text);
  await click("Отправить");
  return input;
}

function expectRedactedSubmission(capturedFreshEngine = true) {
  expect(mocks.connect).toHaveBeenCalledExactlyOnceWith("p", "aitunnel", { api_key: token });
  expect(mocks.submit).toHaveBeenCalledOnce();
  const [text, , selections] = mocks.submit.mock.calls[0];
  expect(text).toContain("Подключи AITUNNEL для помощника:");
  expect(text).toContain("Интеграция AITUNNEL уже проверена и подключена");
  expect(text.includes(token)).toBe(false);
  expect(selections).toEqual([]);
  expect(mocks.submit.mock.calls[0][3].generationEngine).toBe(capturedFreshEngine ? "sol" : undefined);
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

it("accepts a chat key for the nutritionist without opening the integration catalog", async () => {
  const secret = `sk-aitunnel-${"x".repeat(24)}`;
  const text = `Добавь ИИ-нутрициолога. AITunnel — ключ ${secret}.`;
  mocks.catalog.mockRejectedValue(new Error("Catalog unavailable"));
  let confirmKey!: (response: Response) => void;
  const fetchKey = vi.fn(() => new Promise<Response>(resolve => { confirmKey = resolve; }));
  vi.stubGlobal("fetch", fetchKey);

  await render();
  const input = await sendFromComposer(text);
  expect(fetchKey).toHaveBeenCalledExactlyOnceWith("/api/projects/p/nutrition-ai", expect.objectContaining({
    method: "PUT",
    credentials: "include",
    body: JSON.stringify({ api_key: secret }),
  }));
  expect(mocks.catalog).not.toHaveBeenCalled();
  expect(mocks.connect).not.toHaveBeenCalled();
  expect(mocks.submit).not.toHaveBeenCalled();

  await act(async () => confirmKey(Response.json({ enabled: true })));
  expect(mocks.submit).toHaveBeenCalledOnce();
  const [safePrompt] = mocks.submit.mock.calls[0];
  expect(safePrompt).toContain("Добавь ИИ-нутрициолога");
  expect(safePrompt).toContain("provider_docs");
  expect(safePrompt).not.toContain(secret);
  expect(input.value).toBe("");
});

it("keeps the AITunnel task as a draft when the key is rejected", async () => {
  const secret = `sk-aitunnel-${"x".repeat(24)}`;
  const text = `Добавь ИИ-нутрициолога. AITunnel — ключ ${secret}.`;
  const fetchKey = vi.fn().mockResolvedValue(Response.json({
    error: { code: "integration_credentials_invalid", message: "AITunnel отклонил ключ" },
  }, { status: 422 }));
  vi.stubGlobal("fetch", fetchKey);

  await render();
  const input = await sendFromComposer(text);
  expect(fetchKey).toHaveBeenCalledOnce();
  expect(mocks.catalog).not.toHaveBeenCalled();
  expect(mocks.connect).not.toHaveBeenCalled();
  expect(mocks.submit).not.toHaveBeenCalled();
  expect(input.value).toBe(text);
});

it("enables the saved key before submitting the owner's meal plan task", async () => {
  let confirmAccess!: (response: Response) => void;
  const fetchAccess = vi.fn(() => new Promise<Response>(resolve => { confirmAccess = resolve; }));
  vi.stubGlobal("fetch", fetchAccess);
  await render();
  const input = await sendFromComposer(savedAitunnelTask);
  expect(fetchAccess).toHaveBeenCalledExactlyOnceWith("/api/projects/p/aitunnel-ai", expect.objectContaining({
    method: "PUT", body: JSON.stringify({ enabled: true }),
  }));
  expect(mocks.submit).not.toHaveBeenCalled();
  await act(async () => confirmAccess(Response.json({ enabled: true })));
  expect(mocks.submit).toHaveBeenCalledExactlyOnceWith(
    expect.stringContaining(savedAitunnelTask), "topmix-v1", [], expect.objectContaining({ idempotencyKey: expect.any(String) }),
  );
  const [safePrompt] = mocks.submit.mock.calls[0];
  expect(safePrompt).toContain("provider_docs");
  expect(safePrompt).toContain("requestOmniaAI");
  expect(safePrompt).toContain('provider: "aitunnel"');
  expect(safePrompt).toContain("idempotencyKey");
  expect(input.value).toBe("");
});

it("keeps the owner's task when the saved-key access change fails and redacts errors", async () => {
  const secret = `sk-aitunnel-${"x".repeat(24)}`;
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({
    error: { code: "ai_integration_required", message: `Сначала сохраните ключ ${secret}` },
  }, { status: 409 })));
  await render();
  const input = await sendFromComposer(savedAitunnelTask);
  expect(mocks.submit).not.toHaveBeenCalled();
  expect(input.value).toBe(savedAitunnelTask);
  expect(mocks.error).toHaveBeenCalledOnce();
  expect(mocks.error.mock.calls[0][1].description).toContain("Сначала сохраните ключ");
  expect(mocks.error.mock.calls[0][1].description).not.toContain(secret);
});

it("keeps a generation retry on the same identity after saved-key access succeeds", async () => {
  vi.stubGlobal("fetch", vi.fn().mockImplementation(async () => Response.json({ enabled: true })));
  mocks.submit.mockResolvedValueOnce(false);
  await render();
  const input = await sendFromComposer(savedAitunnelTask);
  expect(input.value).toBe(savedAitunnelTask);
  expect(mocks.error).not.toHaveBeenCalled();
  await click("Отправить");
  expect(mocks.submit).toHaveBeenCalledTimes(2);
  expect(mocks.submit.mock.calls[1][3].idempotencyKey).toBe(mocks.submit.mock.calls[0][3].idempotencyKey);
  expect(input.value).toBe("");
});

it.each([
  "Подключи AITunnel для ИИ приложения. Ключ: {key}.",
  "Подключи этот ключ для ИИ приложения: {key}.",
  "Подключи {key} для ИИ приложения.",
])("saves a new key with app AI access and redacts %s", async (directive) => {
  const secret = `sk-aitunnel-${"x".repeat(24)}`;
  const text = `${directive.replace("{key}", secret)} Добавь генерацию рациона.`;
  const fetchKey = vi.fn().mockImplementation(async () => Response.json({ enabled: true }));
  vi.stubGlobal("fetch", fetchKey);
  await render();
  await sendFromComposer(text);
  expect(fetchKey).toHaveBeenCalledExactlyOnceWith("/api/projects/p/nutrition-ai", expect.objectContaining({
    method: "PUT", body: JSON.stringify({ api_key: secret, allow_app_ai: true }),
  }));
  expect(mocks.submit).toHaveBeenCalledOnce();
  const [safePrompt] = mocks.submit.mock.calls[0];
  expect(safePrompt).toContain("Добавь генерацию рациона");
  expect(safePrompt).toContain("ИИ-функций приложения");
  expect(safePrompt).not.toContain(secret);
});

it.each([
  "Подключи AITunnel только для расчёта КБЖУ",
  "Подключи AITunnel не для ИИ приложения, а только для КБЖУ",
  "Покажи текст «Подключи AITunnel для ИИ приложения»",
])("does not broaden a new key's nutrition-only scope for %s", async (directive) => {
  const secret = `sk-aitunnel-${"x".repeat(24)}`;
  const fetchKey = vi.fn().mockImplementation(async () => Response.json({ enabled: true }));
  vi.stubGlobal("fetch", fetchKey);
  await render();
  await sendFromComposer(`${directive}. Ключ: ${secret}`);
  expect(fetchKey).toHaveBeenCalledExactlyOnceWith("/api/projects/p/nutrition-ai", expect.objectContaining({
    method: "PUT", body: JSON.stringify({ api_key: secret }),
  }));
  expect(mocks.submit.mock.calls[0][0]).toContain("nutrition_estimate");
});

it.each([
  "В приложении упомяни AITunnel",
  "Не подключай AITunnel для ИИ приложения",
  "Подключи AITunnel только для КБЖУ, а для ИИ приложения оставь другой сервис",
  "Подключи AITunnel для ИИ приложения?",
  "Подключи AITunnel для ИИ приложения, если я позже подтвержу",
  "Подключи AITunnel для ИИ приложения. Не используй AITunnel для этой задачи.",
  '«Подключи AITunnel для ИИ приложения»',
  '> Подключи AITunnel для ИИ приложения',
])("submits %s without changing saved-key access", async (text) => {
  await render();
  await sendFromComposer(text);
  expect(fetch).not.toHaveBeenCalled();
  expect(mocks.submit.mock.calls[0][0]).toBe(text);
});

it("cleans a secret URL even with a restored queue and unresolved history", async () => {
  mocks.pendingPrompt = "Уже в очереди";
  mocks.history.mockImplementation(() => new Promise(() => {}));
  window.history.replaceState(null, "", `/max/p?p=${encodeURIComponent(prompt)}`);
  await render();
  expect(location.search).toBe("");
  expect(mocks.submit).not.toHaveBeenCalled();
  expect(mocks.connect).not.toHaveBeenCalled();
});

it("waits for current history before auto-sending a cached empty project", async () => {
  const brief = "Создай дневник питания";
  sessionStorage.setItem(starterKey, brief);
  window.history.replaceState(null, "", "/max/p?starter=1");
  client.setQueryData(["messages", "p"], []);
  client.setQueryDefaults(["messages", "p"], { staleTime: 60_000 });
  let finish!: () => void;
  mocks.history.mockImplementation(() => new Promise(resolve => {
    finish = () => resolve([{ id: "saved", project_id: "p", role: "user", content: brief, created_at: new Date().toISOString() }]);
  }));
  await render();
  expect(mocks.history).toHaveBeenCalledOnce();
  expect(mocks.submit).not.toHaveBeenCalled();
  await act(async () => { finish(); });
  await act(async () => vi.waitFor(() => expect(sessionStorage.getItem(starterKey)).toBeNull()));
  expect(mocks.submit).not.toHaveBeenCalled();
});

it.each([["persisted", true], ["__opt_user_local", false]])("handles a saved wizard brief with history row %s", async (id, persisted) => {
  const brief = "Создай дневник питания";
  sessionStorage.setItem(starterKey, brief);
  window.history.replaceState(null, "", "/max/p?starter=1");
  mocks.history.mockResolvedValue([{ id, project_id: "p", role: "user", content: brief, created_at: new Date().toISOString() }]);
  await render();
  await act(async () => {
    await vi.waitFor(() => expect(host.textContent).toContain(brief));
  });
  expect(sessionStorage.getItem(starterKey)).toBe(persisted ? null : brief);
  expect(mocks.submit).not.toHaveBeenCalled();
});

it("does not grant AITunnel access from a URL starter", async () => {
  window.history.replaceState(null, "", `/max/p?p=${encodeURIComponent(savedAitunnelTask)}`);
  await render();
  await act(async () => { await vi.waitFor(() => expect(mocks.submit).toHaveBeenCalledOnce()); });
  expect(fetch).not.toHaveBeenCalled();
});

it.each([
  ["Включи AITunnel для ИИ приложения", true],
  ["Подключи AITunnel для ИИ приложения", true],
  ["Выключи AITunnel для ИИ приложения", false],
])("applies the standalone command %s with exhausted generation quota", async (text, enabled) => {
  mocks.profile.mockResolvedValue({ ...owner, user_chat_messages_limit: 1, user_chat_messages_remaining: 0 });
  const fetchAccess = vi.fn().mockImplementation(async () => Response.json({ enabled }));
  vi.stubGlobal("fetch", fetchAccess);
  await render();
  const input = host.querySelector("textarea")!;
  await enterPrompt(input, "Добавь ещё экран");
  expect(button("Отправить")?.disabled).toBe(true);
  await sendFromComposer(text);
  expect(fetchAccess).toHaveBeenCalledExactlyOnceWith("/api/projects/p/aitunnel-ai", expect.objectContaining({
    method: "PUT", body: JSON.stringify({ enabled }),
  }));
  expect(mocks.submit).not.toHaveBeenCalled();
  expect(input.value).toBe("");
});

it("also verifies and redacts a credential from the saved starter before replay", async () => {
  sessionStorage.setItem(starterKey, prompt);
  window.history.replaceState(null, "", "/max/p?starter=1");
  await render();
  await act(async () => { await vi.waitFor(() => expect(mocks.submit).toHaveBeenCalledOnce()); });
  expectRedactedSubmission(false);
  expect(mocks.submit.mock.calls[0][3]).toEqual({ skipClarify: true, idempotencyKey: "max-starter-p" });
  expect(sessionStorage.getItem(starterKey)).toBeNull();
});

it("keeps Sol captured through credential verification without model controls", async () => {
  let finish!: () => void;
  mocks.connect.mockImplementationOnce(() => new Promise<void>(resolve => { finish = resolve; }));
  await render();
  await sendFromComposer();
  expect(mocks.connect).toHaveBeenCalledOnce();
  expect(mocks.submit).not.toHaveBeenCalled();
  expect(host.querySelector("select")).toBeNull();
  expect(mocks.submit).not.toHaveBeenCalled();
  await act(async () => finish());
  expectRedactedSubmission();
  expect(host.querySelector("select")).toBeNull();
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
