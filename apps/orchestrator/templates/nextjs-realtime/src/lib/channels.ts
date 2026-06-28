/**
 * Channel server helpers — create / list / invite / history. FIXED template file.
 *
 * These are the building blocks a generated app calls from server components and
 * route handlers to manage conversations. Membership (`channel_members`) is the
 * single source of truth that `realtime/policy.ts` enforces on every subscribe
 * and publish, so creating a channel and adding members is ALL it takes to make
 * a private, leak-proof conversation.
 */

import { randomUUID } from "node:crypto";

import { and, asc, eq, inArray } from "drizzle-orm";

import { db } from "@/lib/db";
import {
  channelMembers,
  channels,
  messages,
  users,
  type Channel,
  type Message,
} from "@/lib/db/schema";
import type { RealtimeEvent } from "@/lib/realtime/types";

/** Channels the user is a member of, newest first. */
export async function listUserChannels(userId: string): Promise<Channel[]> {
  const memberRows = await db
    .select({ channelId: channelMembers.channelId })
    .from(channelMembers)
    .where(eq(channelMembers.userId, userId));
  const ids = memberRows.map((r) => r.channelId);
  if (ids.length === 0) return [];
  return db.select().from(channels).where(inArray(channels.id, ids));
}

/** Create a conversation and make `userId` its first (admin) member. */
export async function createChannel(
  userId: string,
  title: string,
): Promise<Channel> {
  const id = randomUUID();
  const [channel] = await db
    .insert(channels)
    .values({ id, kind: "conversation", title: title.trim() || "Беседа", createdBy: userId })
    .returning();
  if (!channel) throw new Error("createChannel: insert returned no row");
  await db
    .insert(channelMembers)
    .values({ channelId: id, userId, role: "admin" })
    .onConflictDoNothing();
  return channel;
}

/** True iff the user belongs to the channel. */
export async function isMember(
  channelId: string,
  userId: string,
): Promise<boolean> {
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

/** Add the user with `email` to a channel. Returns the added user id or null. */
export async function addMemberByEmail(
  channelId: string,
  email: string,
): Promise<string | null> {
  const [user] = await db
    .select({ id: users.id })
    .from(users)
    .where(eq(users.email, email.trim().toLowerCase()))
    .limit(1);
  if (!user) return null;
  await db
    .insert(channelMembers)
    .values({ channelId, userId: user.id, role: "member" })
    .onConflictDoNothing();
  return user.id;
}

/** Channel roster: every member with their user identity + role, oldest first. */
export async function listMembers(
  channelId: string,
): Promise<{ userId: string; email: string; name: string | null; role: string }[]> {
  return db
    .select({
      userId: channelMembers.userId,
      email: users.email,
      name: users.name,
      role: channelMembers.role,
    })
    .from(channelMembers)
    .innerJoin(users, eq(users.id, channelMembers.userId))
    .where(eq(channelMembers.channelId, channelId))
    .orderBy(asc(channelMembers.createdAt));
}

/** Recent persisted messages for a channel as resumable realtime events. */
export async function getHistory(
  channelId: string,
  limit = 50,
): Promise<RealtimeEvent<Message>[]> {
  const rows = await db
    .select()
    .from(messages)
    .where(eq(messages.channelId, channelId))
    .orderBy(asc(messages.createdAt))
    .limit(limit);
  return rows.map((row) => ({
    // Hub ids are in-memory and reset per process, so history carries id 0; the
    // UI de-dupes messages by the persisted row uuid, not this field.
    id: 0,
    channel: `conversation:${channelId}`,
    type: "message",
    userId: row.userId,
    data: row,
    ts: row.createdAt ? new Date(row.createdAt).getTime() : Date.now(),
  }));
}
