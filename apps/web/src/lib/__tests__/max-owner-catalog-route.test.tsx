import { expect, it, vi } from "vitest";
import MaxCatalogPage from "@/app/(app)/max/[id]/catalog/page";
import MaxManagePage from "@/app/(app)/max/[id]/manage/page";

const load = vi.hoisted(() => vi.fn());
vi.mock("@/lib/max-project-server", () => ({ loadMaxProject: load }));
vi.mock("@/components/max/MaxOwnerCatalog", () => ({ MaxOwnerCatalog: () => null }));
vi.mock("@/components/max/MaxOwnerConsole", () => ({ MaxOwnerConsole: () => null }));

it("checks project access before rendering the catalog", async () => {
  load.mockResolvedValue({ id: "owned", name: "Каталог" });
  const page = await MaxCatalogPage({ params: Promise.resolve({ id: "owned" }) });
  expect(load).toHaveBeenCalledWith("owned", "/max/owned/catalog");
  expect(page.props).toMatchObject({ projectId: "owned", projectName: "Каталог" });
  const denied = new Error("Not your project"); load.mockRejectedValue(denied);
  await expect(MaxCatalogPage({ params: Promise.resolve({ id: "other" }) })).rejects.toBe(denied);
});

it("redirects a legacy management catalog link after checking project access", async () => {
  load.mockResolvedValue({ id: "owned", name: "Каталог" });
  const searchParams = Promise.resolve({ section: "catalog" });
  await expect(MaxManagePage({ params: Promise.resolve({ id: "owned" }), searchParams })).rejects.toMatchObject({
    digest: "NEXT_REDIRECT;replace;/max/owned/catalog;307;",
  });
  expect(load).toHaveBeenLastCalledWith("owned", "/max/owned/manage?section=catalog");

  const denied = new Error("Not your project");
  load.mockRejectedValue(denied);
  await expect(MaxManagePage({ params: Promise.resolve({ id: "other" }), searchParams })).rejects.toBe(denied);
});

it("keeps the management route for other sections", async () => {
  load.mockResolvedValue({ id: "owned", name: "Приложение" });
  const page = await MaxManagePage({
    params: Promise.resolve({ id: "owned" }),
    searchParams: Promise.resolve({ section: "orders" }),
  });
  expect(load).toHaveBeenLastCalledWith("owned", "/max/owned/manage");
  expect(page.props).toMatchObject({ projectId: "owned", projectName: "Приложение" });
});
