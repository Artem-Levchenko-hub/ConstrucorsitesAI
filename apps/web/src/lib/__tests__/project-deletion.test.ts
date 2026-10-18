import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { deleteProject, waitForProjectDeletion } from "@/lib/api/projects";

vi.mock("@/lib/api/mocks", () => ({ USE_MOCKS: false, mockApi: {} }));

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { "content-type": "application/json" },
});
const missing = () => json({ error: { code: "not_found", message: "project not found" } }, 404);

describe("durable project deletion", () => {
  const request = vi.fn();

  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal("fetch", request);
    request.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("accepts 202 without claiming completion and polls to a real 404", async () => {
    request.mockResolvedValueOnce(json({ state: "deleting", project_id: "own" }, 202));
    expect(await deleteProject("own")).toEqual({ state: "deleting" });
    request.mockResolvedValueOnce(json({ id: "own" })).mockResolvedValueOnce(missing());
    const finished = waitForProjectDeletion("own");
    await vi.advanceTimersByTimeAsync(2_000);
    expect(await finished).toEqual({ state: "deleted" });
    expect(request.mock.calls.map(([, init]) => init.method ?? "GET")).toEqual([
      "DELETE", "GET", "GET",
    ]);
  });

  it("keeps an unfinished deletion pending after the bounded observation window", async () => {
    request.mockImplementation(async () => json({ id: "own" }));
    const finished = waitForProjectDeletion("own");
    await vi.advanceTimersByTimeAsync(120_000);
    expect(await finished).toEqual({ state: "deleting" });
    expect(request.mock.calls.length).toBe(60);
    expect(request.mock.calls.every(([, init]) => init.method !== "DELETE")).toBe(true);
  });

  it("does not turn lost observation or login failure into successful deletion", async () => {
    request.mockRejectedValueOnce(new TypeError("offline"));
    expect(await waitForProjectDeletion("own")).toEqual({ state: "deleting" });
    request.mockResolvedValueOnce(json({ error: { code: "unauthorized", message: "Login" } }, 401));
    expect(await waitForProjectDeletion("own")).toEqual({ state: "deleting" });
  });

  it("preserves the synchronous 204 contract and rejects mismatched acknowledgements", async () => {
    request.mockResolvedValueOnce(new Response(null, { status: 204 }));
    expect(await deleteProject("own")).toEqual({ state: "deleted" });
    request.mockResolvedValueOnce(json({ state: "deleting", project_id: "other" }, 202));
    await expect(deleteProject("own")).rejects.toThrow("подтвердить");
  });
});
