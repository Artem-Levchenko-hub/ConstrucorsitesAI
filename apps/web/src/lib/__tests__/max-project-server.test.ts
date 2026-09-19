import { afterEach, describe, expect, it, vi } from "vitest";

const redirect = vi.fn((target: string) => {
  throw new Error(`redirect:${target}`);
});
const notFound = vi.fn(() => {
  throw new Error("not-found");
});
vi.mock("next/navigation", () => ({ redirect, notFound }));
vi.mock("@/lib/api/mocks", () => ({ USE_MOCKS: false, mockApi: {} }));
const serverApiFetchResult = vi.fn();
vi.mock("@/lib/api/server", () => ({ serverApiFetchResult }));

afterEach(() => vi.clearAllMocks());

describe("loadMaxProject", () => {
  it("returns a MAX Mini App", async () => {
    serverApiFetchResult.mockResolvedValue({ ok: true, data: { id: "p1", template: "max_miniapp" } });
    const { loadMaxProject } = await import("@/lib/max-project-server");
    await expect(loadMaxProject("p1", "/max/p1/dashboard")).resolves.toMatchObject({ id: "p1" });
  });

  it("sends any other project back to the cabinet — it has no editor here", async () => {
    serverApiFetchResult.mockResolvedValue({ ok: true, data: { id: "p2", template: "blank" } });
    const { loadMaxProject } = await import("@/lib/max-project-server");
    await expect(loadMaxProject("p2", "/max/p2/dashboard")).rejects.toThrow("redirect:/max");
  });

  it("keeps the destination through login and hides foreign projects", async () => {
    const { loadMaxProject } = await import("@/lib/max-project-server");
    serverApiFetchResult.mockResolvedValue({ ok: false, status: 401 });
    await expect(loadMaxProject("p3", "/max/p3/publish")).rejects.toThrow(
      "redirect:/login?next=%2Fmax%2Fp3%2Fpublish",
    );
    serverApiFetchResult.mockResolvedValue({ ok: false, status: 404 });
    await expect(loadMaxProject("p3", "/max/p3/publish")).rejects.toThrow("redirect:/max");
  });
});
