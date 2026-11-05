import { beforeEach, expect, it, vi } from "vitest";
import MaxWorkspacePage from "@/app/(app)/max/[id]/page";
import MaxPublishPage from "@/app/(app)/max/[id]/publish/page";
import MaxIntegrationsPage from "@/app/(app)/max/[id]/integrations/page";

const mocks = vi.hoisted(() => ({ getSession: vi.fn(), load: vi.fn(), project: vi.fn() }));
vi.mock("@/lib/auth-mock", () => ({ getSession: mocks.getSession }));
vi.mock("@/lib/max-project-server", () => ({ loadMaxProject: mocks.load }));
vi.mock("@/lib/api/server", () => ({ serverApiFetchResult: mocks.project }));
vi.mock("@/lib/api/mocks", () => ({ USE_MOCKS: false }));
vi.mock("next/navigation", () => ({
  redirect: (path: string) => { throw new Error(`redirect:${path}`); },
  notFound: () => { throw new Error("notFound"); },
}));

const project = { id: "owned", name: "Калькулятор", template: "max_miniapp" };
const params = Promise.resolve({ id: project.id });

beforeEach(() => {
  vi.resetAllMocks();
  mocks.getSession.mockResolvedValue({ email: "test@example.invalid" });
  mocks.project.mockResolvedValue({ ok: true, data: project });
  mocks.load.mockResolvedValue(project);
});

it.each([
  [{ data: "details" }, "/settings?tab=app&section=details"],
  [{ data: "policies", panel: "max" }, "/settings?tab=app&section=policies"],
  [{ data: "owner" }, "/settings?tab=app&section=owner"],
  [{ data: "content" }, "/catalog"],
  [{ panel: "max" }, "/integrations"],
  [{ panel: "publish" }, "/publish"],
  [{ panel: "hosting" }, "/publish"],
  [{ panel: "services", oauth: "conflict", integration: "moysklad" }, "/integrations?tab=services&oauth=conflict&integration=moysklad"],
  [{ panel: "services", oauth: ["conflict", "connected"], next: "https://example.invalid" }, "/integrations?tab=services"],
])("redirects saved editor link %j to its owner page", async (search, destination) => {
  await expect(MaxWorkspacePage({ params, searchParams: Promise.resolve(search) }))
    .rejects.toThrow(`redirect:/max/owned${destination}`);
  expect(mocks.project).toHaveBeenCalledWith("/api/projects/owned");
});

it("never redirects a legacy link before owner admission", async () => {
  mocks.project.mockResolvedValueOnce({ ok: false, status: 403 });
  await expect(MaxWorkspacePage({ params, searchParams: Promise.resolve({ panel: "publish" }) }))
    .rejects.toThrow(/^redirect:\/max$/);
});

it("keeps normal chat and starter draft entry in the editor", async () => {
  const page = await MaxWorkspacePage({ params, searchParams: Promise.resolve({ starter: "1", panel: ["max", "services"] }) });
  expect(page?.props.project).toBe(project);
});

it.each([[MaxPublishPage, "publish"], [MaxIntegrationsPage, "integrations"]] as const)("renders its owner page only after checking access", async (Page, active) => {
  const page = await Page({ params });
  expect(page.props).toMatchObject({ active, projectId: project.id });
  const denied = new Error("Access denied");
  mocks.load.mockRejectedValueOnce(denied);
  await expect(Page({ params })).rejects.toBe(denied);
});
