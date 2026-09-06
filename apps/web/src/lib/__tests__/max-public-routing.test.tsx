import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

import PricingPage from "@/app/pricing/page";
import RegisterPage from "@/app/(auth)/register/page";
import { RegisterForm } from "@/components/auth/RegisterForm";

const { getSessionMock } = vi.hoisted(() => ({
  getSessionMock: vi.fn(),
}));

vi.mock("@/lib/auth-mock", () => ({ getSession: getSessionMock }));

vi.mock("next-intl/server", () => ({
  getTranslations: vi.fn(async () => (key: string) => key),
}));

describe("public MAX routing", () => {
  beforeEach(() => getSessionMock.mockReset());

  it("sends guest pricing through login with an explicit safe return", async () => {
    getSessionMock.mockResolvedValue(null);
    const html = renderToStaticMarkup(await PricingPage());

    expect(html).toContain('href="/login?next=/billing/plan"');
    expect(html).not.toContain('href="/billing/plan"');
  });

  it("opens account pricing directly for a valid session", async () => {
    getSessionMock.mockResolvedValue({
      id: "user-1",
      email: "owner@example.test",
      isAnon: false,
      emailVerifiedAt: "2026-09-07T00:00:00.000Z",
      status: "active",
    });
    const html = renderToStaticMarkup(await PricingPage());

    expect(html).toContain('href="/billing/plan"');
    expect(html).not.toContain('href="/login?next=/billing/plan"');
  });

  it("sends bare public registration to the consent-enforcing MAX flow", async () => {
    await expect(RegisterPage({ searchParams: Promise.resolve({}) })).rejects.toMatchObject({
      digest: expect.stringContaining("/max/register"),
    });
  });

  it("keeps explicit legacy return and provenance registrations intact", async () => {
    const page = await RegisterPage({
      searchParams: Promise.resolve({ next: "/projects/shared", source: "share", ref: "project-42" }),
    });
    const form = page.props.children as React.ReactElement<React.ComponentProps<typeof RegisterForm>>;

    expect(form.type).toBe(RegisterForm);
    expect(form.props).toMatchObject({
      next: "/projects/shared",
      source: "share",
      referrerProjectId: "project-42",
    });
  });
});
