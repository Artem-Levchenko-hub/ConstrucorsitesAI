import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { usePromptStream } from "@/hooks/usePromptStream";
import { getLatestGeneration, sendPrompt } from "@/lib/api/messages";
import type { Message } from "@/lib/api/types";
import { ApiError } from "@/lib/api/client";
import { useMaxAdaptation } from "@/lib/use-max-adaptation";
import { toast } from "sonner";

vi.mock("@/lib/api/messages", () => ({
  cancelGeneration: vi.fn(), getLatestGeneration: vi.fn(), sendPrompt: vi.fn(),
}));
vi.mock("@/lib/api/mocks", () => ({ USE_MOCKS: false }));
vi.mock("sonner", () => ({ toast: { error: vi.fn(), info: vi.fn() } }));
class Socket {
  static OPEN = 1; static CONNECTING = 0;
  readyState = 1;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onopen: (() => void) | null = null;
  send() {}
  close() { this.readyState = 3; }
}
let client: QueryClient;
let root: Root;
let container: HTMLDivElement;
let stream: ReturnType<typeof usePromptStream>;
let attachment: ReturnType<typeof useMaxAdaptation>;
const options = {
  restorationAdaptation: { operation_id: "op", expected_draft_snapshot_id: "head" },
  idempotencyKey: "restoration-adapt:op", skipClarify: true,
};
function Harness() {
  const value = usePromptStream("p", "fixture");
  const reference = useMaxAdaptation("p");
  useEffect(() => { stream = value; }, [value]);
  useEffect(() => { attachment = reference; }, [reference]);
  return null;
}
async function mount() {
  await act(async () => root.render(<QueryClientProvider client={client}><Harness /></QueryClientProvider>));
}
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.useFakeTimers(); vi.stubGlobal("WebSocket", Socket); vi.clearAllMocks();
  localStorage.clear();
  vi.mocked(getLatestGeneration).mockResolvedValue(null);
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount()); client.clear(); container.remove();
  vi.clearAllTimers(); vi.useRealTimers(); vi.unstubAllGlobals();
});
it("does not acknowledge an adaptation as an in-memory queued request while another run is active", async () => {
  client.setQueryData<Message[]>(["messages", "p"], [{
    id: "m", project_id: "p", role: "assistant", content: "Running", model_id: "model",
    snapshot_id: null, tokens_in: null, tokens_out: null, generation_status: "running",
    created_at: "2026-09-10T00:00:00Z",
  }]);
  vi.mocked(getLatestGeneration).mockResolvedValue({ id: "run", project_id: "p",
    assistant_message_id: "m", status: "running", response_mode: "edit",
    created_at: "2026-09-10T00:00:00Z", started_at: null, finished_at: null });
  await mount();
  await act(async () => { expect(await stream.submit("Adapt", "model", [], options)).toBe(false); });
  expect(sendPrompt).not.toHaveBeenCalled();
  expect(stream.pendingPrompt).toBeNull();
});
it("passes the exact structured reference and stable key through the real stream hook", async () => {
  vi.mocked(sendPrompt).mockResolvedValue({ run_id: "r", message_id: "m", snapshot_id: null,
    replayed: true, run_status: "completed" });
  await mount();
  await act(async () => { expect(await stream.submit("Adapt", "model", [], options)).toBe(true); });
  expect(sendPrompt).toHaveBeenCalledWith("p", "Adapt", "model", [], options);
});
it("retains the attachment and real error after a rejected historical source, including a pending duplicate", async () => {
  let reject!: (reason: Error) => void;
  vi.mocked(sendPrompt).mockReturnValue(new Promise((_resolve, fail) => { reject = fail; }));
  await mount();
  await act(async () => { attachment.attach("Adapt", options.restorationAdaptation); });
  const send = async () => {
    const accepted = await stream.submit("Adapt", "model", [], options);
    if (accepted) attachment.clear(options.restorationAdaptation.operation_id);
    return accepted;
  };
  let first!: Promise<boolean>;
  await act(async () => { first = send(); });
  await act(async () => { expect(await send()).toBe(false); });
  expect(attachment.attachment?.reference).toEqual(options.restorationAdaptation);
  await act(async () => {
    reject(new ApiError(409, { code: "conflict", message: "Исторический исходник недоступен" }));
    expect(await first).toBe(false);
  });
  expect(attachment.attachment?.reference).toEqual(options.restorationAdaptation);
  expect(sendPrompt).toHaveBeenCalledOnce();
  expect(client.getQueryData<Message[]>(["messages", "p"])?.at(-1)?.tokens_out).toBe(0);
  expect(toast.info).not.toHaveBeenCalledWith("Генерация уже запущена", expect.anything());
  expect(toast.error).toHaveBeenCalledWith("Генерация не запустилась", expect.objectContaining({
    description: expect.stringContaining("Исторический исходник недоступен"),
  }));
});
