import { expect, it } from "vitest";
import { isChatMessageStreaming } from "@/lib/chat-message-status";
it("uses terminal server status instead of treating a cancelled response as ongoing forever", () => {
  expect(isChatMessageStreaming({ role: "assistant", tokens_out: null, generation_status: "cancelled" })).toBe(false);
  expect(isChatMessageStreaming({ role: "assistant", tokens_out: null, generation_status: "failed" })).toBe(false);
  expect(isChatMessageStreaming({ role: "assistant", tokens_out: null, generation_status: "completed" })).toBe(false);
  expect(isChatMessageStreaming({ role: "assistant", tokens_out: null, generation_status: null })).toBe(true);
  expect(isChatMessageStreaming({ role: "assistant", tokens_out: null, generation_status: "queued_for_capacity" })).toBe(true);
  expect(isChatMessageStreaming({ role: "user", tokens_out: null })).toBe(false);
});

it("does not reactivate a completed response when an immediate refetch still says running", () => {
  expect(isChatMessageStreaming({ role: "assistant", tokens_out: null, generation_status: "running" })).toBe(true);
  expect(isChatMessageStreaming({ role: "assistant", tokens_out: 0, generation_status: "completed" })).toBe(false);
  expect(isChatMessageStreaming({ role: "assistant", tokens_out: 0, generation_status: "running" })).toBe(false);
});
