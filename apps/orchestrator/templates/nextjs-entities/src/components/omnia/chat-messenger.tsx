"use client";

/**
 * ChatMessenger — a REAL messenger UX over the entity engine: a rooms sidebar +
 * a live conversation pane (bubbles + composer), not two CRUD tables. FIXED kit
 * component. Use it for a chat/messenger app where one entity is the ROOMS
 * (conversations) and another is the MESSAGES referencing a room.
 *
 *   <ChatMessenger roomsEntity="Chat" messagesEntity="Message"
 *                  linkField="chatId" textField="text" titleField="name" />
 *
 * Reuses <ChatView> for the conversation; messages are filtered to the selected
 * room client-side (so it needs no server-side filter support) and polled for
 * near-real-time. Sending creates a message linked to the active room.
 */

import * as React from "react";

import { cn } from "@/lib/utils";
import { type Row } from "@/lib/sdk";

import { ChatView } from "./chat-view";
import { useEntity } from "./use-entity";

export interface ChatMessengerProps {
  roomsEntity: string;
  messagesEntity: string;
  /** Message field that references the room (e.g. "chatId"). */
  linkField: string;
  /** Message text field (default "text"). */
  textField?: string;
  /** Room title field (default "name"). */
  titleField?: string;
}

export function ChatMessenger({
  roomsEntity,
  messagesEntity,
  linkField,
  textField = "text",
  titleField = "name",
}: ChatMessengerProps) {
  const rooms = useEntity(roomsEntity);
  const messages = useEntity(messagesEntity);
  const [activeId, setActiveId] = React.useState<string | null>(null);
  const [creating, setCreating] = React.useState(false);

  // Auto-select the first room.
  React.useEffect(() => {
    if (!activeId && rooms.rows.length) {
      setActiveId(String((rooms.rows[0] as { id?: unknown }).id ?? ""));
    }
  }, [rooms.rows, activeId]);

  // Near-real-time: poll messages (SSE arrives with the realtime stack).
  React.useEffect(() => {
    const t = setInterval(() => {
      void messages.reload();
    }, 3000);
    return () => clearInterval(t);
  }, [messages.reload]);

  const roomMessages = React.useMemo(
    () =>
      messages.rows.filter(
        (m) =>
          String((m as Record<string, unknown>)[linkField] ?? "") ===
          String(activeId ?? ""),
      ),
    [messages.rows, linkField, activeId],
  );

  async function send(text: string) {
    if (!activeId) return;
    await messages.create({ [linkField]: activeId, [textField]: text });
  }

  async function createRoom() {
    setCreating(true);
    try {
      const room = await rooms.create({ [titleField]: "Новая беседа" });
      const id = String((room as { id?: unknown }).id ?? "");
      if (id) setActiveId(id);
    } finally {
      setCreating(false);
    }
  }

  const roomTitle = (r: Row) =>
    String((r as Record<string, unknown>)[titleField] ?? "Беседа");

  return (
    <div className="flex h-[72vh] min-h-0 gap-4">
      <aside className="flex w-72 shrink-0 flex-col overflow-hidden rounded-2xl border border-border/70 bg-card">
        <div className="flex items-center justify-between border-b border-border/70 px-4 py-3">
          <span className="text-sm font-semibold">Беседы</span>
          <button
            type="button"
            onClick={createRoom}
            disabled={creating}
            className="rounded-md bg-primary px-2.5 py-1 text-xs font-medium text-primary-foreground transition hover:opacity-90 disabled:opacity-60"
          >
            + Новая
          </button>
        </div>
        <nav className="min-h-0 flex-1 overflow-y-auto p-2">
          {rooms.rows.length === 0 && (
            <p className="px-2 py-4 text-sm text-muted-foreground">
              Бесед пока нет.
            </p>
          )}
          {rooms.rows.map((r) => {
            const id = String((r as { id?: unknown }).id ?? "");
            return (
              <button
                key={id}
                onClick={() => setActiveId(id)}
                className={cn(
                  "mb-1 w-full truncate rounded-lg px-3 py-2 text-left text-sm transition",
                  id === activeId
                    ? "bg-primary/10 font-medium text-primary"
                    : "hover:bg-muted",
                )}
              >
                {roomTitle(r)}
              </button>
            );
          })}
        </nav>
      </aside>

      <div className="min-w-0 flex-1">
        {activeId ? (
          <ChatView
            rows={roomMessages}
            onSend={send}
            loading={messages.loading}
            emptyHint="Напишите первое сообщение."
          />
        ) : (
          <div className="flex h-full items-center justify-center rounded-2xl border border-border/70 text-sm text-muted-foreground">
            Выберите беседу слева
          </div>
        )}
      </div>
    </div>
  );
}
