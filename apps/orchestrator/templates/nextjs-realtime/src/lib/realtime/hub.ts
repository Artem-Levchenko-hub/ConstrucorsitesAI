/**
 * Realtime hub — the pub/sub + presence core of the stack. FIXED template file.
 *
 * Two-tier transport by design (justified, not a workaround):
 *  - In-process fan-out is the PRIMARY path: an in-memory subscriber set
 *    delivers events to every SSE connection in the same Node process. The dev
 *    preview is a single container, so this alone gives correct realtime with
 *    zero extra infrastructure.
 *  - Redis is the OPTIONAL horizontal-scale path: when `REDIS_URL` is set the
 *    hub also publishes each event to a Redis channel and re-injects events from
 *    other instances, so N prod replicas behind a load balancer behave as one.
 *    Locally-born events carry this instance id and are skipped on the way back
 *    to prevent double delivery.
 *
 * Pinned to `globalThis` so Turbopack HMR (which re-evaluates modules on edit)
 * cannot orphan open SSE connections by constructing a second empty hub.
 */

import { createHash, randomUUID } from "node:crypto";

import type { RealtimeEvent, PresenceState } from "./types";

/**
 * Per-PROJECT Redis channel so one generated app's cross-replica fan-out can
 * NEVER reach another app's replicas — the Redis bus is shared infrastructure in
 * prod. Without this, every project published to one global `omnia:realtime`
 * channel: events for fixed-name channels (`public:lobby`, `presence:*`) were
 * re-injected into every other project's hub and delivered to its legitimate
 * same-named subscribers — a cross-tenant leak the per-subscribe ACL can't catch
 * (the event arrives already "inside" the other project). The namespace is a
 * one-way hash (never the raw secret in a channel name) of a per-project,
 * replica-IDENTICAL seed; it falls back to a literal for the single-container
 * dev preview, where there is no cross-project bus anyway.
 */
function realtimeChannel(): string {
  const seed =
    process.env.OMNIA_PROJECT_NS ||
    process.env.AUTH_SECRET ||
    process.env.AUTH_URL ||
    process.env.DATABASE_URL ||
    "local";
  const ns = createHash("sha256").update(seed).digest("hex").slice(0, 16);
  return `omnia:realtime:${ns}`;
}

type Subscriber = (event: RealtimeEvent) => void;

const RING_SIZE = 100; // last-N events kept per channel for SSE resume

type ChannelState = {
  subscribers: Set<Subscriber>;
  seq: number; // per-channel monotonic event id
  ring: RealtimeEvent[]; // recent events for Last-Event-ID / ?since= resume
  presence: Map<string, { since: number; refs: number }>; // userId -> tabs open
};

type RedisAdapter = { publish: (payload: string) => void; ready: boolean };

class Hub {
  private channels = new Map<string, ChannelState>();
  readonly instanceId = randomUUID();
  private redis: RedisAdapter | null = null;

  private state(channel: string): ChannelState {
    let s = this.channels.get(channel);
    if (!s) {
      s = { subscribers: new Set(), seq: 0, ring: [], presence: new Map() };
      this.channels.set(channel, s);
    }
    return s;
  }

  /**
   * Subscribe to a channel. Returns an unsubscribe function. When `userId` is
   * given the subscriber is tracked for presence: the first active subscription
   * for that user on the channel emits a presence join, the last emits a leave.
   */
  subscribe(
    channel: string,
    fn: Subscriber,
    userId?: string | null,
  ): () => void {
    const s = this.state(channel);
    s.subscribers.add(fn);
    if (userId) this.presenceJoin(channel, userId);
    return () => {
      s.subscribers.delete(fn);
      if (userId) this.presenceLeave(channel, userId);
    };
  }

