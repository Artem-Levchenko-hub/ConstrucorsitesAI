import type { IntegrationField, IntegrationProvider } from "@/lib/api/types";

const SECRET_PATTERNS = [
  /\bsk-aitunnel-[A-Za-z0-9_-]{12,}\b/g,
  /\bsk-[A-Za-z0-9_-]{16,}\b/g,
  /\bAIza[0-9A-Za-z_-]{30,}\b/g,
  /\bgh[pousr]_[A-Za-z0-9]{20,}\b/g,
  /\bxox[baprs]-[0-9A-Za-z-]{10,}\b/g,
] as const;

const PROVIDER_ALIASES: Record<string, string[]> = {
  aitunnel: ["aitunnel", "ai tunnel", "аи туннель", "ай туннель", "аитуннель"],
  bitrix24: ["bitrix24", "битрикс24", "битрикс 24"],
  iiko: ["iiko", "iikocloud", "айко"],
  moysklad: ["moysklad", "мойсклад", "мой склад"],
  yandex_metrica: ["yandex metrica", "яндекс метрика", "метрика"],
  yookassa: ["yookassa", "юkassa", "юкасса", "ю касса"],
};

export type ChatCredentialMatch = {
  provider: IntegrationProvider;
  secretField: IntegrationField;
  secret: string;
  safePrompt: string;
};

export type ChatCredentialResolution =
  | { kind: "none" }
  | { kind: "needs_provider" }
  | {
      kind: "needs_fields";
      provider: IntegrationProvider;
      labels: string[];
    }
  | { kind: "match"; value: ChatCredentialMatch };

function normalized(value: string): string {
  return value
    .toLocaleLowerCase("ru-RU")
    .replaceAll("ё", "е")
    .replace(/[^a-zа-я0-9]+/g, " ")
    .trim();
}

function mentionedProvider(
  text: string,
  providers: IntegrationProvider[],
): IntegrationProvider | null {
  const source = ` ${normalized(text)} `;
  const matches = providers.filter((provider) => {
    const aliases = [
      provider.key,
      provider.name,
      ...(PROVIDER_ALIASES[provider.key] ?? []),
    ];
    return aliases.some((alias) => {
      const candidate = normalized(alias);
      return candidate.length >= 3 && source.includes(` ${candidate} `);
    });
  });
  return matches.length === 1 ? matches[0] : null;
}

function highConfidenceSecrets(text: string): string[] {
  const matches = SECRET_PATTERNS.flatMap((pattern) => [
    ...text.matchAll(new RegExp(pattern.source, pattern.flags)),
  ]).map((match) => match[0]);
  return [...new Set(matches)];
}

function keywordSecret(text: string): string | null {
  const match = text.match(
    /(?:api[\s_-]*key|ключ|token|токен)\s*(?:[:=—–-]|это)?\s*["'`]?([^\s"'`,;]{16,})/iu,
  );
  const candidate = match?.[1]?.trim() ?? "";
  if (!candidate || /^https?:\/\//i.test(candidate)) return null;
  return candidate;
}

export function containsChatSecret(text: string): boolean {
  return highConfidenceSecrets(text).length > 0 || keywordSecret(text) !== null;
}

export function redactChatSecrets(text: string): string {
  let safe = text;
  for (const pattern of SECRET_PATTERNS) {
    safe = safe.replace(
      new RegExp(pattern.source, pattern.flags),
      "[ключ сохранён в Omnia]",
    );
  }
  const keyword = keywordSecret(safe);
  if (keyword) safe = safe.replaceAll(keyword, "[ключ сохранён в Omnia]");
  return safe.trim();
}

export function resolveChatCredential(
  text: string,
  providers: IntegrationProvider[],
  promptText: string = text,
): ChatCredentialResolution {
  const highConfidence = highConfidenceSecrets(text);
  const fallback = keywordSecret(text);
  const secrets = highConfidence.length
    ? highConfidence
    : fallback
      ? [fallback]
      : [];
  if (secrets.length === 0) return { kind: "none" };

  const provider = mentionedProvider(text, providers);
  if (!provider || secrets.length !== 1) return { kind: "needs_provider" };

  const secretFields = provider.fields.filter(
    (field) => field.secret && field.required,
  );
  const missingPublic = provider.fields.filter(
    (field) => !field.secret && field.required,
  );
  if (
    !provider.available ||
    provider.connection_mode !== "credentials" ||
    secretFields.length !== 1 ||
    missingPublic.length > 0
  ) {
    return {
      kind: "needs_fields",
      provider,
      labels: missingPublic.map((field) => field.label),
    };
  }

  const safeSource = redactChatSecrets(promptText).replaceAll(
    secrets[0],
    "[ключ сохранён в Omnia]",
  );
  const integrationHint =
    provider.key === "aitunnel"
      ? "Для ИИ-функций используй requestOmniaAI из @/lib/omnia/integration-client."
      : "Используй только управляемый integration runtime Omnia.";
  const safePrompt = [
    safeSource,
    `Интеграция ${provider.name} уже проверена и подключена через защищённое хранилище Omnia.`,
    "Не запрашивай ключ, не записывай его в код и не создавай .env-файл.",
    integrationHint,
    `Публичная документация провайдера: ${provider.docs_url}`,
  ]
    .filter(Boolean)
    .join("\n\n");

  return {
    kind: "match",
    value: {
      provider,
      secretField: secretFields[0],
      secret: secrets[0],
      safePrompt,
    },
  };
}
