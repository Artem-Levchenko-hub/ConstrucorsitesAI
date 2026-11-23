import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ChatPanel } from "@/components/workspace/ChatPanel";
import { sendPrompt } from "@/lib/api/messages";
import { readQueuedPrompt, writeQueuedPrompt } from "@/lib/studio-prompt-queue";
import { PromptInput } from "@/components/workspace/PromptInput";
import type { GenerationEngine } from "@/lib/generation-engine";

const mocks = vi.hoisted(() => ({ submit: vi.fn(), profile: vi.fn() }));
vi.mock("@/hooks/usePromptStream", () => ({ usePromptStream: () => ({
  submit: mocks.submit, cancel: vi.fn(), cancelPending: vi.fn(), pendingPrompt: null,
}) }));
vi.mock("@/lib/api/owner-profile", () => ({ getOwnerProfile: mocks.profile }));
vi.mock("@/lib/api/messages", async importOriginal => ({
  ...await importOriginal<typeof import("@/lib/api/messages")>(), listMessages: async () => [],
  getLatestGeneration: async () => null,
}));
vi.mock("@/lib/api/mocks", () => ({ USE_MOCKS: false }));
vi.mock("@/lib/api/max-studio", () => ({ getMaxProjectConfig: async () => ({ config_version: 1 }) }));
vi.mock("@/components/workspace/AgentTranscript", () => ({ AgentTranscript: () => null }));
vi.mock("@/hooks/useVoiceInput", () => ({ useVoiceInput: () => ({ supported: false, state: "idle" }) }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("sonner", () => ({ toast: { info: vi.fn(), success: vi.fn(), warning: vi.fn(), error: vi.fn() } }));

let root: Root, host: HTMLDivElement, client: QueryClient;
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.clearAllMocks(); sessionStorage.clear(); window.history.replaceState(null, "", "/max/p");
  mocks.submit.mockResolvedValue(true);
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
});
afterEach(async () => {
  await act(async () => root.unmount()); client.clear(); host.remove(); vi.unstubAllGlobals();
  sessionStorage.clear();
});

async function mount(allowed: boolean) {
  mocks.profile.mockResolvedValue({ id: "owner", can_choose_generation_engine: allowed });
  await act(async () => root.render(<QueryClientProvider client={client}>
    <ChatPanel projectId="p" projectSlug="p" initialCanChooseGenerationEngine={allowed} />
  </QueryClientProvider>));
  await act(async () => vi.waitFor(() => expect(client.getQueryState(["messages", "p"])?.status).toBe("success")));
}

