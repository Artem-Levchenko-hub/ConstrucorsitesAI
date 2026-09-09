import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useMaxAdaptation } from "@/lib/use-max-adaptation";
import { sendPrompt } from "@/lib/api/messages";
import { apiFetch } from "@/lib/api/client";

vi.mock("@/lib/api/client", () => ({ apiFetch: vi.fn() }));
vi.mock("@/lib/api/mocks", () => ({ USE_MOCKS: false, mockApi: {} }));
let root: Root;
let container: HTMLDivElement;
let controller: ReturnType<typeof useMaxAdaptation>;
const reference = { operation_id: "op-a", expected_draft_snapshot_id: "head-a" };
function Harness({ project }: { project: string }) {
  const value = useMaxAdaptation(project);
  useEffect(() => { controller = value; }, [value]);
  return <span>{value.attachment?.prompt ?? "none"}</span>;
}
async function render(project = "a") {
  await act(async () => root.render(<Harness project={project} />));
}
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  localStorage.clear(); vi.clearAllMocks();
  container = document.createElement("div"); document.body.append(container);
  root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });

it("retains the reference and editable prompt through F5, scoped to its project", async () => {
  await render();
  await act(async () => { expect(controller.attach("Adapt calendar", reference)).toBe(true); });
  await act(async () => root.unmount());
  root = createRoot(container);
  await render();
  expect(controller.attachment).toEqual({ projectId: "a", prompt: "Adapt calendar", reference });
  await render("b");
  expect(controller.attachment).toBeNull();
  await render("a");
  expect(controller.attachment?.reference).toEqual(reference);
});

it("late acceptance of an earlier request cannot remove a newly selected reference", async () => {
  await render();
  await act(async () => { controller.attach("first", reference); });
  await act(async () => { controller.attach("second", { ...reference, operation_id: "op-b" }); });
  await act(async () => controller.clear("op-a"));
  expect(controller.attachment?.prompt).toBe("second");
  await act(async () => controller.clear("op-b"));
  expect(controller.attachment).toBeNull();
});

it("sends a structured reference with the same retry key and leaves ordinary prompts unchanged", async () => {
  vi.mocked(apiFetch).mockResolvedValue({ run_id: "run", message_id: "message" });
  const options = { restorationAdaptation: reference, idempotencyKey: "restoration-adapt:op-a" };
  await sendPrompt("a", "Edited request", "model", null, options);
  await sendPrompt("a", "Edited request", "model", null, options);
  expect(apiFetch).toHaveBeenNthCalledWith(1, "/api/projects/a/prompt", {
    method: "POST", timeoutMs: 30_000,
    json: { prompt: "Edited request", idempotency_key: "restoration-adapt:op-a",
      restoration_adaptation: reference },
  });
  expect(vi.mocked(apiFetch).mock.calls[1]).toEqual(vi.mocked(apiFetch).mock.calls[0]);
  await sendPrompt("a", "Normal request", "model", null, { idempotencyKey: "normal" });
  expect(apiFetch).toHaveBeenLastCalledWith("/api/projects/a/prompt", {
    method: "POST", timeoutMs: 30_000,
    json: { prompt: "Normal request", idempotency_key: "normal" },
  });
});
