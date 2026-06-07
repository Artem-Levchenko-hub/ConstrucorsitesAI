"use client";

import { useState } from "react";
import {
  Bot,
  User as UserIcon,
  FileCode2,
  PencilLine,
  ChevronRight,
  Loader2,
} from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import type { Message } from "@/lib/api/types";
import { EASE_OUT, fadeUp } from "@/lib/motion";
import { formatRelativeTime, cn } from "@/lib/utils";
import {
  parseAssistantContent,
  formatBytes,
  type AssistantPart,
} from "@/lib/parse-assistant";
import { SelectedChips } from "./SelectedChips";
import { PassProgressBar } from "./PassProgressBar";

export function ChatMessage({
  message,
  streaming,
  projectId,
}: {
  message: Message;
  streaming?: boolean;
  /**
   * Required for the B.3 multipass progress bar — it subscribes to
   * `["passes", projectId, message.id]` in React Query cache. Optional
   * so callers can omit it for non-streaming chat replays / e2e
   * screenshots, in which case the progress bar simply never renders.
   */
  projectId?: string;
}) {
  const isUser = message.role === "user";
  const parts: AssistantPart[] = isUser
    ? [{ kind: "text", text: message.content }]
    : parseAssistantContent(message.content);

  return (
    <motion.div
      variants={fadeUp}
      initial="hidden"
      animate="visible"
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
        </div>

        <div className="text-sm text-fg-primary leading-6 space-y-1.5">
          {!isUser && streaming && projectId && (
            <PassProgressBar projectId={projectId} messageId={message.id} />
          )}

          {parts.map((p, i) =>
            p.kind === "text" ? (
              <div
                key={i}
                className="whitespace-pre-wrap break-words"
              >
                {p.text}
              </div>
            ) : (
              <FileChip
                key={i}
                path={p.path}
                body={p.body}
                closed={p.closed}
                variant={p.kind}
              />
            ),
          )}

          {streaming &&
            (parts.length === 0 ||
              parts[parts.length - 1].kind === "text") && (
              <span className="inline-block w-[6px] h-[14px] -mb-0.5 ml-0.5 bg-accent animate-pulse align-middle" />
            )}
        </div>

        {isUser &&
          message.selected_elements &&
          message.selected_elements.length > 0 && (
            <SelectedChips items={message.selected_elements} className="pt-1" />
          )}

        {!isUser &&
          message.tokens_out !== null &&
          message.tokens_in !== null && (
            <div className="text-[11px] font-mono text-fg-tertiary pt-1 flex items-center gap-2">
              <span>
                ↑ {message.tokens_in} · ↓ {message.tokens_out} tokens
              </span>
              {message.cost_rub != null && message.cost_rub > 0 && (
                <span
                  title="Списано с кошелька за эту генерацию"
                  className="text-fg-tertiary"
                >
                  · ≈ ₽{message.cost_rub.toFixed(2)}
                </span>
              )}
            </div>
          )}
      </div>
    </motion.div>
  );
}

function FileChip({
  path,
  body,
  closed,
  variant = "file",
}: {
  path: string;
  body: string;
  closed: boolean;
  variant?: "file" | "edit";
}) {
  const [open, setOpen] = useState(false);
  const size = new Blob([body]).size;
  const isEdit = variant === "edit";
  const Icon = isEdit ? PencilLine : FileCode2;
  // For an edit the body is a SEARCH/REPLACE diff — its byte size is noise; show
  // a plain "правка" label instead. The diff stays available behind the chevron.
  const meta = isEdit ? "правка" : formatBytes(size);

  return (
    <div className="rounded-md border border-border-subtle bg-surface-raised overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-2 px-2.5 py-1.5 hover:bg-surface-overlay transition-colors"
      >
        <ChevronRight
          className={cn(
            "h-3.5 w-3.5 text-fg-tertiary transition-transform shrink-0",
            open && "rotate-90",
          )}
        />
        <Icon className="h-3.5 w-3.5 text-fg-secondary shrink-0" />
        <span className="font-mono text-xs text-fg-primary truncate">
          {isEdit ? `Правка · ${path}` : path}
        </span>
        <span className="ml-auto flex items-center gap-2 text-[11px] font-mono text-fg-tertiary shrink-0">
          {closed ? (
            meta
          ) : (
            <>
              <Loader2 className="h-3 w-3 animate-spin" />
              <span>{meta}</span>
            </>
          )}
        </span>
      </button>

      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2, ease: EASE_OUT }}
            className="overflow-hidden border-t border-border-subtle"
          >
            <pre className="text-[11px] font-mono text-fg-secondary leading-relaxed p-3 overflow-x-auto max-h-80 overflow-y-auto bg-surface-base scrollbar-elegant">
              {body}
            </pre>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
