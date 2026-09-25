import { ApiError } from "./client";

/**
 * Human-readable text for a failed request, in the words a Yleum owner
 * understands.
 *
 * The server already answers in Russian, so for most codes the message is
 * passed through. Plan refusals are the exception: `entitlement_exceeded`
 * (a numeric limit of the plan is used up) and
 * `subscription_entitlement_required` (the plan does not include a feature)
 * carry structured `details`, and the text is composed from them so the owner
 * sees which limit, how much is used and where to change the plan — whatever
 * wording the server chose.
 */
const ENTITLEMENT_NAMES: Record<string, { one: string; many: string }> = {
  max_projects: { one: "приложение", many: "приложений" },
  static_publish_slots: { one: "опубликованное приложение", many: "опубликованных приложений" },
  always_on_slots: { one: "постоянно работающее приложение", many: "постоянно работающих приложений" },
  team_seats: { one: "место в команде", many: "мест в команде" },
  custom_domains: { one: "свой домен", many: "своих доменов" },
  integrations: { one: "интеграция", many: "интеграций" },
};

const PLAN_HINT = "Сменить тариф можно в разделе «Аккаунт → Тариф».";

type EntitlementDetails = {
  entitlement?: unknown;
  limit?: unknown;
  used?: unknown;
  plan_code?: unknown;
};

function planLabel(code: unknown): string {
  switch (code) {
    case "free": return "Free";
    case "pro": return "Pro";
    case "business": return "Business";
    default: return typeof code === "string" && code ? code : "текущем тарифе";
  }
}

export function describeEntitlementError(
  code: "entitlement_exceeded" | "subscription_entitlement_required",
  details: EntitlementDetails | undefined,
  fallback: string,
): string {
  const key = typeof details?.entitlement === "string" ? details.entitlement : "";
  const names = ENTITLEMENT_NAMES[key];
  const plan = planLabel(details?.plan_code);
  if (code === "subscription_entitlement_required") {
    if (!names) return fallback ? `${fallback} ${PLAN_HINT}` : `Эта возможность не входит в тариф ${plan}. ${PLAN_HINT}`;
    return `Тариф ${plan} не включает: ${names.many}. ${PLAN_HINT}`;
  }
  const limit = typeof details?.limit === "number" ? details.limit : null;
  const used = typeof details?.used === "number" ? details.used : null;
  if (!names || limit === null) return fallback ? `${fallback} ${PLAN_HINT}` : `Лимит тарифа ${plan} исчерпан. ${PLAN_HINT}`;
  if (limit === 0) return `Тариф ${plan} не включает: ${names.many}. ${PLAN_HINT}`;
  const usage = used === null ? "" : `, сейчас занято ${used}`;
  return `На тарифе ${plan} доступно не больше ${limit} ${limit === 1 ? names.one : names.many}${usage}. ${PLAN_HINT}`;
}

/** Text for any thrown value; `fallback` covers non-API failures. */
export function describeApiError(error: unknown, fallback = "Попробуйте ещё раз."): string {
  if (error instanceof ApiError) {
    if (error.code === "entitlement_exceeded" || error.code === "subscription_entitlement_required") {
      return describeEntitlementError(error.code, error.details, error.message);
    }
    return error.message || fallback;
  }
  if (error instanceof TypeError && /fetch/i.test(error.message)) {
    return "Сервис временно недоступен. Проверьте соединение и повторите попытку.";
  }
  return error instanceof Error && error.message ? error.message : fallback;
}
