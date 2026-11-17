import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import { ChatMessage } from "@/components/workspace/ChatMessage";
import type { Message } from "@/lib/api/types";

vi.mock("@/components/workspace/AgentTranscript", () => ({ AgentTranscript: () => null }));

it.each(["compile", "incomplete"])("keeps %s diagnostics behind a calm explicit action", async category => {
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  const onFix = vi.fn();
  const message: Message = {
    id: "message", project_id: "project", role: "assistant", snapshot_id: null,
    content: `<app-error category="${category}" title="Ошибка проверки" file="src/app/page.tsx" fixable="1">TS9000 exact reason</app-error>`,
    model_id: null, tokens_in: 0, tokens_out: 0, created_at: "2026-11-17T11:00:00Z",
  };
  await act(async () => root.render(<ChatMessage message={message} onFix={onFix} />));
  const card = host.querySelector('[role="status"]')!;
  expect(card).not.toBeNull();
  expect(card.outerHTML).not.toMatch(/(?:border|bg|text)-red-|danger/);
  expect(card.textContent).toContain("Продолжим подготовку");
  expect(card.textContent).not.toContain("Ошибка проверки");
  expect(card.textContent).not.toContain("TS9000");
  expect(onFix).not.toHaveBeenCalled();

  const details = [...card.querySelectorAll("button")].find(button => button.textContent === "Детали")!;
  await act(async () => details.click());
  expect(details.getAttribute("aria-expanded")).toBe("true");
  expect(card.textContent).toContain("Ошибка проверки");
  expect(card.textContent).toContain("src/app/page.tsx");
  expect(card.textContent).toContain("TS9000 exact reason");
  const next = [...card.querySelectorAll("button")].find(button => button.textContent?.startsWith("Продолжить"))!;
  await act(async () => next.click());
  expect(onFix).toHaveBeenCalledOnce();
  if (category === "incomplete") expect(onFix).toHaveBeenCalledWith("продолжи");
  else expect(onFix.mock.calls[0][0]).toContain("TS9000 exact reason");
  await act(async () => root.unmount());
  host.remove();
});
