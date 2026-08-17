import { describe, expect, it } from "vitest";

import type { IntegrationProvider } from "@/lib/api/types";
import {
  containsChatSecret,
  redactChatSecrets,
  resolveChatCredential,
} from "@/lib/max-chat-credentials";

const aitunnel: IntegrationProvider = {
  key: "aitunnel",
  name: "AITUNNEL",
  category: "ai",
  description: "ИИ",
  capabilities: ["ИИ-ответы"],
  fields: [
    {
      key: "api_key",
      label: "API-ключ",
      placeholder: "",
      help: "",
      secret: true,
      required: true,
    },
  ],
  available: true,
  recommended: true,
  requirement: null,
  docs_url: "https://docs.aitunnel.ru/",
  oauth_supported: false,
  oauth_available: false,
  connection_mode: "credentials",
};

describe("MAX chat credential intake", () => {
  it("extracts AITUNNEL key and creates a secretless docs-first agent prompt", () => {
    const secret = `sk-aitunnel-${"a".repeat(24)}`;
    const result = resolveChatCredential(
      `Подключи AITUNNEL, ключ ${secret}, и сделай AI-тренера`,
      [aitunnel],
    );

    expect(result.kind).toBe("match");
    if (result.kind !== "match") return;
    expect(result.value.secret).toBe(secret);
    expect(result.value.safePrompt).not.toContain(secret);
    expect(result.value.safePrompt).toContain("provider_docs");
    expect(result.value.safePrompt).toContain('provider: "aitunnel"');
    expect(result.value.safePrompt).toContain("requestOmniaAI");
  });

  it("does not send a key when provider is absent or ambiguous", () => {
    const secret = `sk-${"b".repeat(24)}`;
    expect(resolveChatCredential(`Вот ключ ${secret}`, [aitunnel])).toEqual({
      kind: "needs_provider",
    });
  });

  it("recognises labelled provider tokens but ignores env names", () => {
    expect(containsChatSecret("AITUNNEL API key: provider_token_1234567890")).toBe(true);
    expect(containsChatSecret("Используй process.env.AITUNNEL_API_KEY")).toBe(false);
  });

  it("redacts every detected credential", () => {
    const secret = `sk-aitunnel-${"c".repeat(24)}`;
    const labelled = "provider_token_1234567890";
    const safe = redactChatSecrets(`AITUNNEL: ${secret}; token: ${labelled}`);

    expect(safe).not.toContain(secret);
    expect(safe).not.toContain(labelled);
    expect(safe).toContain("ключ сохранён в Omnia");
  });
});
