import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Завершение входа через VK ID / Яндекс ID для нового аккаунта: аккаунт
 * создаётся только после трёх обязательных согласий, api получает ровно то,
 * что нужно (билет + согласия + версия документов), cookie-сессия переносится
 * в браузер, а дальше пользователь попадает на запрошенную страницу.
 */
const redirect = vi.fn((target: string) => {
  throw new Error(`redirect:${target}`);
});
const cookieSet = vi.fn();
vi.mock("next/navigation", () => ({ redirect }));
vi.mock("next/headers", () => ({ cookies: async () => ({ set: cookieSet }) }));

function form(fields: Record<string, string>): FormData {
  const data = new FormData();
  for (const [key, value] of Object.entries(fields)) data.set(key, value);
  return data;
}

const consent = {
  ticket: "ticket-1234567890abcdef",
  terms_accepted: "on",
  privacy_accepted: "on",
  personal_data_accepted: "on",
  document_version: "2026-07-30",
  next: "/max/onboarding",
};

let requests: { url: string; body: Record<string, unknown> }[];
let respond: () => Response;

beforeEach(() => {
  requests = [];
  respond = () =>
    new Response(JSON.stringify({ id: "u1", email: "owner@example.com" }), {
      status: 201,
      headers: {
        "content-type": "application/json",
        "set-cookie": "omnia_session=jwt-token; Path=/; HttpOnly",
      },
    });
  vi.stubEnv("INTERNAL_API_URL", "http://api.internal:8000");
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      requests.push({ url, body: JSON.parse(String(init?.body)) });
      return respond();
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  redirect.mockClear();
  cookieSet.mockReset();
});

describe("oauthCompleteAction", () => {
  it("creates the account with the exact consent payload and lands on the destination", async () => {
    const { oauthCompleteAction } = await import("@/app/(auth)/actions");
    await expect(
      oauthCompleteAction({ error: null }, form({ ...consent, marketing_accepted: "on" })),
    ).rejects.toThrow("redirect:/max/onboarding");
    expect(requests).toEqual([
      {
        url: "http://api.internal:8000/api/auth/oauth/complete",
        body: {
          ticket: "ticket-1234567890abcdef",
          terms_accepted: true,
          privacy_accepted: true,
          personal_data_accepted: true,
          marketing_accepted: true,
          document_version: "2026-07-30",
        },
      },
    ]);
    expect(cookieSet).toHaveBeenCalledWith(
      expect.objectContaining({ name: "omnia_session", value: "jwt-token", httpOnly: true }),
    );
  });

  it("defaults to the MAX cabinet and rejects off-site destinations", async () => {
    const { oauthCompleteAction } = await import("@/app/(auth)/actions");
    const { next: _next, ...withoutNext } = consent;
    await expect(oauthCompleteAction({ error: null }, form(withoutNext))).rejects.toThrow(
      "redirect:/max",
    );
    await expect(
      oauthCompleteAction({ error: null }, form({ ...consent, next: "//evil.example/x" })),
    ).rejects.toThrow("redirect:/max");
    expect(requests.every((request) => request.body.marketing_accepted === false)).toBe(true);
  });

  it("refuses without the three mandatory consents and never calls the api", async () => {
    const { oauthCompleteAction } = await import("@/app/(auth)/actions");
    const { personal_data_accepted: _personal, ...partial } = consent;
    await expect(oauthCompleteAction({ error: null }, form(partial))).resolves.toEqual({
      error: "Подтвердите обязательные условия",
    });
    expect(requests).toEqual([]);
    expect(redirect).not.toHaveBeenCalled();
  });

  it("refuses without a ticket", async () => {
    const { oauthCompleteAction } = await import("@/app/(auth)/actions");
    const { ticket: _ticket, ...noTicket } = consent;
    const result = await oauthCompleteAction({ error: null }, form(noTicket));
    expect(result.error).toContain("устарела");
    expect(requests).toEqual([]);
  });

  it("shows the api refusal instead of pretending the account exists", async () => {
    respond = () =>
      new Response(
        JSON.stringify({ error: { code: "oauth_ticket_invalid", message: "Ссылка на подтверждение устарела" } }),
        { status: 400, headers: { "content-type": "application/json" } },
      );
    const { oauthCompleteAction } = await import("@/app/(auth)/actions");
    await expect(oauthCompleteAction({ error: null }, form(consent))).resolves.toEqual({
      error: "Ссылка на подтверждение устарела",
    });
    expect(cookieSet).not.toHaveBeenCalled();
    expect(redirect).not.toHaveBeenCalled();
  });

  it("fails loudly when the api answered without a session cookie", async () => {
    respond = () =>
      new Response(JSON.stringify({ id: "u1" }), {
        status: 201,
        headers: { "content-type": "application/json" },
      });
    const { oauthCompleteAction } = await import("@/app/(auth)/actions");
    const result = await oauthCompleteAction({ error: null }, form(consent));
    expect(result.error).toContain("cookie");
    expect(redirect).not.toHaveBeenCalled();
  });
});
