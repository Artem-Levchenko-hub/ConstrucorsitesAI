"use client";

/**
 * ChatView — renders a messaging entity as a REAL conversation (bubbles + composer
 * + auto-scroll), not a CRUD table. FIXED engine component. CrudResource switches
 * to it for messaging entities (view="chat" or auto-detected), so a "мессенджер"
 * actually lets people write to each other instead of showing a table of rows.
 *
 * Schema-tolerant: it reads the message text / author / time from each row by
 * trying common field names, so it works whether the writer called the text field
 * `body`, `text`, `content` or `message`. Near-real-time is short polling driven by
 * CrudResource (true SSE arrives with the realtime stack).
 */

import * as React from "react";

import { type Row } from "@/lib/sdk";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

export const CHAT_TEXT_KEYS = [
  "body",
  "text",
  "content",
  "message",
  "msg",
  "comment",
  "caption",
];
const AUTHOR_KEYS = [
  "author",
  "sender",
  "user",
  "userId",
  "user_id",
  "createdBy",
  "created_by",
  "from",
  "authorId",
  "senderId",
];
const NAME_KEYS = [
  "authorName",
  "senderName",
  "name",
  "fullName",
  "full_name",
  "displayName",
  "username",
  "email",
];
const TIME_KEYS = [
  "createdAt",
  "created_at",
  "sentAt",
  "sent_at",
  "timestamp",
  "date",
  "time",
];

function pick(row: Row, keys: string[]): unknown {
  const r = row as Record<string, unknown>;
  for (const k of keys) {
    const v = r[k];
    if (v != null && v !== "") return v;
  }
  return undefined;
}

function fmtTime(v: unknown): string {
  if (v == null) return "";
  const d = new Date(String(v));
  return Number.isNaN(d.getTime())
    ? ""
    : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export interface ChatViewProps {
  rows: Row[];
  onSend: (text: string) => Promise<void> | void;
  loading?: boolean;
  emptyHint?: string;
  /** Current user / author id for own-vs-others bubble alignment (optional). The
   *  entity app has no client SessionProvider, so the caller passes this when it
   *  knows who "me" is; without it the thread renders left-aligned (still valid). */
  meId?: string;
}

export function ChatView({ rows, onSend, loading, emptyHint, meId }: ChatViewProps) {
  const meEmail: string | undefined = undefined;

  const [text, setText] = React.useState("");
  const [sending, setSending] = React.useState(false);
  const bottomRef = React.useRef<HTMLDivElement>(null);

  const ordered = React.useMemo(() => {
    return [...rows].sort((a, b) => {
      const ta = new Date(String(pick(a, TIME_KEYS) ?? 0)).getTime() || 0;
      const tb = new Date(String(pick(b, TIME_KEYS) ?? 0)).getTime() || 0;
      return ta - tb;
    });
  }, [rows]);

  React.useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [ordered.length]);

  const isMine = React.useCallback(
    (row: Row): boolean => {
      const a = pick(row, AUTHOR_KEYS);
      if (a == null) return false;
      const s = String(a);
      return (
        (meId != null && s === String(meId)) ||
        (meEmail != null && s === meEmail)
      );
    },
    [meId, meEmail],
  );

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const t = text.trim();
    if (!t || sending) return;
    setSending(true);
    setText("");
    try {
      await onSend(t);
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="flex h-[72vh] min-h-0 flex-col overflow-hidden rounded-xl border bg-card">
      <div className="min-h-0 flex-1 space-y-2 overflow-y-auto px-4 py-4">
        {ordered.length === 0 && (
          <p className="pt-12 text-center text-sm text-muted-foreground">
            {emptyHint ?? "Сообщений пока нет. Напишите первое."}
          </p>
        )}
        {ordered.map((row, i) => {
          const mine = isMine(row);
          const author = pick(row, NAME_KEYS) ?? pick(row, AUTHOR_KEYS);
          const body = pick(row, CHAT_TEXT_KEYS);
          const t = pick(row, TIME_KEYS);
          return (
            <div
              key={String((row as { id?: unknown }).id ?? i)}
              className={cn("flex", mine ? "justify-end" : "justify-start")}
            >
              <div
                className={cn(
                  "max-w-[78%] rounded-2xl px-3 py-2 text-sm shadow-sm",
                  mine
                    ? "bg-primary text-primary-foreground"
                    : "bg-muted text-foreground",
                )}
              >
                {!mine && author ? (
                  <div className="mb-0.5 text-[11px] font-medium opacity-70">
                    {String(author)}
                  </div>
                ) : null}
                <div className="whitespace-pre-wrap break-words">
                  {body == null ? (
                    <span className="opacity-50">—</span>
                  ) : (
                    String(body)
                  )}
                </div>
                {t ? (
                  <div className="mt-0.5 text-right text-[10px] opacity-60">
                    {fmtTime(t)}
                  </div>
                ) : null}
              </div>
            </div>
          );
        })}
        <div ref={bottomRef} />
      </div>

      <form
        onSubmit={submit}
        className="flex items-center gap-2 border-t bg-background/60 p-3"
      >
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Сообщение…"
          disabled={loading}
          className="min-w-0 flex-1 rounded-full border bg-background px-4 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
        <Button
          type="submit"
          disabled={sending || !text.trim()}
          className="rounded-full px-5"
        >
          {sending ? "…" : "Отправить"}
        </Button>
      </form>
    </div>
  );
}
