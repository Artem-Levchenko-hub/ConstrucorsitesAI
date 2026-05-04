"use client";

import { Bot, User as UserIcon } from "lucide-react";
import { motion } from "framer-motion";
import type { Message } from "@/lib/api/types";
import { formatRelativeTime } from "@/lib/utils";

export function ChatMessage({
  message,
  streaming,
}: {
  message: Message;
  streaming?: boolean;
}) {
  const isUser = message.role === "user";

  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.18 }}
      className="flex gap-3 px-4 py-3"
    >
      <div
        className={
          isUser
            ? "h-7 w-7 rounded-full bg-accent-subtle border border-accent/40 flex items-center justify-center shrink-0"
            : "h-7 w-7 rounded-full bg-surface-overlay border border-border-default flex items-center justify-center shrink-0"
        }
      >
        {isUser ? (
          <UserIcon className="h-3.5 w-3.5 text-accent" />
        ) : (
          <Bot className="h-3.5 w-3.5 text-fg-secondary" />
        )}
      </div>

      <div className="flex-1 min-w-0 space-y-1">
        <div className="flex items-center gap-2 text-xs">
          <span className="font-medium text-fg-primary">
            {isUser ? "Вы" : "Omnia"}
          </span>
          <span className="text-fg-tertiary">
            {formatRelativeTime(message.created_at)}
          </span>
          {message.model_id && !isUser && (
            <span className="font-mono text-fg-tertiary">
              · {message.model_id}
            </span>
          )}
        </div>

        <div className="text-sm text-fg-primary whitespace-pre-wrap break-words leading-6">
          {message.content}
          {streaming && (
            <span className="inline-block w-[6px] h-[14px] -mb-0.5 ml-0.5 bg-accent animate-pulse" />
          )}
        </div>

        {!isUser &&
          message.tokens_out !== null &&
          message.tokens_in !== null && (
            <div className="text-[11px] font-mono text-fg-tertiary pt-1">
              ↑ {message.tokens_in} · ↓ {message.tokens_out} tokens
            </div>
          )}
      </div>
    </motion.div>
  );
}
