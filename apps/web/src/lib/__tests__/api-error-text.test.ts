import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api/client";
import { describeApiError, describeEntitlementError } from "@/lib/api/errors";

/**
 * Plan refusals come with structured details; the owner must read which limit
 * is full, how much is used and where to change the plan — never a bare code.
 */
const error = (code: ApiError["code"], details?: Record<string, unknown>, message = "server text") =>
  new ApiError(402, { code, message, details });

describe("describeApiError", () => {
  it("spells out a used-up numeric plan limit with the plan and the numbers", () => {
    const text = describeApiError(
      error("entitlement_exceeded", { entitlement: "max_projects", limit: 3, used: 3, plan_code: "pro" }),
    );
    expect(text).toBe("На тарифе Pro доступно не больше 3 приложений, сейчас занято 3. Сменить тариф можно в разделе «Аккаунт → Тариф».");
  });

  it("uses the singular for a limit of one and names zero as not included", () => {
    expect(describeEntitlementError("entitlement_exceeded", { entitlement: "static_publish_slots", limit: 1, used: 1, plan_code: "pro" }, ""))
      .toContain("не больше 1 опубликованное приложение");
    expect(describeEntitlementError("entitlement_exceeded", { entitlement: "static_publish_slots", limit: 0, used: 0, plan_code: "free" }, ""))
      .toBe("Тариф Free не включает: опубликованных приложений. Сменить тариф можно в разделе «Аккаунт → Тариф».");
  });

  it("explains a feature the plan does not include", () => {
    expect(describeApiError(error("subscription_entitlement_required", { entitlement: "integrations", plan_code: "free" })))
      .toBe("Тариф Free не включает: интеграций. Сменить тариф можно в разделе «Аккаунт → Тариф».");
  });

  it("falls back to the server text plus the plan hint for an unknown entitlement", () => {
    expect(describeApiError(error("entitlement_exceeded", { entitlement: "mystery", limit: 2, used: 2 }, "Лимит исчерпан")))
      .toBe("Лимит исчерпан Сменить тариф можно в разделе «Аккаунт → Тариф».");
    expect(describeApiError(error("subscription_entitlement_required", undefined, "")))
      .toBe("Эта возможность не входит в тариф текущем тарифе. Сменить тариф можно в разделе «Аккаунт → Тариф».");
  });

  it("passes every other API answer through and covers non-API failures", () => {
    expect(describeApiError(error("conflict", undefined, "Сначала подключите MAX-бота"))).toBe("Сначала подключите MAX-бота");
    expect(describeApiError(new TypeError("Failed to fetch"))).toBe("Сервис временно недоступен. Проверьте соединение и повторите попытку.");
    expect(describeApiError(new Error("boom"))).toBe("boom");
    expect(describeApiError("???", "Попробуйте ещё раз.")).toBe("Попробуйте ещё раз.");
  });
});
