"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useRef, useState } from "react";
import { simulatePromptStream } from "@/lib/ws-mock";
import type { Message, Snapshot, WalletState, WsEvent } from "@/lib/api/types";
import { sendPrompt } from "@/lib/api/messages";
import { USE_MOCKS } from "@/lib/api/mocks";
import { useWorkspaceStore } from "@/store/workspace";

/**
 * Opens a real WebSocket to /api/ws/projects/:id and routes server events
 * through `apply`. Returns a cancel function that closes the socket.
 *
 * The session JWT cookie travels automatically on the WS handshake (browsers
 * include cookies). No extra auth wiring needed here.
 */
function openRealStream(
  projectId: string,
  apply: (event: WsEvent) => void,
): () => void {
  const wsBase =
    process.env.NEXT_PUBLIC_WS_URL ??
    (typeof window !== "undefined"
      ? `wss://${window.location.host}`
      : "wss://constructor.lead-generator.ru");
  const ws = new WebSocket(`${wsBase}/api/ws/projects/${projectId}`);

  ws.onmessage = (ev) => {
    try {
      apply(JSON.parse(ev.data) as WsEvent);
    } catch {
      // ignore malformed frames
    }
  };

  // Keep-alive ping — many proxies (and our nginx) close idle WS at 60s.
  const pingInt = setInterval(() => {
    if (ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "ping" }));
    }
  }, 25_000);

  return () => {
    clearInterval(pingInt);
    if (
      ws.readyState === WebSocket.OPEN ||
      ws.readyState === WebSocket.CONNECTING
    ) {
      ws.close();
    }
  };
}

