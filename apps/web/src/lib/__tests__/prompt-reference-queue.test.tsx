import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { usePromptStream } from "@/hooks/usePromptStream";
import { getLatestGeneration, sendPrompt } from "@/lib/api/messages";
import { ApiError } from "@/lib/api/client";

vi.mock("@/lib/api/messages", () => ({ cancelGeneration: vi.fn(), getLatestGeneration: vi.fn(), sendPrompt: vi.fn() }));
vi.mock("@/lib/api/mocks", () => ({ USE_MOCKS: false }));
vi.mock("sonner", () => ({ toast: { error: vi.fn(), info: vi.fn(), dismiss: vi.fn() } }));
class Socket {
  static OPEN = 1; static CONNECTING = 0; readyState = 1;
  onmessage = null; onclose = null; onopen = null;
  send() {} close() { this.readyState = 3; }
}
let client: QueryClient; let root: Root; let host: HTMLDivElement;
let stream: ReturnType<typeof usePromptStream>;
const references = { referenceImages: [{ id: "i", filename: "x.png", mime_type: "image/png", size_bytes: 4, width: 1, height: 1, url: "/private" }], idempotencyKey: "stable-image-key" };
function Harness() { const value = usePromptStream("p", "p"); useEffect(() => { stream = value; }, [value]); return null; }
beforeEach(async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true }); vi.stubGlobal("WebSocket", Socket); vi.clearAllMocks();
  vi.mocked(getLatestGeneration).mockResolvedValue(null);
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  await act(async () => root.render(<QueryClientProvider client={client}><Harness /></QueryClientProvider>));
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); host.remove(); vi.unstubAllGlobals(); });
it("keeps an image queue unacknowledged until accepted and resolves cancellation as refusal", async () => {
  vi.mocked(sendPrompt).mockResolvedValue({ run_id: "run", message_id: "m", snapshot_id: null });
  await act(async () => { expect(await stream.submit("first", "model")).toBe(true); });
  let settled = false; let queued!: Promise<boolean>;
  await act(async () => { queued = stream.submit("second", "model", [], references); void queued.then(() => { settled = true; }); });
  expect(settled).toBe(false); expect(stream.pendingPrompt).toBe("second"); expect(sendPrompt).toHaveBeenCalledTimes(1);
  await act(async () => { stream.cancelPending(); expect(await queued).toBe(false); });
});
it("does not acknowledge images when the server reports an unrelated active generation", async () => {
  vi.mocked(sendPrompt).mockRejectedValue(new ApiError(409, { code: "generation_active", message: "active", details: { active_message_id: "existing" } }));
  vi.mocked(getLatestGeneration).mockResolvedValue({ id: "run", project_id: "p", assistant_message_id: "existing", status: "running", response_mode: "edit", created_at: "now", started_at: null, finished_at: null });
  await act(async () => { expect(await stream.submit("image", "model", [], references)).toBe(false); });
  expect(sendPrompt).toHaveBeenCalledWith("p", "image", "model", [], references);
});
