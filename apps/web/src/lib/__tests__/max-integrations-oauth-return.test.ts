import { beforeEach, describe, expect, it, vi } from "vitest";
import MaxIntegrationsPage from "@/app/(app)/max/[id]/integrations/page";

const boundary = vi.hoisted(() => ({ redirect: vi.fn(), load: vi.fn() }));
vi.mock("next/navigation", () => ({ redirect: boundary.redirect }));
vi.mock("@/lib/max-project-server", () => ({ loadMaxProject: boundary.load }));

describe("standalone project connections", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    boundary.load.mockResolvedValue({ id: "owned-project", name: "Мой магазин" });
  });
  it("preserves the conflict outcome so the owner sees that no grant was replaced", async () => {
    const page = await MaxIntegrationsPage({ params: Promise.resolve({ id: "owned-project" }), searchParams: Promise.resolve({ oauth: "conflict" }) });
    expect(boundary.load).toHaveBeenCalledWith("owned-project", "/max/owned-project/integrations?oauth=conflict");
    expect(boundary.redirect).not.toHaveBeenCalled();
    expect(page.props).toMatchObject({ active: "integrations", title: "Подключения" });
    expect(page.props.children.props.tab).toBe("services");
  });
  it("does not forward arbitrary or multiple provider query values", async () => {
    for (const oauth of ["<untrusted>", ["conflict", "connected"]]) {
      const page = await MaxIntegrationsPage({ params: Promise.resolve({ id: "owned-project" }), searchParams: Promise.resolve({ oauth }) });
      expect(boundary.load).toHaveBeenLastCalledWith("owned-project", "/max/owned-project/integrations");
      expect(page.props.children.props.tab).toBe("bot");
    }
  });
  it("opens the services tab on a supported installation return", async () => {
    const page = await MaxIntegrationsPage({ params: Promise.resolve({ id: "owned-project" }), searchParams: Promise.resolve({ integration: "moysklad" }) });
    expect(page.props.children.props.tab).toBe("services");
    expect(boundary.redirect).not.toHaveBeenCalled();
  });
});
