/**
 * Channel authorization — the security seam of the realtime stack. FIXED file.
 *
 * Every subscribe (SSE) and publish (POST) is gated here BEFORE the hub is
 * touched, so a user can never read or write a channel they do not belong to.
 * Server-enforced; the client cannot bypass it. Default policy:
 *
 *   conversation:<id>  → only rows in `channel_members` (channel, user) may read
 *                        AND write. Relation-based (membership) authorization,
 *                        not owner-scoping — what a DM / group chat / class chat
 *                        needs so messages cannot leak across conversations.
 *   user:<id>          → only that user (a private per-user notification feed).
 *   presence:<id>      → alias of conversation:<id> membership.
 *   public:<name>      → any authenticated user may read; write is members-only
 *                        when a membership list exists, else any authed user.
 *
 * G002 generalises this into the entity engine (membership declared in data, not
 * code). Until then, apps add channels by inserting `channel_members` rows.
 */

import { and, eq } from "drizzle-orm";

import { db } from "@/lib/db";
import { channelMembers } from "@/lib/db/schema";

export type Access = "read" | "write";

export class ChannelForbiddenError extends Error {
  constructor(public channel: string) {
    super(`forbidden channel: ${channel}`);
    this.name = "ChannelForbiddenError";
  }
}

/** Parse a channel string into `{ kind, id }` (split on the first colon). */
export function parseChannel(channel: string): { kind: string; id: string } {
  const i = channel.indexOf(":");
  if (i === -1) return { kind: channel, id: "" };
  return { kind: channel.slice(0, i), id: channel.slice(i + 1) };
}

/** True iff `userId` is a member of the conversation/channel id. */
async function isMember(channelId: string, userId: string): Promise<boolean> {
  if (!channelId) return false;
  const rows = await db
    .select({ id: channelMembers.id })
    .from(channelMembers)
    .where(
      and(
        eq(channelMembers.channelId, channelId),
        eq(channelMembers.userId, userId),
      ),
    )
    .limit(1);
  return rows.length > 0;
}

/**
 * Throw {@link ChannelForbiddenError} unless `userId` may `access` `channel`.
 * Pure authorization — callers map the throw to a 403.
 */
export async function assertChannelAccess(
  channel: string,
  userId: string,
  access: Access,
): Promise<void> {
  const { kind, id } = parseChannel(channel);

  switch (kind) {
    case "conversation":
    case "presence": {
      if (await isMember(id, userId)) return;
      throw new ChannelForbiddenError(channel);
    }
    case "user": {
      if (id === userId) return;
      throw new ChannelForbiddenError(channel);
    }
    case "public": {
      if (access === "read") return;
      const hasList = await db
        .select({ id: channelMembers.id })
        .from(channelMembers)
        .where(eq(channelMembers.channelId, id))
        .limit(1);
      if (hasList.length === 0) return; // open write when no membership list
      if (await isMember(id, userId)) return;
      throw new ChannelForbiddenError(channel);
    }
    default:
      throw new ChannelForbiddenError(channel); // fail-closed on unknown kinds
  }
}
