import { expect, it, vi } from "vitest";
import MaxCatalogPage from "@/app/(app)/max/[id]/catalog/page";

const load = vi.hoisted(() => vi.fn());
vi.mock("@/lib/max-project-server", () => ({ loadMaxProject: load }));
vi.mock("@/components/max/MaxOwnerCatalog", () => ({ MaxOwnerCatalog: () => null }));

it("checks project access before rendering the catalog", async () => {
  load.mockResolvedValue({ id: "owned", name: "Каталог" });
  const page = await MaxCatalogPage({ params: Promise.resolve({ id: "owned" }) });
  expect(load).toHaveBeenCalledWith("owned", "/max/owned/catalog");
  expect(page.props).toMatchObject({ projectId: "owned", projectName: "Каталог" });
  const denied = new Error("Not your project"); load.mockRejectedValue(denied);
  await expect(MaxCatalogPage({ params: Promise.resolve({ id: "other" }) })).rejects.toBe(denied);
});
