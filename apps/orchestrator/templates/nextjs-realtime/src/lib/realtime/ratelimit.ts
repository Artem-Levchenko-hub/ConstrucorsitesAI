/**
 * In-memory fixed-window rate limiter for publishes. FIXED template file.
 *
 * Secure-from-first-prompt baseline: without a limit, any authenticated member
 * could flood a channel. A token-per-key fixed window (default 20 publishes /
 * 10s per user+channel) is enough to stop trivial spam in the single-container
 * dev preview and the single-replica default deploy. Multi-replica prod should
 * front this with a shared store (Redis INCR) — tracked in G005/G006; the call
 * site is unchanged when that lands.
 */

const WINDOW_MS = 10_000;
const MAX_IN_WINDOW = 20;

type Bucket = { count: number; resetAt: number };

const g = globalThis as unknown as { __omniaRateBuckets?: Map<string, Bucket> };
const buckets: Map<string, Bucket> =
  g.__omniaRateBuckets ?? (g.__omniaRateBuckets = new Map());

/** Returns true when the action is allowed; false when the window is exhausted. */
export function allowPublish(userId: string, channel: string): boolean {
  const key = `${userId}|${channel}`;
  const now = Date.now();
  const b = buckets.get(key);
  if (!b || now >= b.resetAt) {
    buckets.set(key, { count: 1, resetAt: now + WINDOW_MS });
    return true;
  }
  if (b.count >= MAX_IN_WINDOW) return false;
  b.count += 1;
  return true;
}
