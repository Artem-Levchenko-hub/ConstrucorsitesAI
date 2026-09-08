import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { usePromptStream } from "@/hooks/usePromptStream";
import { cancelGeneration, getLatestGeneration } from "@/lib/api/messages";
import type { GenerationRun, Message, WsEvent } from "@/lib/api/types";

vi.mock("@/lib/api/messages", () => ({
  cancelGeneration: vi.fn(), getLatestGeneration: vi.fn(), sendPrompt: vi.fn(),
}));
vi.mock("@/lib/api/mocks", () => ({ USE_MOCKS: false }));
vi.mock("sonner", () => ({ toast: { error: vi.fn(), info: vi.fn() } }));

function run(project = "p", status: GenerationRun["status"] = "running"): GenerationRun {
  return {
    id: `run-${project}`, project_id: project, assistant_message_id: `message-${project}`,
    status, response_mode: "build", created_at: "2026-09-08T00:00:00Z",
    started_at: null, finished_at: null,
  };
}
class Socket {
  static OPEN = 1;
  static CONNECTING = 0;
  static instances: Socket[] = [];
  readyState = 1;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onopen: (() => void) | null = null;
  send = vi.fn<(data: string) => void>();
  close = vi.fn(() => { this.readyState = 3; this.onclose?.(); });
  constructor(readonly url: string) { Socket.instances.push(this); }
  emit(event: WsEvent) { this.onmessage?.({ data: JSON.stringify(event) }); }
}
let client: QueryClient;
let root: Root;
let container: HTMLDivElement;
let mounted = false;
let stream: ReturnType<typeof usePromptStream>;
function Harness({ project }: { project: string }) {
  const current = usePromptStream(project, "fixture");
  useEffect(() => { stream = current; }, [current]);
  return null;
}
function seed(project: string) {
  client.setQueryData<Message[]>(["messages", project], [{
    id: `message-${project}`, project_id: project, role: "assistant", content: "Fixture",
    model_id: "fixture", snapshot_id: null, tokens_in: null, tokens_out: null,
    generation_status: "running", created_at: "2026-09-08T00:00:00Z",
  }]);
}
async function mount(project = "p") {
  await act(async () => root.render(
    <QueryClientProvider client={client}><Harness project={project} /></QueryClientProvider>,
  ));
  mounted = true;
}
async function unmount() {
  await act(async () => root.unmount()); mounted = false;
}
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.useFakeTimers(); vi.stubGlobal("WebSocket", Socket);
  Socket.instances = [];
  vi.mocked(getLatestGeneration).mockImplementation(async (project) => run(project));
  vi.mocked(cancelGeneration).mockResolvedValue(run("p", "cancelled"));
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  seed("p");
  container = document.createElement("div"); document.body.append(container);
  root = createRoot(container);
});
afterEach(async () => {
  if (mounted) await unmount();
  // Explicit test cleanup; not evidence that product unmount cleans sockets.
  for (const socket of Socket.instances) { socket.onclose = null; socket.close(); }
  client.clear(); container.remove(); vi.clearAllTimers(); vi.useRealTimers();
  vi.unstubAllGlobals(); vi.clearAllMocks();
});

describe("prompt stream reconnect", () => {
  it("F5 reattaches active canonical run and reconnects after 1 second with replay watermark", async () => {
    await mount(); const first = Socket.instances[0];
    expect(first.url).toContain("/api/ws/projects/p?run_id=run-p&after_seq=0");
    await act(async () => first.emit({
      type: "generation.replay.complete", data: { run_id: "run-p", high_water: 7 },
    }));
    await act(async () => first.close());
    await act(async () => vi.advanceTimersByTime(999)); expect(Socket.instances).toHaveLength(1);
    await act(async () => vi.advanceTimersByTime(1)); expect(Socket.instances).toHaveLength(2);
    expect(Socket.instances[1].url).toContain("run_id=run-p&after_seq=7");
  });
  it("confirmed cancellation closes actual transport without reconnect", async () => {
    await mount(); const first = Socket.instances[0];
    await act(async () => stream.cancel());
    expect(first.close).toHaveBeenCalledTimes(1); expect(first.onclose).toBeNull();
    await act(async () => vi.advanceTimersByTime(25_000));
    expect(Socket.instances).toHaveLength(1); expect(first.send).not.toHaveBeenCalled();
  });
});