  /**
   * Publish an event to a channel. Assigns the monotonic id + timestamp for a
   * locally-born event, stores it in the ring buffer, fans it out to local
   * subscribers, and (when Redis is configured) to other instances. An event
   * re-injected from Redis (`opts.fromRedis`) keeps its origin id/ts and is NOT
   * re-published (no echo loop).
   */
  publish(
    input: Omit<RealtimeEvent, "id" | "ts"> & { id?: number; ts?: number },
    opts?: { fromRedis?: boolean; instanceId?: string },
  ): RealtimeEvent {
    const s = this.state(input.channel);

    const event: RealtimeEvent = {
      id: input.id ?? ++s.seq,
      channel: input.channel,
      type: input.type,
      data: input.data,
      userId: input.userId,
      ts: input.ts ?? Date.now(),
    };

    // Keep local seq ahead of any id seen from a peer so a later local publish
    // can never collide with an id minted on another instance.
    if (input.id != null && input.id > s.seq) s.seq = input.id;

    s.ring.push(event);
    if (s.ring.length > RING_SIZE) s.ring.shift();

    for (const sub of s.subscribers) {
      try {
        sub(event);
      } catch {
        // A dead subscriber (client gone mid-write) must not stop fan-out.
      }
    }

    if (!opts?.fromRedis && this.redis?.ready) {
      this.redis.publish(JSON.stringify({ event, instanceId: this.instanceId }));
    }
    return event;
  }

  /** Events with id greater than `sinceId` from the ring buffer (SSE resume). */
  replay(channel: string, sinceId: number): RealtimeEvent[] {
    const s = this.channels.get(channel);
    if (!s) return [];
    return s.ring.filter((e) => e.id > sinceId);
  }

  /** Current presence snapshot for a channel. */
  presence(channel: string): PresenceState[] {
    const s = this.channels.get(channel);
    if (!s) return [];
    return [...s.presence.entries()].map(([userId, v]) => ({
      userId,
      since: v.since,
    }));
  }

  private presenceJoin(channel: string, userId: string): void {
    const s = this.state(channel);
    const cur = s.presence.get(userId);
    if (cur) {
      cur.refs += 1; // another tab for the same user — no new join event
      return;
    }
    s.presence.set(userId, { since: Date.now(), refs: 1 });
    this.emitPresence(channel);
  }

  private presenceLeave(channel: string, userId: string): void {
    const s = this.state(channel);
    const cur = s.presence.get(userId);
    if (!cur) return;
    cur.refs -= 1;
    if (cur.refs <= 0) {
      s.presence.delete(userId);
      this.emitPresence(channel);
    }
  }

  /** Fan a presence snapshot out as a reserved `presence` event. */
  private emitPresence(channel: string): void {
    this.publish({
      channel,
      type: "presence",
      userId: null,
      data: { channel, members: this.presence(channel) },
    });
  }

  /** Wire a Redis adapter (called once at startup when REDIS_URL is set). */
  attachRedis(adapter: RedisAdapter): void {
    this.redis = adapter;
  }
}

// HMR-safe singleton.
const g = globalThis as unknown as { __omniaHub?: Hub };
export const hub: Hub = g.__omniaHub ?? (g.__omniaHub = new Hub());

// Bring up the optional Redis fan-out exactly once per process. No-op without
// REDIS_URL, so the dev preview never needs Redis or the ioredis package.
let redisBootstrapped = false;
export async function ensureRedis(): Promise<void> {
  if (redisBootstrapped) return;
  redisBootstrapped = true;
  const url = process.env.REDIS_URL;
  if (!url) return;
  try {
    const { default: Redis } = await import("ioredis");
    const CHANNEL = realtimeChannel();
    const pub = new Redis(url);
    const sub = new Redis(url);
    await sub.subscribe(CHANNEL);
    sub.on("message", (_ch: string, payload: string) => {
      try {
        const { event, instanceId } = JSON.parse(payload) as {
          event: RealtimeEvent;
          instanceId: string;
        };
        if (instanceId === hub.instanceId) return; // our own echo
        hub.publish(event, { fromRedis: true, instanceId });
      } catch {
        // ignore malformed peer payloads
      }
    });
    hub.attachRedis({
      ready: true,
      publish: (p: string) => {
        void pub.publish(CHANNEL, p);
      },
    });
  } catch {
    // ioredis missing or Redis unreachable — in-process fan-out still serves.
  }
}
