/**
 * In-memory fixed-window rate limiter for publishes. FIXED template file.
 *
 * Secure-from-first-prompt baseline: without a limit, any authenticated member
 * could flood a channel. A token-per-key fixed window stops trivial spam in the
 * single-container dev preview and the single-replica default deploy.
 *
 * Messages and EPHEMERAL signals (typing / reactions / cursors) get SEPARATE
 * budgets keyed by kind: a chatty typing indicator (one ping per keystroke
 * burst) must never starve a real "send". Sharing one bucket is what made a
 * single user 429 their own messages just by typing — the live self-DoS bug.
 * Multi-replica prod should front this with a shared store (Redis INCR) —
 * tracked in G005/G006; the call site is unchanged when that lands.
 */

const WINDOW_MS = 10_000;
const MAX_MESSAGE = 30; // real messages / 10s per user+channel
const MAX_EPHEMERAL = 120; // typing/reactions/cursors / 10s — never starves a message

export type PublishKind = "message" | "ephemeral";

type Bucket = { count: number; resetAt: number };

const g = globalThis as unknown as { __omniaRateBuckets?: Map<string, Bucket> };
const buckets: Map<string, Bucket> =
  g.__omniaRateBuckets ?? (g.__omniaRateBuckets = new Map());

/**
 * Returns true when the action is allowed; false when that KIND's window is
 * exhausted. Messages and ephemeral signals are limited independently so typing
 * can never block a message.
 */
export function allowPublish(
  userId: string,
  channel: string,
  kind: PublishKind = "message",
): boolean {
  const max = kind === "message" ? MAX_MESSAGE : MAX_EPHEMERAL;
  const key = `${userId}|${channel}|${kind}`;
  const now = Date.now();
  const b = buckets.get(key);
  if (!b || now >= b.resetAt) {
    buckets.set(key, { count: 1, resetAt: now + WINDOW_MS });
    return true;
  }
  if (b.count >= max) return false;
  b.count += 1;
  return true;
}
