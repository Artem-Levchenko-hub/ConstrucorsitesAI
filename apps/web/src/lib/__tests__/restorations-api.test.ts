import { afterEach, expect, it, vi } from "vitest";
import { apiFetch } from "@/lib/api/client";
import { listRestorations, getRestoration, prepareRestoration, applyRestoration, cancelRestoration } from "@/lib/api/restorations";

vi.mock("@/lib/api/client", () => ({ apiFetch: vi.fn(async () => ({})) }));
afterEach(() => vi.clearAllMocks());
it("reads list and operation with caller cancellation and bounded requests", async () => {
  const signal = new AbortController().signal;
  await listRestorations("project", signal); await getRestoration("project", "operation", signal);
  expect(apiFetch).toHaveBeenNthCalledWith(1, "/api/projects/project/restorations", { signal, timeoutMs: 15_000 });
  expect(apiFetch).toHaveBeenNthCalledWith(2, "/api/projects/project/restorations/operation", { signal, timeoutMs: 15_000 });
});
it("sends explicit preparation identity without invoking legacy rollback or publication", async () => {
  const payload = { target_version_id: "v3", expected_draft_snapshot_id: "s11", idempotency_key: "logical-prepare" };
  await prepareRestoration("project", payload);
  expect(apiFetch).toHaveBeenCalledExactlyOnceWith("/api/projects/project/restorations", {
    method: "POST", json: payload, timeoutMs: 30_000,
  });
});
it("binds apply to operation, reviewed report and expected draft", async () => {
  const payload = { report_revision: 4, expected_draft_snapshot_id: "s11", idempotency_key: "logical-apply" };
  await applyRestoration("project", "operation", payload);
  expect(apiFetch).toHaveBeenCalledExactlyOnceWith("/api/projects/project/restorations/operation/apply", {
    method: "POST", json: payload, timeoutMs: 30_000,
  });
});
it("cancels the selected operation explicitly", async () => {
  await cancelRestoration("project", "operation");
  expect(apiFetch).toHaveBeenCalledExactlyOnceWith("/api/projects/project/restorations/operation/cancel", {
    method: "POST", json: {}, timeoutMs: 30_000,
  });
});
