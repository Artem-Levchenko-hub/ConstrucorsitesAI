import { apiFetch } from "./client";
import { USE_MOCKS } from "./mocks";

type QuestionAnswer = {
  answered: true;
  user_message_id: string;
  assistant_message_id: string;
  answer: string;
};

export async function askProjectChatQuestion(
  projectId: string,
  prompt: string,
  idempotencyKey: string,
): Promise<QuestionAnswer | { answered: false }> {
  if (USE_MOCKS) return { answered: false };
  const result = await apiFetch<QuestionAnswer | { answered: false } | null>(
    `/api/projects/${projectId}/chat/question`,
    {
      method: "POST",
      timeoutMs: 30_000,
      json: { prompt, idempotency_key: idempotencyKey },
    },
  );
  if (result?.answered === false) return result;
  if (result?.answered !== true
    || typeof result.user_message_id !== "string" || !result.user_message_id
    || typeof result.assistant_message_id !== "string" || !result.assistant_message_id
    || result.user_message_id === result.assistant_message_id
    || typeof result.answer !== "string" || !result.answer.trim()) {
    throw new Error("Сервер не подтвердил ответ в чате");
  }
  return result;
}
