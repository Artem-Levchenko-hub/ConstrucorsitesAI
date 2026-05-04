"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useRef } from "react";
import { simulatePromptStream } from "@/lib/ws-mock";
import type { Message, Snapshot, WalletState, WsEvent } from "@/lib/api/types";
import { sendPrompt } from "@/lib/api/messages";

export function usePromptStream(projectId: string, projectSlug: string) {
  const qc = useQueryClient();
  const cancelRef = useRef<(() => void) | null>(null);
  const streamingRef = useRef(false);

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
        return;
      }

      if (event.type === "snapshot.created") {
        qc.setQueryData<Snapshot[]>(["snapshots", projectId], (prev) => [
          event.data.snapshot,
          ...(prev ?? []),
        ]);
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
        // Re-sync charges list from mock truth.
        qc.invalidateQueries({ queryKey: ["wallet"] });
        return;
      }

      if (event.type === "llm.error") {
        qc.setQueryData<Message[]>(["messages", projectId], (prev) =>
          (prev ?? []).map((m) =>
            m.id === event.data.message_id
              ? { ...m, content: `[Ошибка: ${event.data.error}]` }
              : m,
          ),
        );
        streamingRef.current = false;
      }
    },
    [qc, projectId],
  );

  const submit = useCallback(
    async (promptText: string, modelId: string) => {
      if (streamingRef.current) return;
      streamingRef.current = true;

      const { message_id } = await sendPrompt(projectId, promptText, modelId);

      // Refetch messages so the new user + empty assistant rows appear.
      await qc.invalidateQueries({ queryKey: ["messages", projectId] });

      cancelRef.current?.();
      cancelRef.current = simulatePromptStream({
        projectId,
        projectSlug,
        promptText,
        modelId,
        assistantMessageId: message_id,
        emit: apply,
      });

      // Streaming flag stays true until llm.done lands; flip it then.
      const unsub = qc.getQueryCache().subscribe((evt) => {
        if (
          evt.type === "updated" &&
          evt.query.queryKey[0] === "messages" &&
          (evt.query.queryKey[1] as string) === projectId
        ) {
          const data = evt.query.state.data as Message[] | undefined;
          const m = data?.find((x) => x.id === message_id);
          if (m && m.tokens_out !== null) {
            streamingRef.current = false;
            unsub();
          }
        }
      });
    },
    [projectId, projectSlug, qc, apply],
  );

  const cancel = useCallback(() => {
    cancelRef.current?.();
    cancelRef.current = null;
    streamingRef.current = false;
  }, []);

  return { submit, cancel };
}
