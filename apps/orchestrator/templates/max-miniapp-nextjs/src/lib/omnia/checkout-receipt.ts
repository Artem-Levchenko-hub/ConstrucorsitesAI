import "server-only";
import { createPublicKey, verify } from "node:crypto";

const realKey = "__YLEUM_CHECKOUT_RECEIPT_PUBLIC_KEY__";
const fixtureKey = "__YLEUM_MEAL_FIXTURE_PUBLIC_KEY__";
const uuid = /^[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}$/;
const synthetic = /^mealqa_[a-f0-9]{32}$/;
const fields = ["purpose", "project_id", "actor_id", "order_id", "sku", "quantity",
  "amount_kopecks", "currency", "test", "iat", "exp"] as const;

export type CheckoutReceiptClaims = {
  purpose: "yleum.max.checkout.receipt";
  project_id: string;
  actor_id: string;
  order_id: string;
  sku: string;
  quantity: number;
  amount_kopecks: number;
  currency: "RUB";
  test: boolean;
  iat: number;
  exp: number;
};

type ExpectedCheckout = {
  actorId: string;
  projectId: string;
  sku: string;
  quantity: number;
  amountKopecks: number;
  test: boolean;
};

/** Verify before granting any access. Read the expected actor from getMaxUser(). */
export function verifyYleumCheckoutReceipt(
  receipt: unknown, expected: ExpectedCheckout,
): CheckoutReceiptClaims | null {
  const isFixture = synthetic.test(expected.actorId);
  if (typeof receipt !== "string" || receipt.length > 4096 ||
      typeof expected.test !== "boolean" || !uuid.test(expected.projectId) ||
      (isFixture && !expected.test) ||
      (!isFixture && (!/^[1-9][0-9]{0,18}$/.test(expected.actorId) ||
        BigInt(expected.actorId) > 9223372036854775807n)) ||
      !Number.isSafeInteger(expected.quantity) || expected.quantity < 1 ||
      !Number.isSafeInteger(expected.amountKopecks) || expected.amountKopecks < 1) return null;
  const parts = /^v1\.([A-Za-z0-9_-]+)\.([A-Za-z0-9_-]{86})$/.exec(receipt);
  if (!parts) return null;
  try {
    const payload = Buffer.from(parts[1], "base64url");
    const signature = Buffer.from(parts[2], "base64url");
    if (payload.toString("base64url") !== parts[1] || signature.toString("base64url") !== parts[2] ||
        !verify(null, Buffer.from(`v1.${parts[1]}`, "ascii"),
          createPublicKey(isFixture ? fixtureKey : realKey), signature)) return null;
    const claims: CheckoutReceiptClaims = JSON.parse(payload.toString("utf8"));
    if (!claims || Object.keys(claims).length !== fields.length ||
        !fields.every((field) => Object.hasOwn(claims, field))) return null;
    const now = Math.floor(Date.now() / 1000);
    if (claims.purpose !== "yleum.max.checkout.receipt" || claims.project_id !== expected.projectId ||
        claims.actor_id !== expected.actorId || typeof claims.order_id !== "string" ||
        !uuid.test(claims.order_id) || claims.sku !== expected.sku ||
        claims.quantity !== expected.quantity || claims.amount_kopecks !== expected.amountKopecks ||
        claims.currency !== "RUB" || claims.test !== expected.test ||
        !Number.isSafeInteger(claims.iat) || !Number.isSafeInteger(claims.exp) ||
        claims.iat > now + 5 || claims.exp <= now ||
        claims.exp <= claims.iat || claims.exp - claims.iat > 300) return null;
    return claims;
  } catch {
    return null;
  }
}
