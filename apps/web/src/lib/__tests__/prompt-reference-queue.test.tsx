import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { usePromptStream } from "@/hooks/usePromptStream";
import { getLatestGeneration, sendPrompt } from "@/lib/api/messages";
import { ApiError } from "@/lib/api/client";
import type { GenerationRun, WsEvent } from "@/lib/api/types";

vi.mock("@/lib/api/messages", () => ({ cancelGeneration: vi.fn(), getLatestGeneration: vi.fn(), sendPrompt: vi.fn() }));
vi.mock("@/lib/api/mocks", () => ({ USE_MOCKS: false }));
vi.mock("sonner", () => ({ toast: { error: vi.fn(), info: vi.fn(), dismiss: vi.fn() } }));
class Socket {
  static instances: Socket[] = [];
  static OPEN = 1; static CONNECTING = 0; readyState = 1;
  onmessage: ((event: { data: string }) => void) | null = null; onclose = null; onopen = null;
  constructor() { Socket.instances.push(this); }
  send() {} close() { this.readyState = 3; }
  emit(event: WsEvent) { this.onmessage?.({ data: JSON.stringify(event) }); }
}
let client: QueryClient; let root: Root; let host: HTMLDivElement;
let stream: ReturnType<typeof usePromptStream>;
const references = { referenceImages: [{ id: "i", filename: "x.png", mime_type: "image/png", size_bytes: 4, width: 1, height: 1, url: "/private" }], idempotencyKey: "stable-image-key" };
const fonts = { fontSelections: [{ id: "f", family: "Onest", filename: "x.woff2", size_bytes: 4, sha256: "a".repeat(64), weight: 400, style: "normal" as const, source: "upload" as const, license: null, role: "all" as const }], idempotencyKey: "stable-font-key" };
function Harness({ owner = "owner-a", project = "p" }: { owner?: string; project?: string }) {
  const value = usePromptStream(project, project, owner);
  useEffect(() => { stream = value; }, [value]);
  return null;
}
async function render(owner = "owner-a", project = "p") {
  await act(async () => root.render(<QueryClientProvider client={client}><Harness key={`${owner}:${project}`} owner={owner} project={project} /></QueryClientProvider>));
}
const run: GenerationRun = { id: "run", project_id: "p", assistant_message_id: "m", status: "running", response_mode: "edit", created_at: "now", started_at: null, finished_at: null };
const done = { type: "llm.done", data: { message_id: "m", tokens_in: 2, tokens_out: 4, cost_rub: 0 } } as const;
beforeEach(async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true }); vi.stubGlobal("WebSocket", Socket); vi.resetAllMocks(); vi.useFakeTimers(); sessionStorage.clear(); Socket.instances = [];
  vi.mocked(getLatestGeneration).mockResolvedValue(null);
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  await render();
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); host.remove(); vi.clearAllTimers(); vi.useRealTimers(); vi.unstubAllGlobals(); sessionStorage.clear(); });
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
it("keeps fonts queued until server acceptance and preserves them on cancellation", async () => {
  vi.mocked(sendPrompt).mockResolvedValue({ run_id: "run", message_id: "m", snapshot_id: null });
  await act(async () => { await stream.submit("first", "model"); });
  let settled=false;let queued!:Promise<boolean>;
  await act(async()=>{queued=stream.submit("font","model",[],fonts);void queued.then(()=>{settled=true;});});
  expect(settled).toBe(false);expect(sendPrompt).toHaveBeenCalledTimes(1);
  await act(async()=>{stream.cancelPending();expect(await queued).toBe(false);});
});
it("does not acknowledge fonts for an unrelated active generation", async () => {
  vi.mocked(sendPrompt).mockRejectedValue(new ApiError(409,{code:"generation_active",message:"active",details:{active_message_id:"existing"}}));
  vi.mocked(getLatestGeneration).mockResolvedValue({id:"run",project_id:"p",assistant_message_id:"existing",status:"running",response_mode:"edit",created_at:"now",started_at:null,finished_at:null});
  await act(async()=>{expect(await stream.submit("font","model",[],fonts)).toBe(false);});
});

