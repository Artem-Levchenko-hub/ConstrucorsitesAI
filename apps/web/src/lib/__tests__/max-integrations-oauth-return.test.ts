import { beforeEach, describe, expect, it, vi } from "vitest";
import MaxIntegrationsPage from "@/app/(app)/max/[id]/integrations/page";

const boundary = vi.hoisted(() => ({ redirect: vi.fn(), load: vi.fn() }));
vi.mock("next/navigation", () => ({ redirect: boundary.redirect }));
vi.mock("@/lib/max-project-server", () => ({ loadMaxProject: boundary.load }));

describe("integration OAuth return to the editor", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    boundary.load.mockResolvedValue({ id: "owned-project" });
  });
  it("preserves the conflict outcome so the owner sees that no grant was replaced", async () => {
    await MaxIntegrationsPage({ params: Promise.resolve({ id: "owned-project" }), searchParams: Promise.resolve({ oauth: "conflict" }) });
    expect(boundary.redirect).toHaveBeenCalledWith("/max/owned-project?panel=services&oauth=conflict");
  });
  it("does not forward arbitrary or multiple provider query values", async () => {
    for (const oauth of ["<untrusted>", ["conflict", "connected"]]) {
      await MaxIntegrationsPage({ params: Promise.resolve({ id: "owned-project" }), searchParams: Promise.resolve({ oauth }) });
      expect(boundary.redirect).toHaveBeenLastCalledWith("/max/owned-project?panel=services");
    }
  });
});