export function usePromptStream(projectId: string, projectSlug: string) {
  const qc = useQueryClient();
  const cancelRef = useRef<(() => void) | null>(null);
  const streamingRef = useRef(false);
  // Очередь — один слот. Если юзер кидает второй промпт, пока стримит первый,
  // он сюда попадает и автоматом стартует на llm.done/llm.error. Если новый
  // промпт прилетает поверх — заменяет предыдущий (предсказуемее, чем стек).
  const pendingRef = useRef<{ text: string; modelId: string } | null>(null);
  const [pendingPrompt, setPendingPrompt] = useState<string | null>(null);
  // submitRef нужен, потому что fireQueued вызывается из apply (стабильный
  // useCallback), а submit пересоздаётся при каждом рендере с свежим apply —
  // прямая ссылка на submit замкнётся на устаревшую версию.
  const submitRef = useRef<((text: string, modelId: string) => void) | null>(
    null,
  );
  const selectSnapshot = useWorkspaceStore((s) => s.selectSnapshot);

  const fireQueued = useCallback(() => {
    const p = pendingRef.current;
    if (!p) return;
    pendingRef.current = null;
    setPendingPrompt(null);
    // setTimeout, чтобы не вызвать submit изнутри обработчика событий react-query
    // и дать React закоммитить текущий рендер.
    setTimeout(() => submitRef.current?.(p.text, p.modelId), 0);
  }, []);

  const apply = useCallback(
    (event: WsEvent) => {
      if (event.type === "llm.chunk") {
        qc.setQueryData<Message[]>(["messages", projectId], (prev) =>
          (prev ?? []).map((m) =>
            m.id === event.data.message_id
              ? { ...m, content: m.content + event.data.delta }
              : m,
          ),
        );
        return;
      }

      if (event.type === "llm.done") {
        qc.setQueryData<Message[]>(["messages", projectId], (prev) =>
          (prev ?? []).map((m) =>
            m.id === event.data.message_id
              ? {
                  ...m,
                  tokens_in: event.data.tokens_in,
                  tokens_out: event.data.tokens_out,
                }
              : m,
          ),
        );
        // Real backend: re-fetch messages and snapshots to capture
        // server-side state we may have missed during streaming.
        if (!USE_MOCKS) {
          qc.invalidateQueries({ queryKey: ["messages", projectId] });
          qc.invalidateQueries({ queryKey: ["snapshots", projectId] });
          qc.invalidateQueries({ queryKey: ["wallet"] });
        }
        streamingRef.current = false;
        fireQueued();
        return;
      }

      if (event.type === "snapshot.created") {
        qc.setQueryData<Snapshot[]>(["snapshots", projectId], (prev) => [
          event.data.snapshot,
          ...(prev ?? []),
        ]);
        // Hot-reload: jump the iframe to the freshly-created HEAD so the
        // user sees their generated site immediately without manually
        // clicking the new card in the timeline. `null` = "show HEAD",
        // which PreviewFrame resolves to snapshots[0].
        selectSnapshot(null);
        return;
      }

      if (event.type === "preview.ready") {
        qc.setQueryData<Snapshot[]>(["snapshots", projectId], (prev) =>
          (prev ?? []).map((s) =>
            s.id === event.data.snapshot_id
              ? { ...s, preview_url: event.data.preview_url }
              : s,
          ),
        );
        return;
      }

      if (event.type === "wallet.updated") {
        qc.setQueryData<WalletState>(["wallet"], (prev) => ({
          balance_rub: event.data.balance_rub,
          recent_charges: prev?.recent_charges ?? [],
        }));
        qc.invalidateQueries({ queryKey: ["wallet"] });
        return;
      }

      if (event.type === "llm.error") {
        qc.setQueryData<Message[]>(["messages", projectId], (prev) =>
          (prev ?? []).map((m) =>
            m.id === event.data.message_id
              ? {
                  ...m,
                  content: `[Ошибка: ${event.data.error}]`,
                  // Без tokens_out !== null ChatPanel считает сообщение
                  // всё ещё стримящимся — UI не разлочивается.
                  tokens_out: m.tokens_out ?? 0,
                  tokens_in: m.tokens_in ?? 0,
                }
              : m,
          ),
        );
        streamingRef.current = false;
        fireQueued();
      }
    },
    [qc, projectId, fireQueued, selectSnapshot],
  );

  const submit = useCallback(
    async (promptText: string, modelId: string) => {
      // Стрим в процессе — кладём в очередь (один слот, новый замещает старый).
      if (streamingRef.current) {
        pendingRef.current = { text: promptText, modelId };
        setPendingPrompt(promptText);
        return;
      }
      streamingRef.current = true;

      // Real prod path: open WS BEFORE POST so we don't race the first chunk.
      if (!USE_MOCKS) {
        cancelRef.current?.();
        cancelRef.current = openRealStream(projectId, apply);
      }

      const { message_id } = await sendPrompt(projectId, promptText, modelId);

      // New user + assistant rows are written by the api — refetch to display.
      await qc.invalidateQueries({ queryKey: ["messages", projectId] });

      if (USE_MOCKS) {
        cancelRef.current?.();
        cancelRef.current = simulatePromptStream({
          projectId,
          projectSlug,
          promptText,
          modelId,
          assistantMessageId: message_id,
          emit: apply,
        });
      }

      // Mocks set tokens_out on the assistant row when done — keep the
      // existing waiter so the streamingRef releases on llm.done.
      const unsub = qc.getQueryCache().subscribe((evt) => {
        if (
          evt.type === "updated" &&
          evt.query.queryKey[0] === "messages" &&
          (evt.query.queryKey[1] as string) === projectId
        ) {
          const data = evt.query.state.data as Message[] | undefined;
          const m = data?.find((x) => x.id === message_id);
          if (m && m.tokens_out !== null && m.tokens_out !== undefined) {
            streamingRef.current = false;
            unsub();
          }
        }
      });
    },
    [projectId, projectSlug, qc, apply],
  );

  // Сохраняем актуальный submit в ref, чтобы fireQueued мог его вызвать,
  // не замыкаясь на устаревшую версию (useCallback пересоздаётся каждый рендер).
  submitRef.current = submit;

  const cancel = useCallback(() => {
    // 1) Рвём WS — фронт перестаёт получать чанки.
    cancelRef.current?.();
    cancelRef.current = null;
    streamingRef.current = false;

    // 2) Помечаем последнее ассистентское сообщение завершённым, чтобы
    //    ChatPanel.isStreaming (читает tokens_out из кэша) сразу разблокировался.
    //    Бэкенд может ещё какое-то время дописывать в БД — это TODO для
    //    отдельного /messages/:id/cancel-эндпоинта; пока best-effort на фронте.
    qc.setQueryData<Message[]>(["messages", projectId], (prev) =>
      (prev ?? []).map((m, i, arr) =>
        i === arr.length - 1 && m.role === "assistant" && m.tokens_out === null
          ? {
              ...m,
              content: m.content + "\n\n[Отменено пользователем]",
              tokens_out: m.tokens_out ?? 0,
              tokens_in: m.tokens_in ?? 0,
            }
          : m,
      ),
    );

    // 3) Сбрасываем очередь — Стоп = «всё, прекратить».
    pendingRef.current = null;
    setPendingPrompt(null);
  }, [qc, projectId]);

  const cancelPending = useCallback(() => {
    pendingRef.current = null;
    setPendingPrompt(null);
  }, []);

  return { submit, cancel, cancelPending, pendingPrompt };
}
