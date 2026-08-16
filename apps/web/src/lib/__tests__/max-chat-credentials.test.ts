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
  it("extracts an AITUNNEL key but sends only a redacted instruction onward", () => {
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
    expect(result.value.safePrompt).toContain("integration runtime");
    expect(result.value.safePrompt).toContain(aitunnel.docs_url);
  });

  it("blocks a generic secret when the provider name is absent", () => {
    const secret = `sk-${"b".repeat(24)}`;
    expect(resolveChatCredential(`Вот ключ ${secret}`, [aitunnel])).toEqual({
      kind: "needs_provider",
    });
  });

  it("does not classify environment variable names as pasted credentials", () => {
    expect(containsChatSecret("Используй process.env.AITUNNEL_API_KEY")).toBe(false);
  });

  it("redacts high-confidence keys completely", () => {
    const secret = `sk-aitunnel-${"c".repeat(24)}`;
    const safe = redactChatSecrets(`AITUNNEL: ${secret}`);
    expect(safe).not.toContain(secret);
    expect(safe).toContain("ключ сохранён в Omnia");
  });
});
