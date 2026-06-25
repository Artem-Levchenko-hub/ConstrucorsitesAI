/**
 * Realtime substrate — shared event contracts.
 *
 * FIXED template file. The AI building an app on this stack consumes these
 * types but never edits this file. A realtime app is modelled as a set of
 * CHANNELS (e.g. `conversation:42`, `room:lobby`, `user:7`); clients SUBSCRIBE
 * to a channel over SSE and PUBLISH events to it over POST. Every event the
 * server fans out has this shape.
 */

/** A single realtime event delivered to every subscriber of a channel. */
export type RealtimeEvent<T = unknown> = {
  /** Server-assigned, per-channel monotonic id. Used for resume + dedupe. */
  id: number;
  /** Channel the event belongs to, e.g. "conversation:42". */
  channel: string;
  /**
   * Event type. `message` and `presence` are reserved (the engine persists
   * `message` and synthesises `presence`); any other string is an app event
   * the writer model invents (e.g. `typing`, `reaction`, `cursor`).
   */
  type: string;
  /** App payload. For `message` this is the persisted row. */
  data: T;
  /** Sender user id, or null for system/engine-emitted events (presence). */
  userId: string | null;
  /** Epoch milliseconds the server stamped the event. */
  ts: number;
};

/** One member's presence on a channel. */
export type PresenceState = {
  userId: string;
  /** Epoch ms the member joined (first active subscription on this channel). */
  since: number;
};

/** The presence snapshot the engine emits as a `presence` event's `data`. */
export type PresencePayload = {
  channel: string;
  members: PresenceState[];
};

/** Body accepted by POST /api/realtime/[channel]. */
export type PublishBody = {
  type: string;
  data: unknown;
};

/** Reserved event types the engine treats specially. */
export const RESERVED_EVENT_TYPES = ["message", "presence"] as const;
