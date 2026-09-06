import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import PricingPage from "@/app/pricing/page";
import RegisterPage from "@/app/(auth)/register/page";
import { RegisterForm } from "@/components/auth/RegisterForm";

vi.mock("next-intl/server", () => ({
  getTranslations: vi.fn(async () => (key: string) => key),
}));

describe("public MAX routing", () => {
  it("sends public pricing through login with an explicit safe return", () => {
    const html = renderToStaticMarkup(<PricingPage />);

    expect(html).toContain('href="/login?next=/billing/plan"');
    expect(html).not.toContain('href="/billing/plan"');
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
