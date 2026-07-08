"use client";

/**
 * Chat demo — the worked example that proves the realtime substrate end to end:
 * channel list, live message pane (SSE), presence roster, typing indicator,
 * and member invite. It is an EDITABLE example: a generated app rewrites this
 * UI freely but keeps using `useChannel` + the /api/channels + /api/realtime
 * contracts. The fixed engine underneath (hub, policy, routes) is never touched.
 *
 * Styling: token-driven (bg-card / bg-primary / text-muted-foreground / …) so it
 * inherits the theme + the per-project brand accent. Never hardcode neutral-*.
 */

import { useEffect, useRef, useState } from "react";

import { InviteMember } from "@/components/realtime/invite-member";
import { useChannel } from "@/components/realtime/use-channel";
import { useChannelHistory } from "@/components/realtime/use-channel-history";
import type { Message } from "@/lib/db/schema";
import type { RealtimeEvent } from "@/lib/realtime/types";

type ChannelRef = { id: string; title: string | null };

function shortId(id: string): string {
  return id.slice(0, 6);
}

// Deterministic avatar hue from a user id → each person a stable colour.
function hueFromId(id: string): number {
  let h = 0;
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) % 360;
  return h;
}

function Avatar({ id, size = 28 }: { id: string; size?: number }) {
  const hue = hueFromId(id);
  return (
    <span
      className="flex shrink-0 items-center justify-center rounded-full text-[11px] font-semibold text-white"
      style={{
        width: size,
        height: size,
        background: `linear-gradient(135deg, hsl(${hue} 70% 55%), hsl(${(hue + 40) % 360} 70% 45%))`,
      }}
    >
      {shortId(id).slice(0, 2).toUpperCase()}
    </span>
  );
}

function fmtTime(value: unknown): string {
  if (!value) return "";
  const d = new Date(value as string);
  return Number.isNaN(d.getTime())
    ? ""
    : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

const STATUS = {
  open: { dot: "bg-success", label: "на связи" },
  connecting: { dot: "bg-warning", label: "соединение…" },
  closed: { dot: "bg-destructive", label: "нет связи" },
} as const;

// ── Live message pane for one conversation ──────────────────────────────────
function MessagePane({
  channelId,
  currentUserId,
  initial,
}: {
  channelId: string;
  currentUserId: string;
  initial: RealtimeEvent[];
}) {
  const channel = `conversation:${channelId}`;
  const [typingUser, setTypingUser] = useState<string | null>(null);
  const typingTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const { messages, presence, status, send } = useChannel(channel, {
    initial,
    onEvent: (e) => {
      if (e.type === "typing" && e.userId && e.userId !== currentUserId) {
        setTypingUser(e.userId);
        if (typingTimer.current) clearTimeout(typingTimer.current);
        typingTimer.current = setTimeout(() => setTypingUser(null), 2500);
      }
    },
  });

  const [text, setText] = useState("");
  const lastTyping = useRef(0);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length]);

  function onType(v: string) {
    setText(v);
    const now = Date.now();
    if (now - lastTyping.current > 2500) {
      lastTyping.current = now;
      void send("typing", {});
    }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const t = text.trim();
    if (!t) return;
    setText("");
    await send("message", { text: t });
  }

  const st = STATUS[status as keyof typeof STATUS] ?? {
    dot: "bg-muted-foreground",
    label: status,
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center justify-between border-b border-border px-4 py-2.5 text-sm">
        <div className="flex items-center gap-2 text-muted-foreground">
          <span className={`pulse-dot h-2 w-2 rounded-full ${st.dot}`} title={st.label} />
          <span className="font-medium text-foreground">{presence.length} в сети</span>
          <span className="text-muted-foreground">· {st.label}</span>
        </div>
        <span className="rounded-md bg-muted px-2 py-0.5 font-mono text-xs text-muted-foreground">
          {shortId(channelId)}
        </span>
      </div>

      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-4 py-4">
        {messages.length === 0 && (
          <div className="flex flex-col items-center justify-center gap-2 pt-16 text-center">
            <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-primary/10 text-primary">
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z" />
              </svg>
            </span>
            <p className="text-sm text-muted-foreground">
              Сообщений пока нет. Напишите первое.
            </p>
          </div>
        )}
        {messages.map((e) => {
          const m = e.data as Message;
          const mine = m.userId === currentUserId;
          return (
            <div
              key={m.id ?? e.id}
              className={`msg-in flex items-end gap-2 ${mine ? "justify-end" : "justify-start"}`}
            >
              {!mine && <Avatar id={m.userId} size={26} />}
              <div
                className={`max-w-[76%] rounded-2xl px-3.5 py-2 text-sm shadow-sm ${
                  mine
                    ? "rounded-br-md bg-primary text-primary-foreground"
                    : "rounded-bl-md border border-border bg-card text-card-foreground"
                }`}
              >
                {!mine && (
                  <div className="mb-0.5 text-[11px] font-medium text-muted-foreground">
                    {shortId(m.userId)}
                  </div>
                )}
                <div className="whitespace-pre-wrap break-words leading-relaxed">
                  {m.body}
                </div>
                <div
                  className={`mt-0.5 text-right text-[10px] ${
                    mine ? "text-primary-foreground/70" : "text-muted-foreground"
                  }`}
                >
                  {fmtTime(m.createdAt)}
                </div>
              </div>
            </div>
          );
        })}
        <div ref={bottomRef} />
      </div>

      <div className="flex h-6 items-center gap-1.5 px-4 text-xs text-muted-foreground">
        {typingUser && (
          <>
            <span className="flex items-end gap-0.5">
              <span className="typing-dot h-1.5 w-1.5 rounded-full bg-muted-foreground" />
              <span className="typing-dot h-1.5 w-1.5 rounded-full bg-muted-foreground" />
              <span className="typing-dot h-1.5 w-1.5 rounded-full bg-muted-foreground" />
            </span>
            {shortId(typingUser)} печатает…
          </>
        )}
      </div>

      <form
        onSubmit={submit}
        className="flex items-center gap-2 border-t border-border p-3"
      >
        <input
          value={text}
          onChange={(e) => onType(e.target.value)}
          placeholder="Сообщение…"
          className="min-w-0 flex-1 rounded-full border border-border bg-muted px-4 py-2.5 text-sm outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/30"
        />
        <button
          type="submit"
          aria-label="Отправить"
          className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground transition hover:opacity-90 active:scale-95 disabled:opacity-50"
          disabled={!text.trim()}
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <line x1="22" y1="2" x2="11" y2="13" />
            <polygon points="22 2 15 22 11 13 2 9 22 2" />
          </svg>
        </button>
      </form>
    </div>
  );
}

