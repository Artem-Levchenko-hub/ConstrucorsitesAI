import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { deleteProject } from "@/lib/api/projects";

vi.mock("@/lib/api/mocks", () => ({ USE_MOCKS: false, mockApi: {} }));

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { "content-type": "application/json" },
});
describe("durable project deletion", () => {
  const request = vi.fn();

  beforeEach(() => {
    vi.stubGlobal("fetch", request);
    request.mockReset();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("returns accepted, distinct from physical deletion, with a single request", async () => {
    request.mockResolvedValueOnce(json({ state: "deleting", project_id: "own" }, 202));
    expect(await deleteProject("own")).toEqual({ state: "deleting" });
    expect(request).toHaveBeenCalledTimes(1);
    expect(request.mock.calls[0]?.[1].method).toBe("DELETE");
  });

  it("does not treat a lost reply or a login failure as acceptance", async () => {
    request.mockRejectedValueOnce(new TypeError("offline"));
    await expect(deleteProject("own")).rejects.toThrow("offline");
    request.mockResolvedValueOnce(json({ error: { code: "unauthorized", message: "Login" } }, 401));
    await expect(deleteProject("own")).rejects.toMatchObject({ status: 401 });
  });

  it("preserves the synchronous 204 contract and rejects mismatched acknowledgements", async () => {
    request.mockResolvedValueOnce(new Response(null, { status: 204 }));
    expect(await deleteProject("own")).toEqual({ state: "deleted" });
    request.mockResolvedValueOnce(json({ state: "deleting", project_id: "other" }, 202));
    await expect(deleteProject("own")).rejects.toThrow("подтвердить");
  });
});
