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
    expect(safe).toContain("ключ сохранён в Yleum");
  });

  it.each([
    "requestToken===detailsSeq.current.",
    "const opToken=++operationCounter.current;",
    "requestToken !== detailsSeq.current;",
    "token === detailsSeq.current;",
    "token: ++operationCounter.current;",
    "token = detailsSeq.current;",
    "api_key = process.env.PROVIDER_API_KEY;",
    "token: import.meta.env.PROVIDER_TOKEN;",
    "my_token: operationCounter.current;",
    "мойтокен: operationCounter.current;",
  ])("admits public code without redaction or provider intake: %s", (code) => {
    expect(containsChatSecret(code)).toBe(false);
    expect(resolveChatCredential(code, [aitunnel])).toEqual({ kind: "none" });
    expect(redactChatSecrets(code)).toBe(code);
  });

  it.each(["token:", "TOKEN =", "api-key:", "api_key =", "ключ —", "токен это"])(
    "still protects a standalone labelled opaque credential: %s", (label) => {
      const secret = "synthetic_opaque_credential_123456";
      const prompt = `AITUNNEL ${label} \"${secret}\"`;
      const result = resolveChatCredential(prompt, [aitunnel]);
      expect(containsChatSecret(prompt)).toBe(true);
      expect(result.kind).toBe("match");
      if (result.kind !== "match") return;
      expect(result.value.secret).toBe(secret);
      expect(result.value.safePrompt).not.toContain(secret);
      expect(redactChatSecrets(prompt)).not.toContain(secret);
    },
  );

  it("keeps real key detection inside code and identifier assignments", () => {
    const secret = `sk-${"d".repeat(24)}`;
    const code = `const opToken=\"${secret}\"; requestToken===detailsSeq.current.`;
    expect(containsChatSecret(code)).toBe(true);
    expect(resolveChatCredential(code, [aitunnel])).toEqual({ kind: "needs_provider" });
    expect(redactChatSecrets(code)).not.toContain(secret);
    expect(redactChatSecrets(code)).toContain("requestToken===detailsSeq.current.");
  });

  it.each([
    "synthetic_jwt_header.synthetic_jwt_payload.synthetic_jwt_signature",
    "synthetic_base64_1234567890+/=",
    "detailsSeq.current",
    "process.env.PROVIDER_API_KEY",
  ])("does not exempt a quoted credential literal: %s", (secret) => {
    const prompt = `token: \"${secret}\"`;
    expect(containsChatSecret(prompt)).toBe(true);
    expect(resolveChatCredential(prompt, [aitunnel])).toEqual({ kind: "needs_provider" });
    expect(redactChatSecrets(prompt)).not.toContain(secret);
  });

  it.each([
    "++ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
    "!synthetic_opaque_credential_123456",
    "--synthetic_opaque_credential_123456",
    "process.env.NOT_A_REFERENCE/opaque_suffix",
  ])("protects opaque unquoted credentials even with operator-like prefixes: %s", (secret) => {
    const prompt = `token: ${secret}`;
    expect(containsChatSecret(prompt)).toBe(true);
    expect(resolveChatCredential(prompt, [aitunnel])).toEqual({ kind: "needs_provider" });
    expect(redactChatSecrets(prompt)).not.toContain(secret);
  });
});
