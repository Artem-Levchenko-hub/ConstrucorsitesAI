import { afterEach, expect, it } from "vitest";
import { supportTiming, takeSupportRecovery, type SupportTicket } from "@/lib/api/support";

const ticket: SupportTicket = {
  id: "11111111-1111-4111-8111-111111111111", reference: "SUP-42", subject: "Вопрос",
  status: "new", version: 1, requester_email: "guest@example.com",
  created_at: "2026-10-30T07:00:00Z", updated_at: "2026-10-30T07:00:00Z",
  resolved_at: null, archived: false, source_path: "/max", assigned_admin_email: null,
  message_count: 1, last_message_preview: "Вопрос", first_response_due_at: "2026-10-30T16:00:00Z",
};
afterEach(() => window.history.replaceState({}, "", "/max"));

it("shows the Moscow deadline and remaining time before a human reply", () => {
  const timing = supportTiming(ticket, Date.parse("2026-10-30T15:00:00Z"));
  expect(timing).toContain("19:00");
  expect(timing).toContain("МСК · Осталось 1 ч 0 мин");
});
it("shows elapsed overdue time independently from ticket status", () => {
  expect(supportTiming({ ...ticket, status: "done" }, Date.parse("2026-10-30T17:30:00Z")))
    .toContain("Просрочено на 1 ч 30 мин");
});
it("stops the clock after a human reply without inventing a legacy promise", () => {
  expect(supportTiming({ ...ticket, first_responded_at: "2026-10-30T15:00:00Z" }, Date.parse("2026-10-30T17:30:00Z")))
    .toContain("Первый ответ оператора:");
  expect(supportTiming({ ...ticket, first_response_due_at: null }, Date.parse("2026-10-30T17:30:00Z"))).toContain("не установлен");
});
it("rejects malformed recovery capabilities and clears them before opening a thread", () => {
  window.history.replaceState({}, "", `/max?keep=yes#support=${ticket.id}&token=guessed`);
  expect(takeSupportRecovery()).toBeNull();
  expect(window.location.hash).toBe("");
  expect(window.location.search).toBe("?keep=yes");
});
