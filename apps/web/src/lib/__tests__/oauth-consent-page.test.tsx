import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

/**
 * Экран подтверждения документов после входа через провайдера: показывает,
 * через кого и под каким email создаётся аккаунт, требует те же три согласия,
 * что и обычная регистрация, и честно объясняет устаревший билет.
 */
const pending = vi.hoisted(() => ({ value: null as unknown }));
vi.mock("@/lib/oauth-login-server", () => ({
  getOAuthPending: async (ticket: string) => (ticket ? pending.value : null),
}));
vi.mock("@/app/(auth)/actions", () => ({ oauthCompleteAction: async () => ({ error: null }) }));

afterEach(() => {
  pending.value = null;
});

async function render(ticket?: string): Promise<string> {
  const { default: OAuthCompletePage } = await import("@/app/(auth)/oauth/complete/page");
  return renderToStaticMarkup(await OAuthCompletePage({ searchParams: Promise.resolve({ ticket }) }));
}

describe("/oauth/complete", () => {
  it("asks for the same three consents before creating the account", async () => {
    pending.value = {
      provider: "vk",
      label: "VK ID",
      email: "owner@example.com",
      next: "/max/onboarding",
      legal_document_version: "2026-07-30",
    };
    const html = await render("ticket-1");
    expect(html).toContain("Вход через VK ID");
    expect(html).toContain("owner@example.com");
    const checkbox = (name: string) => html.match(new RegExp(`<input[^>]*name="${name}"[^>]*>`))?.[0];
    for (const name of ["terms_accepted", "privacy_accepted", "personal_data_accepted"]) {
      expect(checkbox(name)).toContain('type="checkbox"');
      expect(checkbox(name)).toContain("required");
    }
    expect(checkbox("marketing_accepted")).toContain('type="checkbox"');
    expect(checkbox("marketing_accepted")).not.toContain("required");
    for (const href of ["/legal/terms", "/legal/privacy", "/legal/personal-data"]) {
      expect(html).toContain(`href="${href}"`);
    }
    expect(html).toContain('name="ticket" value="ticket-1"');
    expect(html).toContain('name="next" value="/max/onboarding"');
    expect(html).toContain('name="document_version" value="2026-07-30"');
    expect(html).toContain("Создать аккаунт");
    // Обещание о минимуме данных проверяется здесь: под кнопками входа его
    // больше нет, а человек принимает его именно на этом экране.
    expect(html).toContain("храним только email");
  });

  it("explains an expired or used ticket and offers the usual ways in", async () => {
    for (const ticket of [undefined, "stale"]) {
      const html = await render(ticket);
      expect(html).toContain("Ссылка устарела");
      expect(html).toContain('href="/login"');
      expect(html).toContain('href="/max/register"');
      expect(html).not.toContain("terms_accepted");
    }
  });
});