it("refuses the queued font draft when the active run is cancelled over WebSocket", async () => {
  vi.mocked(sendPrompt).mockResolvedValue({ run_id: "run", message_id: "m", snapshot_id: null });
  await act(async () => { await stream.submit("first", "model"); });
  let accepted: boolean | undefined;
  await act(async () => { void stream.submit("font", "model", [], fonts).then(value => { accepted = value; }); });
  expect(accepted).toBeUndefined();
  await act(async () => {
    Socket.instances.at(-1)!.onmessage?.({ data: JSON.stringify({
      type: "generation.cancelled", data: { run_id: "run", message_id: "m" },
    }) });
  });
  expect(accepted).toBe(false);
  expect(stream.pendingPrompt).toBeNull();
  expect(sendPrompt).toHaveBeenCalledTimes(1);
});

it("restores an uploaded attachment queue only for its owner and project after a reload", async () => {
  vi.mocked(sendPrompt).mockResolvedValueOnce({ run_id: "run", message_id: "m", snapshot_id: null });
  vi.mocked(getLatestGeneration).mockResolvedValue(run);
  await act(async () => { expect(await stream.submit("first", "model")).toBe(true); });
  const selections = [{ selector: "#hero", comment: "Make this bigger" }];
  const attachments = { ...references, fontSelections: fonts.fontSelections };
  await act(async () => { void stream.submit("follow up", "model", selections, attachments); });
  await act(async () => root.render(null));
  client.clear();
  await render("owner-b");
  expect(stream.pendingPrompt).toBeNull();
  await render("owner-a", "another-project");
  expect(stream.pendingPrompt).toBeNull();
  await render();
  expect(stream.pendingPrompt).toBe("follow up");
  expect(sendPrompt).toHaveBeenCalledTimes(1);

  vi.mocked(sendPrompt).mockResolvedValueOnce({ run_id: "run-2", message_id: "m2", snapshot_id: null });
  vi.mocked(getLatestGeneration).mockResolvedValue({ ...run, status: "completed" });
  await act(async () => Socket.instances.at(-1)!.emit(done));
  await act(async () => vi.advanceTimersByTimeAsync(0));
  expect(sendPrompt).toHaveBeenCalledTimes(2);
  expect(sendPrompt).toHaveBeenLastCalledWith("p", "follow up", "model", selections, expect.objectContaining({
    ...attachments, referenceImages: [{ ...references.referenceImages[0], url: "" }],
  }));
  expect(stream.pendingPrompt).toBeNull();
});

it("retains a failed queued text send on reload and retries it with the same idempotency key", async () => {
  vi.mocked(sendPrompt).mockResolvedValueOnce({ run_id: "run", message_id: "m", snapshot_id: null });
  await act(async () => { expect(await stream.submit("first", "model")).toBe(true); });
  await act(async () => { expect(await stream.submit("follow up", "model", [{ selector: "#hero" }])).toBe(true); });
  vi.mocked(sendPrompt).mockRejectedValueOnce(new Error("response lost"));
  vi.mocked(getLatestGeneration).mockResolvedValue({ ...run, status: "completed" });
  await act(async () => Socket.instances.at(-1)!.emit(done));
  await act(async () => vi.advanceTimersByTimeAsync(0));
  expect(stream.pendingPrompt).toBe("follow up");
  expect(sendPrompt).toHaveBeenCalledTimes(2);
  const key = vi.mocked(sendPrompt).mock.calls[1][4]?.idempotencyKey;
  expect(key).toEqual(expect.any(String));

  await act(async () => root.render(null));
  client.clear();
  await render();
  expect(stream.pendingPrompt).toBe("follow up");
  expect(sendPrompt).toHaveBeenCalledTimes(2);
  vi.mocked(sendPrompt).mockResolvedValueOnce({ run_id: "existing", message_id: "m2", snapshot_id: null, replayed: true, run_status: "completed" });
  await act(async () => stream.retryPending());
  await act(async () => vi.advanceTimersByTimeAsync(0));
  expect(sendPrompt).toHaveBeenCalledTimes(3);
  expect(sendPrompt).toHaveBeenLastCalledWith("p", "follow up", "model", [{ selector: "#hero" }], expect.objectContaining({ idempotencyKey: key }));
  expect(stream.pendingPrompt).toBeNull();
});
