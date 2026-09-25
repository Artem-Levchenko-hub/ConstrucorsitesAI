import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OAuthButtons } from "@/components/auth/OAuthButtons";

/**
 * Кнопки провайдеров: рисуются только для настроенных, клик просит у api
 * ссылку и переводит браузер на провайдера; отказ api виден как текст, а не
 * как зависшая кнопка.
 */
const providers = [
  { provider: "vk" as const, label: "VK ID" },
  { provider: "yandex" as const, label: "Яндекс ID" },
];

let root: Root | null = null;
let container: HTMLDivElement;
let requests: string[];
let respond: (url: string) => Response;

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  requests = [];
  respond = () => Response.json({ authorization_url: "https://id.vk.com/authorize?state=s" });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      requests.push(url);
      return respond(url);
    }),
  );
  container = document.createElement("div");
  document.body.append(container);
});

afterEach(async () => {
  if (root) await act(async () => root!.unmount());
  root = null;
  container.remove();
  vi.unstubAllGlobals();
});

async function mount(element: React.ReactElement) {
  root = createRoot(container);
  await act(async () => root!.render(element));
}

const buttons = () => [...container.querySelectorAll<HTMLButtonElement>("button[data-oauth-provider]")];
const click = async (provider: string) => {
  await act(async () => {
    buttons().find((b) => b.dataset.oauthProvider === provider)!.click();
  });
};
const settle = async (check: () => void) => {
  await act(async () => {
    await vi.waitFor(check);
  });
};

describe("OAuthButtons", () => {
  it("renders nothing when no provider is configured", async () => {
    await mount(<OAuthButtons providers={[]} />);
    expect(container.innerHTML).toBe("");
  });

  it("renders one button per configured provider, labelled «Войти через …»", async () => {
    await mount(<OAuthButtons providers={providers} />);
    expect(buttons().map((b) => b.textContent)).toEqual([
      expect.stringContaining("Войти через VK ID"),
      expect.stringContaining("Войти через Яндекс ID"),
    ]);
    // Обещание «берём только email» переехало на экран согласия — туда, где
    // человек его принимает; под кнопками оно было лишним текстом.
    expect(container.textContent).not.toContain("только email");
  });

  it("asks the api for the provider link and sends the browser there", async () => {
    const navigate = vi.fn();
    await mount(<OAuthButtons providers={providers} next="/max/onboarding" navigate={navigate} />);
    await click("vk");
    await settle(() => expect(navigate).toHaveBeenCalledWith("https://id.vk.com/authorize?state=s"));
    expect(requests).toEqual(["/api/auth/oauth/vk/start?next=%2Fmax%2Fonboarding"]);
    // Пока идём к провайдеру, второй клик невозможен.
    expect(buttons().every((b) => b.disabled)).toBe(true);
  });

  it("shows the api refusal and re-enables the buttons", async () => {
    respond = () =>
      new Response(
        JSON.stringify({ error: { code: "oauth_provider_unavailable", message: "Вход через этого провайдера не настроен" } }),
        { status: 404, headers: { "content-type": "application/json" } },
      );
    const navigate = vi.fn();
    await mount(<OAuthButtons providers={providers} navigate={navigate} />);
    await click("yandex");
    await settle(() =>
      expect(container.querySelector('[role="alert"]')?.textContent).toBe(
        "Вход через этого провайдера не настроен",
      ),
    );
    expect(navigate).not.toHaveBeenCalled();
    expect(requests).toEqual(["/api/auth/oauth/yandex/start"]);
    expect(buttons().every((b) => !b.disabled)).toBe(true);
  });
});
