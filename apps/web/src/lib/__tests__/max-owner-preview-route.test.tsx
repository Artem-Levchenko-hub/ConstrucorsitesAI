import { afterEach, describe, expect, it, vi } from "vitest";
import type { Project } from "@/lib/api/types";
import MaxPreviewPage, { metadata } from "@/app/(app)/max/[id]/preview/page";
import { MaxOwnerPreviewPage } from "@/components/max/MaxOwnerPreviewPage";

const mocks = vi.hoisted(() => ({ load: vi.fn(), session: vi.fn(), target: "" }));
vi.mock("@/lib/max-project-server", () => ({ loadMaxProject: mocks.load }));
vi.mock("@/lib/auth-mock", () => ({ getSession: mocks.session }));
vi.mock("next/headers", () => ({ headers: async () => new Headers({ "x-omnia-return-to": mocks.target }) }));
vi.mock("@/components/max/MaxOwnerPreviewPage", () => ({ MaxOwnerPreviewPage: () => null }));
const project = { id: "owner-project", template: "max_miniapp" } as Project;
afterEach(() => { vi.resetAllMocks(); mocks.target = ""; });

describe("stable owner preview route", () => {
  it("admits the owner using the exact stable return path before rendering the parent shell", async () => {
    mocks.session.mockResolvedValue({ email: "owner@example.invalid" });
    mocks.load.mockResolvedValue(project);
    const result = await MaxPreviewPage({ params: Promise.resolve({ id: project.id }) });
    expect(mocks.load).toHaveBeenCalledWith(project.id, `/max/${project.id}/preview`);
    expect(result.type).toBe(MaxOwnerPreviewPage);
    expect(result.props).toEqual({ project });
    expect(metadata.robots).toEqual({ index: false, follow: false });
  });

  it("preserves its own login return even if the page renders before its layout", async () => {
    mocks.session.mockResolvedValue(null);
    await expect(MaxPreviewPage({ params: Promise.resolve({ id: project.id }) })).rejects.toMatchObject({
      digest: expect.stringContaining(`;/login?next=${encodeURIComponent(`/max/${project.id}/preview`)};`),
    });
    expect(mocks.load).not.toHaveBeenCalled();
  });

  it.each([401, 403, 404, "non-max"])("propagates owner admission rejection %s without rendering", async (status) => {
    mocks.session.mockResolvedValue({ email: "owner@example.invalid" });
    const denied = new Error(`admission:${status}`);
    mocks.load.mockRejectedValue(denied);
    await expect(MaxPreviewPage({ params: Promise.resolve({ id: project.id }) })).rejects.toBe(denied);
  });

  it.each([false, true])("preserves exact matching middleware query in page admission, signed in=%s", async signedIn => {
    mocks.target = `/max/${project.id}/preview?mode=phone&filter=a%2Fb`;
    mocks.session.mockResolvedValue(signedIn ? { email: "owner@example.invalid" } : null);
    mocks.load.mockResolvedValue(project);
    const result = MaxPreviewPage({ params: Promise.resolve({ id: project.id }) });
    if (signedIn) {
      await result;
      expect(mocks.load).toHaveBeenCalledWith(project.id, mocks.target);
    } else {
      await expect(result).rejects.toMatchObject({ digest: expect.stringContaining(`;/login?next=${encodeURIComponent(mocks.target)};`) });
      expect(mocks.load).not.toHaveBeenCalled();
    }
  });

  it.each(["https://evil.test/max/owner-project/preview", "/max/other/preview?private=x"])("ignores a return header for a different target: %s", async target => {
    mocks.target = target;
    mocks.session.mockResolvedValue({ email: "owner@example.invalid" });
    mocks.load.mockResolvedValue(project);
    await MaxPreviewPage({ params: Promise.resolve({ id: project.id }) });
    expect(mocks.load).toHaveBeenCalledWith(project.id, `/max/${project.id}/preview`);
  });
});
