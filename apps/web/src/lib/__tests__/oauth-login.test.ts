import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { startOAuthLogin } from "@/lib/api/oauth-login";
import {
  isOAuthProviderKey,
  normalizeOAuthProviders,
  oauthButtonLabel,
  oauthErrorMessage,
} from "@/lib/oauth-login";
import { getOAuthPending, listOAuthProviders } from "@/lib/oauth-login-server";

/**
 * Вход через VK ID / Яндекс ID: кнопки рисуются только для настроенных
 * провайдеров, любой сбой api = кнопок нет (а не сломанная страница входа),
 * билет подтверждения читается с api, а не из адресной строки.
 */

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });

/** Строгий fetch: любой URL вне ожидания — ошибка теста. */
function expectFetch(expected: string, response: Response | Error) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    if (url !== expected) throw new Error(`unexpected fetch ${url} (init: ${JSON.stringify(init)})`);
    if (response instanceof Error) throw response;
    return response;
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

beforeEach(() => {
  vi.stubEnv("NEXT_PUBLIC_USE_MOCKS", "false");
  vi.stubEnv("INTERNAL_API_URL", "http://api.internal:8000");
  vi.stubEnv("NEXT_PUBLIC_API_URL", "");
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("provider helpers", () => {
  it("recognises exactly the two providers", () => {
    expect(isOAuthProviderKey("vk")).toBe(true);
    expect(isOAuthProviderKey("yandex")).toBe(true);
    expect(isOAuthProviderKey("github")).toBe(false);
    expect(isOAuthProviderKey(undefined)).toBe(false);
  });

  it("keeps only well-formed, unique provider entries", () => {
    expect(
      normalizeOAuthProviders([
        { provider: "vk", label: "VK ID" },
        { provider: "vk", label: "дубль" },
        { provider: "github", label: "GitHub" },
        { provider: "yandex", label: "  " },
        { provider: "yandex", label: " Яндекс ID " },
        "junk",
        null,
      ]),
    ).toEqual([
      { provider: "vk", label: "VK ID" },
      { provider: "yandex", label: "Яндекс ID" },
    ]);
    expect(normalizeOAuthProviders(undefined)).toEqual([]);
  });

  it("labels the button and explains callback errors in plain words", () => {
    expect(oauthButtonLabel({ provider: "vk", label: "VK ID" })).toBe("Войти через VK ID");
    expect(oauthErrorMessage("oauth_cancelled")).toContain("отменён");
    expect(oauthErrorMessage("oauth_email_required")).toContain("email");
    expect(oauthErrorMessage("account_unavailable")).toContain("недоступен");
    expect(oauthErrorMessage("something_new")).toContain("Не удалось войти");
    expect(oauthErrorMessage(undefined)).toBeNull();
    expect(oauthErrorMessage("")).toBeNull();
  });
});

describe("listOAuthProviders", () => {
  it("asks the internal api and returns the configured providers", async () => {
    const fetchMock = expectFetch(
      "http://api.internal:8000/api/auth/oauth/providers",
      json(200, {
        providers: [{ provider: "yandex", label: "Яндекс ID" }],
        legal_document_version: "2026-07-30",
      }),
    );
    await expect(listOAuthProviders()).resolves.toEqual([
      { provider: "yandex", label: "Яндекс ID" },
    ]);
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ cache: "no-store" });
  });

  it.each([
    ["api error", json(503, { error: { code: "internal_error", message: "down" } })],
    ["network failure", new Error("ECONNREFUSED")],
    ["garbage body", json(200, { providers: "vk" })],
  ])("hides the buttons on %s", async (_name, response) => {
    expectFetch("http://api.internal:8000/api/auth/oauth/providers", response);
    await expect(listOAuthProviders()).resolves.toEqual([]);
  });

  it("never calls the api in mock mode", async () => {
    vi.stubEnv("NEXT_PUBLIC_USE_MOCKS", "true");
    const fetchMock = expectFetch("never", json(200, {}));
    await expect(listOAuthProviders()).resolves.toEqual([]);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("getOAuthPending", () => {
  it("reads the consent screen data by ticket", async () => {
    expectFetch(
      "http://api.internal:8000/api/auth/oauth/pending?ticket=t%2Fabc%3D%3D",
      json(200, {
        provider: "vk",
        label: "VK ID",
        email: "owner@example.com",
        next: "/max/onboarding",
        legal_document_version: "2026-07-30",
      }),
    );
    await expect(getOAuthPending("t/abc==")).resolves.toEqual({
      provider: "vk",
      label: "VK ID",
      email: "owner@example.com",
      next: "/max/onboarding",
      legal_document_version: "2026-07-30",
    });
  });

  it("falls back to the cabinet when the destination is not a same-origin path", async () => {
    expectFetch(
      "http://api.internal:8000/api/auth/oauth/pending?ticket=t",
      json(200, {
        provider: "yandex",
        label: "Яндекс ID",
        email: "owner@example.com",
        next: "https://evil.example",
        legal_document_version: "2026-07-30",
      }),
    );
    await expect(getOAuthPending("t")).resolves.toMatchObject({ next: "/max" });
  });

  it.each([
    ["an expired ticket", json(400, { error: { code: "oauth_ticket_invalid", message: "x" } })],
    ["a body without email", json(200, { provider: "vk", label: "VK ID", legal_document_version: "v" })],
    ["a network failure", new Error("offline")],
  ])("returns null for %s", async (_name, response) => {
    expectFetch("http://api.internal:8000/api/auth/oauth/pending?ticket=t", response);
    await expect(getOAuthPending("t")).resolves.toBeNull();
  });

  it("does not call the api without a ticket", async () => {
    const fetchMock = expectFetch("never", json(200, {}));
    await expect(getOAuthPending("")).resolves.toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("startOAuthLogin", () => {
  it("requests the authorization url with the same-origin destination", async () => {
    const fetchMock = expectFetch(
      "/api/auth/oauth/vk/start?next=%2Fmax%2Fonboarding",
      json(200, { authorization_url: "https://id.vk.com/authorize?state=s" }),
    );
    await expect(startOAuthLogin("vk", "/max/onboarding")).resolves.toEqual({
      authorization_url: "https://id.vk.com/authorize?state=s",
    });
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ credentials: "include" });
  });

  it("omits the destination when none was requested", async () => {
    expectFetch("/api/auth/oauth/yandex/start", json(200, { authorization_url: "https://oauth.yandex.ru/authorize" }));
    await expect(startOAuthLogin("yandex")).resolves.toEqual({
      authorization_url: "https://oauth.yandex.ru/authorize",
    });
  });

  it("surfaces the api refusal as an ApiError with its code", async () => {
    expectFetch(
      "/api/auth/oauth/vk/start",
      json(404, { error: { code: "oauth_provider_unavailable", message: "не настроен" } }),
    );
    await expect(startOAuthLogin("vk")).rejects.toMatchObject({
      code: "oauth_provider_unavailable",
      status: 404,
    });
  });
});
