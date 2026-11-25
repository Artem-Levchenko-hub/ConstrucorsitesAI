import type { Message } from "@/lib/api/types";

export function lastPromptMessage<T extends Pick<Message, "chat_question">>(messages?: readonly T[]) {
  return messages?.findLast(message => !message.chat_question);
}

export function isChatMessageStreaming(message?: Pick<Message, "role" | "tokens_out" | "generation_status" | "chat_question">) {
  if (message?.role !== "assistant" || message.chat_question) return false;
  if (message.generation_status && ["completed", "cancelled", "failed"].includes(message.generation_status)) return false;
  // Usage is persisted before the durable run releases its lease. A refetch in
  // that gap may still say "running"; never reactivate an already finished reply.
  return message.tokens_out === null;
}
