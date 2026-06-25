"use client";

/**
 * Chat demo — the worked example that proves the realtime substrate end to end:
 * channel list, live message pane (SSE), presence roster, typing indicator,
 * and member invite. It is an EDITABLE example: a generated app rewrites this
 * UI freely but keeps using `useChannel` + the /api/channels + /api/realtime
 * contracts. The fixed engine underneath (hub, policy, routes) is never touched.
 */

import { useEffect, useRef, useState } from "react";

import { useChannel } from "@/components/realtime/use-channel";
import type { Message } from "@/lib/db/schema";
import type { RealtimeEvent } from "@/lib/realtime/types";

type ChannelRef = { id: string; title: string | null };

function shortId(id: string): string {
  return id.slice(0, 6);
}

function fmtTime(value: unknown): string {
  if (!value) return "";
  const d = new Date(value as string);
  return Number.isNaN(d.getTime())
    ? ""
    : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

const STATUS_DOT: Record<string, string> = {
  open: "bg-emerald-500",
  connecting: "bg-amber-500",
  closed: "bg-red-500",
};

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
    if (now - lastTyping.current > 1500) {
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

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center justify-between border-b border-neutral-800 px-4 py-2 text-sm">
        <div className="flex items-center gap-2 text-neutral-400">
          <span
            className={`h-2 w-2 rounded-full ${STATUS_DOT[status] ?? "bg-neutral-600"}`}
            title={status}
          />
          <span>{presence.length} в сети</span>
        </div>
        <span className="text-neutral-500">{shortId(channelId)}</span>
      </div>

      <div className="min-h-0 flex-1 space-y-2 overflow-y-auto px-4 py-3">
        {messages.length === 0 && (
          <p className="pt-8 text-center text-sm text-neutral-600">
            Сообщений пока нет. Напишите первое.
          </p>
        )}
        {messages.map((e) => {
          const m = e.data as Message;
          const mine = m.userId === currentUserId;
          return (
            <div
              key={m.id ?? e.id}
              className={`flex ${mine ? "justify-end" : "justify-start"}`}
            >
              <div
                className={`max-w-[78%] rounded-2xl px-3 py-2 text-sm ${
                  mine
                    ? "bg-white text-neutral-900"
                    : "bg-neutral-800 text-neutral-100"
                }`}
              >
                {!mine && (
                  <div className="mb-0.5 text-[11px] text-neutral-400">
                    {shortId(m.userId)}
                  </div>
                )}
                <div className="whitespace-pre-wrap break-words">{m.body}</div>
                <div
                  className={`mt-0.5 text-[10px] ${mine ? "text-neutral-500" : "text-neutral-500"}`}
                >
                  {fmtTime(m.createdAt)}
                </div>
              </div>
            </div>
          );
        })}
        <div ref={bottomRef} />
      </div>

      <div className="h-5 px-4 text-xs text-neutral-500">
        {typingUser ? `${shortId(typingUser)} печатает…` : ""}
      </div>

      <form
        onSubmit={submit}
        className="flex items-center gap-2 border-t border-neutral-800 p-3"
      >
        <input
          value={text}
          onChange={(e) => onType(e.target.value)}
          placeholder="Сообщение…"
          className="min-w-0 flex-1 rounded-full border border-neutral-700 bg-neutral-950 px-4 py-2 text-sm outline-none focus:border-neutral-500"
        />
        <button
          type="submit"
          className="rounded-full bg-white px-4 py-2 text-sm font-medium text-neutral-900 transition hover:bg-neutral-200"
        >
          →
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
  const [initial, setInitial] = useState<RealtimeEvent[] | null>(null);
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteMsg, setInviteMsg] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setInitial(null);
    fetch(`/api/channels/${channelId}/messages`, { credentials: "include" })
      .then((r) => r.json())
      .then((j: { data?: RealtimeEvent[] }) => {
        if (alive) setInitial(j.data ?? []);
      })
      .catch(() => {
        if (alive) setInitial([]);
      });
    return () => {
      alive = false;
    };
  }, [channelId]);

  async function invite(e: React.FormEvent) {
    e.preventDefault();
    const email = inviteEmail.trim();
    if (!email) return;
    const r = await fetch(`/api/channels/${channelId}/members`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email }),
      credentials: "include",
    });
    const j = (await r.json().catch(() => ({}))) as { error?: string };
    setInviteMsg(r.ok ? "Участник добавлен" : (j.error ?? "Ошибка"));
    setInviteEmail("");
    setTimeout(() => setInviteMsg(null), 3000);
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <form
        onSubmit={invite}
        className="flex items-center gap-2 border-b border-neutral-800 px-4 py-2"
      >
        <input
          value={inviteEmail}
          onChange={(e) => setInviteEmail(e.target.value)}
          placeholder="Добавить участника по email"
          className="min-w-0 flex-1 rounded-md border border-neutral-700 bg-neutral-950 px-3 py-1.5 text-sm outline-none focus:border-neutral-500"
        />
        <button
          type="submit"
          className="rounded-md border border-neutral-700 px-3 py-1.5 text-sm text-neutral-300 transition hover:bg-neutral-800"
        >
          Пригласить
        </button>
        {inviteMsg && (
          <span className="text-xs text-neutral-400">{inviteMsg}</span>
        )}
      </form>
      {initial === null ? (
        <div className="p-4 text-sm text-neutral-500">Загрузка…</div>
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
      <aside className="flex w-64 shrink-0 flex-col border-r border-neutral-800">
        <form onSubmit={createChannel} className="space-y-2 border-b border-neutral-800 p-3">
          <input
            value={newTitle}
            onChange={(e) => setNewTitle(e.target.value)}
            placeholder="Новая беседа"
            className="w-full rounded-md border border-neutral-700 bg-neutral-950 px-3 py-1.5 text-sm outline-none focus:border-neutral-500"
          />
          <button
            type="submit"
            className="w-full rounded-md bg-white px-3 py-1.5 text-sm font-medium text-neutral-900 transition hover:bg-neutral-200"
          >
            Создать
          </button>
        </form>
        <nav className="min-h-0 flex-1 overflow-y-auto p-2">
          {channels.length === 0 && (
            <p className="px-2 py-4 text-sm text-neutral-600">
              Создайте первую беседу.
            </p>
          )}
          {channels.map((c) => (
            <button
              key={c.id}
              onClick={() => setActiveId(c.id)}
              className={`mb-1 w-full truncate rounded-md px-3 py-2 text-left text-sm transition ${
                c.id === activeId
                  ? "bg-neutral-800 text-white"
                  : "text-neutral-400 hover:bg-neutral-900"
              }`}
            >
              {c.title || "Беседа"}
            </button>
          ))}
        </nav>
      </aside>

      <section className="min-w-0 flex-1">
        {activeId ? (
          <ChannelView
            key={activeId}
            channelId={activeId}
            currentUserId={currentUserId}
          />
        ) : (
          <div className="flex h-full items-center justify-center text-sm text-neutral-600">
            Выберите или создайте беседу
          </div>
        )}
      </section>
    </div>
  );
}
