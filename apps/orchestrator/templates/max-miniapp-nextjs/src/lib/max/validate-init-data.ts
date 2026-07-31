import { createHmac, timingSafeEqual } from "node:crypto";

export type MaxLaunchUser = {
  id: string;
  first_name: string;
  last_name?: string | null;
  username?: string | null;
  language_code?: string | null;
  photo_url?: string | null;
};

export type ValidatedMaxInitData = {
  queryId: string | null;
  authDate: number;
  user: MaxLaunchUser;
  startParam: string | null;
};

const MAX_AGE_SECONDS = 24 * 60 * 60;
const MAX_CLOCK_SKEW_SECONDS = 5 * 60;

export type MaxInitDataErrorCode =
  | "missing"
  | "malformed"
  | "signature"
  | "expired"
  | "user";

export class MaxInitDataError extends Error {
  readonly code: MaxInitDataErrorCode;

  constructor(code: MaxInitDataErrorCode, message: string) {
    super(message);
    this.name = "MaxInitDataError";
    this.code = code;
  }
}

function fail(code: MaxInitDataErrorCode, message: string): never {
  throw new MaxInitDataError(code, message);
}

function normaliseInitData(input: string): string {
  const trimmed = input.trim().replace(/^#/, "");
  if (!trimmed) return "";

  // WebApp.initData normally contains the value of WebAppData. Some older
  // clients expose the complete URL fragment instead, so unwrap it while
  // still requiring the inner payload to pass the same HMAC validation.
  if (trimmed.startsWith("WebAppData=")) {
    return new URLSearchParams(trimmed).get("WebAppData") || "";
  }
  return trimmed;
}

export function validateMaxInitData(
  initData: string,
  botToken: string,
  nowSeconds = Math.floor(Date.now() / 1000),
): ValidatedMaxInitData {
  const candidate = normaliseInitData(initData);
  if (!candidate || candidate.length > 16_384) {
    fail("missing", "invalid MAX initData");
  }
  const pairs = candidate.split("&").map((pair) => {
    const separator = pair.indexOf("=");
    if (separator < 1) fail("malformed", "invalid MAX initData");
    try {
      return [
        pair.slice(0, separator),
        decodeURIComponent(pair.slice(separator + 1)),
      ] as const;
    } catch {
      fail("malformed", "invalid MAX initData encoding");
    }
  });
  const hashes = pairs.filter(([key]) => key === "hash");
  if (hashes.length !== 1 || !/^[a-f0-9]{64}$/i.test(hashes[0][1])) {
    fail("signature", "invalid MAX initData signature");
  }
  const keys = pairs.map(([key]) => key);
  if (new Set(keys).size !== keys.length) {
    fail("malformed", "duplicate MAX initData parameter");
  }
  const launchParams = pairs
    .filter(([key]) => key !== "hash")
    .sort(([left], [right]) => (left < right ? -1 : left > right ? 1 : 0))
    .map(([key, value]) => `${key}=${value}`)
    .join("\n");
  const secretKey = createHmac("sha256", "WebAppData").update(botToken).digest();
  const actual = createHmac("sha256", secretKey).update(launchParams).digest();
  const expected = Buffer.from(hashes[0][1], "hex");
  if (expected.length !== actual.length || !timingSafeEqual(expected, actual)) {
    fail("signature", "invalid MAX initData signature");
  }

  const values = new Map(pairs);
  const authDate = Number(values.get("auth_date"));
  if (
    !Number.isSafeInteger(authDate) ||
    nowSeconds - authDate > MAX_AGE_SECONDS ||
    authDate - nowSeconds > MAX_CLOCK_SKEW_SECONDS
  ) {
    fail("expired", "expired MAX initData");
  }
  let rawUser: unknown;
  try {
    rawUser = JSON.parse(values.get("user") || "") as unknown;
  } catch {
    fail("user", "invalid MAX user");
  }
  if (!rawUser || typeof rawUser !== "object") {
    fail("user", "invalid MAX user");
  }
  const value = rawUser as Record<string, unknown>;
  const id =
    typeof value.id === "number" && Number.isSafeInteger(value.id)
      ? String(value.id)
      : typeof value.id === "string" && /^\d+$/.test(value.id)
        ? value.id
        : "";
  const firstName = typeof value.first_name === "string" ? value.first_name.trim() : "";
  if (!id || !firstName) fail("user", "invalid MAX user");

  const optionalString = (field: string): string | null =>
    typeof value[field] === "string" && value[field] ? value[field] : null;
  const user: MaxLaunchUser = {
    id,
    first_name: firstName,
    last_name: optionalString("last_name"),
    username: optionalString("username"),
    language_code: optionalString("language_code"),
    photo_url: optionalString("photo_url"),
  };
  return {
    queryId: values.get("query_id") || null,
    authDate,
    user,
    startParam: values.get("start_param") || null,
  };
}
