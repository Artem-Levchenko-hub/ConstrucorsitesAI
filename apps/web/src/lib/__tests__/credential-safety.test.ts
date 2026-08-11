import { describe, expect, it } from "vitest";

import { redactCredentialsBeforeTransport } from "../credential-safety";

describe("redactCredentialsBeforeTransport", () => {
  it("removes a provider key before MAX chat sends the product request", () => {
    const key = `sk-${"a".repeat(24)}`;
    const result = redactCredentialsBeforeTransport(
      `Собери AI-тренера и подключи ${key}`,
    );

    expect(result.credentialsRemoved).toBe(true);
    expect(result.text).toBe(
      "Собери AI-тренера и подключи [CREDENTIAL REDACTED]",
    );
    expect(result.text).not.toContain(key);
  });

  it("leaves an ordinary AI brief unchanged", () => {
    const text = "Собери AI-тренера через управляемый runtime";
    expect(redactCredentialsBeforeTransport(text)).toEqual({
      text,
      credentialsRemoved: false,
    });
  });
});