// ── Loads history for a channel, then mounts the live pane ──────────────────
function ChannelView({
  channelId,
  currentUserId,
}: {
  channelId: string;
  currentUserId: string;
}) {
  // Fixed primitives: history is loaded envelope-safe (useChannelHistory unwraps
  // `.data` and guards an undefined id — the #1 client bug), and the invite
  // control is the locked <InviteMember> so a restyle can never drop "add a
  // friend" and leave the user alone in the channel.
  const { initial } = useChannelHistory(channelId);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="border-b border-border bg-card/50 px-4 py-2.5">
        <InviteMember channelId={channelId} />
      </div>
      {initial === null ? (
        <div className="space-y-3 p-4">
          <div className="omnia-skeleton h-10 w-1/2" />
          <div className="omnia-skeleton ml-auto h-10 w-2/5" />
          <div className="omnia-skeleton h-10 w-3/5" />
        </div>
      ) : (
        <MessagePane
          key={channelId}
          channelId={channelId}
          currentUserId={currentUserId}
          initial={initial}
        />
      )}
    </div>
  );
}

// ── Sidebar + active conversation ───────────────────────────────────────────
export function ChatClient({
  currentUserId,
  initialChannels,
}: {
  currentUserId: string;
  initialChannels: ChannelRef[];
}) {
  const [channels, setChannels] = useState<ChannelRef[]>(initialChannels);
  const [activeId, setActiveId] = useState<string | null>(
    initialChannels[0]?.id ?? null,
  );
  const [newTitle, setNewTitle] = useState("");

  async function createChannel(e: React.FormEvent) {
    e.preventDefault();
    const title = newTitle.trim();
    if (!title) return;
    const r = await fetch("/api/channels", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
      credentials: "include",
    });
    const j = (await r.json().catch(() => ({}))) as {
      data?: { id: string; title: string | null };
    };
    if (r.ok && j.data) {
      setChannels((prev) => [...prev, { id: j.data!.id, title: j.data!.title }]);
      setActiveId(j.data.id);
      setNewTitle("");
    }
  }

  return (
    <div className="flex h-full min-h-0">
      <aside className="flex w-64 shrink-0 flex-col border-r border-border bg-sidebar">
        <form onSubmit={createChannel} className="space-y-2 border-b border-border p-3">
          <input
            value={newTitle}
            onChange={(e) => setNewTitle(e.target.value)}
            placeholder="Новая беседа"
            className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/30"
          />
          <button
            type="submit"
            className="w-full rounded-lg bg-primary px-3 py-2 text-sm font-semibold text-primary-foreground transition hover:opacity-90 active:scale-[.98] disabled:opacity-50"
            disabled={!newTitle.trim()}
          >
            Создать
          </button>
        </form>
        <nav className="min-h-0 flex-1 space-y-1 overflow-y-auto p-2">
          {channels.length === 0 && (
            <p className="px-2 py-6 text-center text-sm text-muted-foreground">
              Создайте первую беседу.
            </p>
          )}
          {channels.map((c) => {
            const active = c.id === activeId;
            return (
              <button
                key={c.id}
                onClick={() => setActiveId(c.id)}
                className={`flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-sm transition ${
                  active
                    ? "bg-primary/12 font-medium text-foreground ring-1 ring-primary/25"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground"
                }`}
              >
                <Avatar id={c.id} size={30} />
                <span className="min-w-0 flex-1 truncate">{c.title || "Беседа"}</span>
              </button>
            );
          })}
        </nav>
      </aside>

      <section className="min-w-0 flex-1 bg-background">
        {activeId ? (
          <ChannelView
            key={activeId}
            channelId={activeId}
            currentUserId={currentUserId}
          />
        ) : (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-center text-sm text-muted-foreground">
            <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-primary/10 text-primary">
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
              </svg>
            </span>
            Выберите или создайте беседу
          </div>
        )}
      </section>
    </div>
  );
}
