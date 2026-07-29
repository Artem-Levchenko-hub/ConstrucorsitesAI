import { createHmac, timingSafeEqual } from "node:crypto";

export type MaxLaunchUser = {
  id: number;
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

export function validateMaxInitData(
  initData: string,
  botToken: string,
  nowSeconds = Math.floor(Date.now() / 1000),
): ValidatedMaxInitData {
  if (!initData || initData.length > 16_384) {
    throw new Error("invalid MAX initData");
  }
  const pairs = initData.split("&").map((pair) => {
    const separator = pair.indexOf("=");
    if (separator < 1) throw new Error("invalid MAX initData");
    return [pair.slice(0, separator), decodeURIComponent(pair.slice(separator + 1))] as const;
  });
  const hashes = pairs.filter(([key]) => key === "hash");
  if (hashes.length !== 1 || !/^[a-f0-9]{64}$/i.test(hashes[0][1])) {
    throw new Error("invalid MAX initData signature");
  }
  const keys = pairs.map(([key]) => key);
  if (new Set(keys).size !== keys.length) {
    throw new Error("duplicate MAX initData parameter");
  }
  const launchParams = pairs
    .filter(([key]) => key !== "hash")
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([key, value]) => `${key}=${value}`)
    .join("\n");
  const secretKey = createHmac("sha256", "WebAppData").update(botToken).digest();
  const actual = createHmac("sha256", secretKey).update(launchParams).digest();
  const expected = Buffer.from(hashes[0][1], "hex");
  if (expected.length !== actual.length || !timingSafeEqual(expected, actual)) {
    throw new Error("invalid MAX initData signature");
  }

  const values = new Map(pairs);
  const authDate = Number(values.get("auth_date"));
  if (
    !Number.isSafeInteger(authDate) ||
    nowSeconds - authDate > MAX_AGE_SECONDS ||
    authDate - nowSeconds > MAX_CLOCK_SKEW_SECONDS
  ) {
    throw new Error("expired MAX initData");
  }
  let user: MaxLaunchUser;
  try {
    user = JSON.parse(values.get("user") || "") as MaxLaunchUser;
  } catch {
    throw new Error("invalid MAX user");
  }
  if (!Number.isSafeInteger(user?.id) || !user.first_name) {
    throw new Error("invalid MAX user");
  }
  return {
    queryId: values.get("query_id") || null,
    authDate,
    user,
    startParam: values.get("start_param") || null,
  };
}