async function typePrompt(text: string) {
  const input = host.querySelector("textarea")!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(input, text);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

async function clickSend() {
  await act(async () => [...host.querySelectorAll("button")].find(b => b.textContent?.trim() === "Отправить")!.click());
}

it("QA submits with Sol without exposing model controls", async () => {
  await mount(true);
  expect(host.querySelector("select")).toBeNull();
  expect(host.textContent).not.toContain("Следующий запрос");
  expect(host.textContent).not.toContain("GPT-6.2 Sol");
  expect(host.textContent).not.toContain("Claude Sonnet");
  expect(mocks.submit).not.toHaveBeenCalled();
  await typePrompt("Создай дневник растений");
  await clickSend();
  expect(mocks.submit).toHaveBeenCalledTimes(1);
  expect(mocks.submit.mock.calls[0][3]).toMatchObject({ generationEngine: "sol", idempotencyKey: expect.any(String) });
});

it("ordinary users send the captured Sol default without a QA selector", async () => {
  await mount(false);
  expect(host.querySelector("select")).toBeNull();
  expect(mocks.submit).not.toHaveBeenCalled();
  await typePrompt("Создай дневник питания");
  await clickSend();
  expect(mocks.submit).toHaveBeenCalledTimes(1);
  expect(mocks.submit.mock.calls[0][3]).toMatchObject({ generationEngine: "sol" });
});

it("explicit client capability revocation hides the selector and restores the normal next-request default", async () => {
  await mount(true);
  expect(host.querySelector("select")).toBeNull();
  await act(async () => {
    client.setQueryData(["owner-profile"], { id: "owner", can_choose_generation_engine: false });
    await vi.waitFor(() => expect(host.querySelector("select")).toBeNull());
  });
  expect(mocks.submit).not.toHaveBeenCalled();
  await typePrompt("Создай дневник питания");
  await clickSend();
  expect(mocks.submit.mock.calls[0][3]).toMatchObject({ generationEngine: "sol" });
});

it("a previous QA login's cached profile cannot expose the selector in an ordinary server session", async () => {
  client.setQueryData(["owner-profile"], { id: "previous-owner", can_choose_generation_engine: true });
  mocks.profile.mockImplementation(() => new Promise(() => {}));
  await act(async () => root.render(<QueryClientProvider client={client}>
    <ChatPanel projectId="p" projectSlug="p" initialCanChooseGenerationEngine={false} />
  </QueryClientProvider>));
  expect(host.querySelector("select")).toBeNull();
  expect(mocks.submit).not.toHaveBeenCalled();
});

it("server-authorized QA can review the first Sol wizard brief before it is sent, retaining the handoff on unknown acceptance", async () => {
  const brief = "Хочу дневник растений с красивым оформлением";
  window.history.replaceState(null, "", "/max/p?starter=1");
  sessionStorage.setItem("omnia:max:starter:p", brief);
  mocks.profile.mockImplementation(() => new Promise(() => {}));
  mocks.submit.mockResolvedValue(false);
  await act(async () => root.render(<QueryClientProvider client={client}>
    <ChatPanel projectId="p" projectSlug="p" initialCanChooseGenerationEngine />
  </QueryClientProvider>));
  await act(async () => {
    await vi.waitFor(() => expect(client.getQueryState(["messages", "p"])?.status).toBe("success"));
    // Flush the query's already-scheduled observer notification before checking
    // the child composer update; a successful cache alone is not a DOM commit.
    await new Promise(resolve => setTimeout(resolve, 0));
  });
  expect(host.querySelector("textarea")?.value).toBe(brief);
  expect(mocks.submit).not.toHaveBeenCalled();
  expect(host.querySelector("select")).toBeNull();
  expect(mocks.submit).not.toHaveBeenCalled();
  const send = [...host.querySelectorAll("button")].find(b => b.textContent?.trim() === "Отправить")!;
  await act(async () => send.click());
  expect(mocks.submit).toHaveBeenCalledTimes(1);
  expect(mocks.submit.mock.calls[0][0]).toBe(brief);
  expect(mocks.submit.mock.calls[0][3]).toMatchObject({ generationEngine: "sol", skipClarify: true, idempotencyKey: "max-starter-p" });
  expect(sessionStorage.getItem("omnia:max:starter:p")).toBe(brief);
  expect(location.search).toBe("?starter=1");
  mocks.submit.mockResolvedValue(true);
  await act(async () => send.click());
  expect(mocks.submit.mock.calls[1][3].idempotencyKey).toBe("max-starter-p");
  expect(sessionStorage.getItem("omnia:max:starter:p")).toBeNull();
  expect(location.search).toBe("");
});

it("an ordinary stable wizard handoff preserves omission for server-side old/new resolution without waiting for the profile", async () => {
  const brief = "Создай дневник растений";
  window.history.replaceState(null, "", "/max/p?starter=1");
  sessionStorage.setItem("omnia:max:starter:p", brief);
  mocks.profile.mockImplementation(() => new Promise(() => {}));
  await act(async () => root.render(<QueryClientProvider client={client}>
    <ChatPanel projectId="p" projectSlug="p" />
  </QueryClientProvider>));
  await act(async () => vi.waitFor(() => expect(mocks.submit).toHaveBeenCalledTimes(1)));
  expect(mocks.submit.mock.calls[0][0]).toBe(brief);
  expect(mocks.submit.mock.calls[0][3]).toEqual({ skipClarify: true, idempotencyKey: "max-starter-p" });
  expect(sessionStorage.getItem("omnia:max:starter:p")).toBeNull();
  expect(host.querySelector("select")).toBeNull();
});

it.each(["sol", "claude", undefined] as const)("a queued submit retains its exact %s engine identity across reload", generationEngine => {
  writeQueuedPrompt("test-queue", { text: "Build", modelId: "topmix-v1", phase: "sending",
    opts: { ...(generationEngine ? { generationEngine } : {}), idempotencyKey: "stable-request-key" } });
  expect(readQueuedPrompt("test-queue")).toMatchObject({ phase: "retry",
    opts: { idempotencyKey: "stable-request-key" } });
  expect(readQueuedPrompt("test-queue")!.opts.generationEngine).toBe(generationEngine);
  const corrupted = JSON.parse(sessionStorage.getItem("test-queue")!);
  corrupted.opts.generationEngine = "arbitrary-model";
  sessionStorage.setItem("test-queue", JSON.stringify(corrupted));
  expect(readQueuedPrompt("test-queue")).toBeNull();
});

it("wire sends only the closed engine choice, preserving the ignored legacy model field", async () => {
  const fetch = vi.fn(async (_url: unknown, _init?: RequestInit) => new Response(JSON.stringify({ message_id: "m" }), {
    status: 202, headers: { "content-type": "application/json" },
  }));
  vi.stubGlobal("fetch", fetch);
  await sendPrompt("p", "Build", "untrusted-model", null, { generationEngine: "sol", idempotencyKey: "stable-request-key" });
  expect(JSON.parse(fetch.mock.calls[0][1]!.body as string)).toEqual({
    prompt: "Build", generation_engine: "sol", idempotency_key: "stable-request-key",
  });
});

it("an unresolved QA profile becoming default Sol keeps an unconfirmed submit key; Claude changes it", async () => {
  const submit = vi.fn(async (..._args: unknown[]) => false);
  const renderComposer = async (generationEngine?: GenerationEngine) => {
    await act(async () => root.render(<PromptInput projectId="p" generationEngine={generationEngine}
      onSubmit={submit} onCancel={() => {}} onCancelPending={() => {}}
      isStreaming={false} pendingPrompt={null} />));
  };
  await renderComposer();
  const input = host.querySelector("textarea")!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(input, "Build a journal");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  const send = async () => act(async () => [...host.querySelectorAll("button")].find(b => b.textContent?.trim() === "Отправить")!.click());
  await send();
  await renderComposer("sol");
  await send();
  expect(submit.mock.calls[1][2]).toMatchObject(submit.mock.calls[0][2] as object);
  expect(submit.mock.calls[1][3]).toBe(true);
  await renderComposer("claude");
  await send();
  expect(submit.mock.calls[2][2]).not.toMatchObject(submit.mock.calls[0][2] as object);
  expect(submit.mock.calls[2][3]).toBe(false);
});
