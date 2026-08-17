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
  it("extracts one AITUNNEL key and creates only a redacted agent prompt", () => {
    const secret = `sk-aitunnel-${"a".repeat(24)}`;
    const result = resolveChatCredential(
      `Подключи AITUNNEL, ключ ${secret}, и сделай AI-тренера`,
      [aitunnel],
    );

    expect(result.kind).toBe("match");
    if (result.kind !== "match") return;
    expect(result.value.secret).toBe(secret);
    expect(result.value.secretField.key).toBe("api_key");
    expect(result.value.safePrompt).not.toContain(secret);
    expect(result.value.safePrompt).toContain("requestOmniaAI");
    expect(result.value.safePrompt).toContain(aitunnel.docs_url);
  });

  it("keeps a secret out of generation when provider is unknown", () => {
    const secret = `sk-${"b".repeat(24)}`;
    expect(resolveChatCredential(`Вот ключ ${secret}`, [aitunnel])).toEqual({
      kind: "needs_provider",
    });
  });

  it("recognises a labelled nonstandard provider token", () => {
    expect(containsChatSecret("AITUNNEL API key: provider_token_1234567890")).toBe(
      true,
    );
  });

  it("does not classify an environment variable name as a pasted credential", () => {
    expect(containsChatSecret("Используй process.env.AITUNNEL_API_KEY")).toBe(false);
    expect(containsChatSecret("ключевая_характеристика продукта")).toBe(false);
  });

  it("redacts high-confidence and labelled values completely", () => {
    const secret = `sk-aitunnel-${"c".repeat(24)}`;
    const labelled = "provider_token_1234567890";
    const safe = redactChatSecrets(
      `AITUNNEL: ${secret}; резервный ключ ${labelled}`,
    );
    expect(safe).not.toContain(secret);
    expect(safe).not.toContain(labelled);
    expect(safe).toContain("ключ сохранён в Omnia");
  });

  it("redacts every labelled secret and refuses ambiguous multi-key intake", () => {
    const first = "provider_token_1234567890";
    const second = "backup_token_0987654321";
    const text = `AITUNNEL ключ ${first}; резервный token: ${second}`;
    const safe = redactChatSecrets(text);

    expect(safe).not.toContain(first);
    expect(safe).not.toContain(second);
    expect(resolveChatCredential(text, [aitunnel])).toEqual({
      kind: "needs_provider",
    });
  });
});
