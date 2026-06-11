"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { simulatePromptStream } from "@/lib/ws-mock";
import type {
  Message,
  PassProgress,
  SelectedElement,
  Snapshot,
  WalletState,
  WsEvent,
} from "@/lib/api/types";
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
type StreamHandle = {
  /** Intentional close — suppresses the auto-reconnect (onclose is nulled). */
  cancel: () => void;
  /** Send a client→server control frame (e.g. `{type:"resync"}`). No-op if closed. */
  send: (msg: unknown) => void;
};

/**
 * Opens a real WebSocket to /api/ws/projects/:id and routes server events
 * through `apply`. Returns a handle to close the socket and to send control
 * frames (resync). The session JWT cookie travels automatically on the WS
 * handshake (browsers include cookies). No extra auth wiring needed here.
 *
 * `onOpen`/`onClose` let the caller drive bounded auto-reconnect: an
 * UNEXPECTED drop (nginx, flaky network) while a generation is still in flight
 * should reconnect; an intentional close (cancel) must not — so `cancel` nulls
 * `onclose` before closing.
 */
function openRealStream(
  projectId: string,
  apply: (event: WsEvent) => void,
  opts?: { onOpen?: () => void; onClose?: () => void },
): StreamHandle {
  const wsBase =
    process.env.NEXT_PUBLIC_WS_URL ??
    (typeof window !== "undefined"
      ? `wss://${window.location.host}`
      : "wss://constructor.lead-generator.ru");
  const ws = new WebSocket(`${wsBase}/api/ws/projects/${projectId}`);

  // Keep-alive ping — many proxies (and our nginx) close idle WS at 60s.
  const pingInt = setInterval(() => {
    if (ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "ping" }));
    }
  }, 25_000);

  ws.onopen = () => opts?.onOpen?.();
  ws.onmessage = (ev) => {
    try {
      apply(JSON.parse(ev.data) as WsEvent);
    } catch {
      // ignore malformed frames
    }
  };
  ws.onclose = () => {
    clearInterval(pingInt);
    opts?.onClose?.();
  };

  const send = (msg: unknown) => {
    if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg));
  };

  const cancel = () => {
    clearInterval(pingInt);
    // Null onclose so an intentional close never triggers the reconnect path.
    ws.onclose = null;
    if (
      ws.readyState === WebSocket.OPEN ||
      ws.readyState === WebSocket.CONNECTING
    ) {
      ws.close();
    }
  };

  return { cancel, send };
}

export function usePromptStream(projectId: string, projectSlug: string) {
  const qc = useQueryClient();
  const cancelRef = useRef<(() => void) | null>(null);
  const streamingRef = useRef(false);
  // Resumable-stream bookkeeping. `sendRef` lets `apply` ask the server to
  // replay the buffer (resync) when it spots a seq-gap. `streamMetaRef` holds
  // per-message {lastSeq, resyncing} so we dedup buffered deltas and drop live
  // ones while a resync is pending. `connectRef` breaks the self-reference in
  // the reconnect closure; `reconnectAttemptsRef` bounds backoff retries.
  const sendRef = useRef<((msg: unknown) => void) | null>(null);
  const reconnectAttemptsRef = useRef(0);
  const connectRef = useRef<() => void>(() => {});
  const streamMetaRef = useRef<
    Record<string, { lastSeq: number; resyncing: boolean }>
  >({});
  // Очередь — один слот. Если юзер кидает второй промпт, пока стримит первый,
  // он сюда попадает и автоматом стартует на llm.done/llm.error. Если новый
  // промпт прилетает поверх — заменяет предыдущий (предсказуемее, чем стек).
  const pendingRef = useRef<{
    text: string;
    modelId: string;
    selections?: SelectedElement[];
    opts?: { skipClarify?: boolean };
  } | null>(null);
  const [pendingPrompt, setPendingPrompt] = useState<string | null>(null);
  // submitRef нужен, потому что fireQueued вызывается из apply (стабильный
  // useCallback), а submit пересоздаётся при каждом рендере с свежим apply —
  // прямая ссылка на submit замкнётся на устаревшую версию.
  const submitRef = useRef<
    | ((
        text: string,
        modelId: string,
        selections?: SelectedElement[],
        opts?: { skipClarify?: boolean },
      ) => void)
    | null
  >(null);
  const selectSnapshot = useWorkspaceStore((s) => s.selectSnapshot);

  const fireQueued = useCallback(() => {
    const p = pendingRef.current;
    if (!p) return;
    pendingRef.current = null;
    setPendingPrompt(null);
    // setTimeout, чтобы не вызвать submit изнутри обработчика событий react-query
    // и дать React закоммитить текущий рендер.
    setTimeout(
      () => submitRef.current?.(p.text, p.modelId, p.selections, p.opts),
      0,
    );
  }, []);

  const apply = useCallback(
    (event: WsEvent) => {
      if (event.type === "stream.sync") {
        // (Re)connect replay: replace the in-flight message's content with the
        // server's cumulative buffer and reset our seq cursor. This is what
        // unfreezes the preview after a page refresh mid-build.
        const mid = event.data.message_id;
        qc.setQueryData<Message[]>(["messages", projectId], (prev) =>
          (prev ?? []).map((m) =>
            m.id === mid ? { ...m, content: event.data.content } : m,
          ),
        );
        streamMetaRef.current[mid] = {
          lastSeq: event.data.seq,
          resyncing: false,
        };
        return;
      }

      if (event.type === "llm.chunk") {
        const mid = event.data.message_id;
        const seq = event.data.seq;
        if (typeof seq === "number") {
          const meta = streamMetaRef.current[mid] ?? {
            lastSeq: 0,
            resyncing: false,
          };
          // Waiting on a resync after a detected gap — drop live deltas; the
          // pending stream.sync carries the full content that supersedes them.
          if (meta.resyncing) return;
          // Already applied (was included in a prior sync buffer).
          if (seq <= meta.lastSeq) return;
          if (seq > meta.lastSeq + 1) {
            // Missed delta(s): appending now would leave a hole in the HTML.
            // Ask the server to replay the full buffer; ignore live deltas
            // until it arrives. Self-heals reconnect-window gaps.
            meta.resyncing = true;
            streamMetaRef.current[mid] = meta;
            sendRef.current?.({ type: "resync" });
            return;
          }
          meta.lastSeq = seq;
          streamMetaRef.current[mid] = meta;
        }
        qc.setQueryData<Message[]>(["messages", projectId], (prev) =>
          (prev ?? []).map((m) =>
            m.id === mid ? { ...m, content: m.content + event.data.delta } : m,
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
                  // Client-side annotation: not persisted in the DB row, but
                  // ChatMessage.tsx surfaces it as "≈ ₽X" so the user sees
                  // approximate per-prompt cost without opening the wallet.
                  cost_rub: event.data.cost_rub ?? null,
                }
              : m,
          ),
        );
        // B.3 — done implies all multipass stages finished, drop the
        // progress entry so the bar disappears at the same instant the
        // tokens/cost line appears. Removing instead of clearing keeps
        // the cache lean for long sessions.
        qc.removeQueries({
          queryKey: ["passes", projectId, event.data.message_id],
        });
        qc.removeQueries({
          queryKey: ["stream-images", projectId, event.data.message_id],
        });
        qc.removeQueries({
          queryKey: ["turn-mode", projectId, event.data.message_id],
        });
        // Real backend: re-fetch messages and snapshots to capture
        // server-side state we may have missed during streaming.
        if (!USE_MOCKS) {
          qc.invalidateQueries({ queryKey: ["messages", projectId] });
          qc.invalidateQueries({ queryKey: ["snapshots", projectId] });
          qc.invalidateQueries({ queryKey: ["wallet"] });
          // A build can auto-route the stack (static → nextjs_entities), which
          // changes `template`. The workspace preview reads it reactively from
          // this cache, so refresh it here to flip to the live-container path
          // without a manual reload.
          qc.invalidateQueries({ queryKey: ["project", projectId] });
        }
        delete streamMetaRef.current[event.data.message_id];
        streamingRef.current = false;
        fireQueued();
        return;
      }

      if (event.type === "llm.pass") {
        // B.3 — multipass progress. Stage "start" sets `current`; stage
        // "end" pushes the stage into `completed` and clears `current` so
        // the UI shows "done" between passes (the next `start` re-fills
        // `current` immediately). We dedupe `completed` because the
        // backend re-emits `end` on retries.
        qc.setQueryData<PassProgress>(
          ["passes", projectId, event.data.message_id],
          (prev) => {
            const base: PassProgress = prev ?? {
              current: null,
              currentModel: null,
              completed: [],
            };
            const stageName = event.data.pass;
            if (event.data.stage === "start") {
              // Carry the working model (backend sends it on freeform `start`)
              // so the build UI can narrate "<model> · <stage>".
              return {
                ...base,
                current: stageName,
                currentModel: event.data.model ?? null,
              };
            }
            // stage === "end" — clear current (and its model) when this is the
            // active stage; the next `start` re-fills both immediately.
            const completed = base.completed.includes(stageName)
              ? base.completed
              : [...base.completed, stageName];
            const ending = base.current === stageName;
            return {
              current: ending ? null : base.current,
              currentModel: ending ? null : base.currentModel,
              completed,
            };
          },
        );
        return;
      }

      if (event.type === "image.resolved") {
        // Live drop-in: accumulate resolved images for this message so the
        // streaming preview can swap each into its frame. Client-only cache
        // (no fetch), read by StreamingPreviewFrame via useQuery+enabled:false.
        // De-dup by idx so a re-emit doesn't replay the settle animation twice.
        qc.setQueryData<{ idx: number; url: string }[]>(
          ["stream-images", projectId, event.data.message_id],
          (prev) => {
            const list = prev ?? [];
            if (list.some((i) => i.idx === event.data.idx)) return list;
            return [...list, { idx: event.data.idx, url: event.data.url }];
          },
        );
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

      // V2 — orchestrator-originated events flow through the same WS pipe.
      // We don't render logs/progress here yet; RuntimeButton subscribes to
      // ["runtime", projectId] via React Query, so flipping that cache is
      // enough to update the button label + colour the moment the container
      // changes state on the host.
      if (event.type === "runtime.started" || event.type === "runtime.stopped") {
        qc.setQueryData(["runtime", projectId], event.data.runtime);
        qc.invalidateQueries({ queryKey: ["runtime", projectId] });
        return;
      }
      if (event.type === "runtime.crashed") {
        qc.invalidateQueries({ queryKey: ["runtime", projectId] });
        return;
      }
      if (event.type === "deploy.progress" || event.type === "deploy.done") {
        qc.invalidateQueries({ queryKey: ["deploy", projectId] });
        return;
      }
      if (event.type === "deploy.failed") {
        qc.invalidateQueries({ queryKey: ["deploy", projectId] });
        return;
      }

      if (event.type === "app.error") {
        // The card block is already persisted into the assistant message
        // (services/app_errors.py appended it). Refetch so it renders now —
        // single source of truth = the DB row, no cache surgery here. The card
        // can land after llm.done, so this is independent of streaming state.
        qc.invalidateQueries({ queryKey: ["messages", projectId] });
        toast.error(event.data.title, {
          description: "Подробности — в карточке ошибки в чате.",
          duration: 7_000,
        });
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
        // B.3 — drop progress so the bar doesn't outlive the error toast.
        qc.removeQueries({
          queryKey: ["passes", projectId, event.data.message_id],
        });
        // Loud surface so the user notices: silent inline-error in the chat
        // tab was getting overlooked while the preview placeholder kept
        // shimmering — they thought generation was still in progress.
        toast.error("Генерация прервалась", {
          description: event.data.error.slice(0, 240),
          duration: 8_000,
        });
        delete streamMetaRef.current[event.data.message_id];
        streamingRef.current = false;
        fireQueued();
      }
    },
    [qc, projectId, fireQueued, selectSnapshot],
  );

  // Silence watchdog: fires `onSilence` if the message gets no update for
  // WATCHDOG_MS while still streaming (tokens_out === null). Re-arms on every
  // update; self-removes once the message completes. Shared by submit and the
  // reconnect effect so the "stuck spinner" guard works in both paths.
  const watchMessage = useCallback(
    (messageId: string, onSilence: () => void): (() => void) => {
      const WATCHDOG_MS = 180_000;
      let timer = 0;
      let unsub: () => void = () => {};
      const stillStreaming = () => {
        const data = qc.getQueryData<Message[]>(["messages", projectId]);
        const m = data?.find((x) => x.id === messageId);
        return !!m && m.tokens_out === null;
      };
      const fire = () => {
        if (stillStreaming()) {
          unsub();
          onSilence();
        }
      };
      const arm = () => {
        window.clearTimeout(timer);
        timer = window.setTimeout(fire, WATCHDOG_MS);
      };
      arm();
      unsub = qc.getQueryCache().subscribe((evt) => {
        if (
          evt.type === "updated" &&
          evt.query.queryKey[0] === "messages" &&
          (evt.query.queryKey[1] as string) === projectId
        ) {
          const data = evt.query.state.data as Message[] | undefined;
          const m = data?.find((x) => x.id === messageId);
          if (m && m.tokens_out !== null && m.tokens_out !== undefined) {
            window.clearTimeout(timer);
            streamingRef.current = false;
            unsub();
          } else if (m) {
            arm();
          }
        }
      });
      return () => {
        window.clearTimeout(timer);
        unsub();
      };
    },
    [qc, projectId],
  );

  // Opens the real WS and wires bounded auto-reconnect: an unexpected drop
  // while a generation is still in flight reconnects (up to 5×, backing off);
  // the server replays the buffer via stream.sync on each (re)connect. Used by
  // submit (new prompt) and the reconnect effect (page refresh mid-build).
  const connect = useCallback(() => {
    const ctl = openRealStream(projectId, apply, {
      onOpen: () => {
        reconnectAttemptsRef.current = 0;
      },
      onClose: () => {
        // Normal end leaves streamingRef false → no reconnect. Cancel/stop nulls
        // onclose upstream, so this never runs for an intentional close.
        if (!streamingRef.current) return;
        if (reconnectAttemptsRef.current >= 5) return;
        reconnectAttemptsRef.current += 1;
        window.setTimeout(
          () => {
            if (streamingRef.current) connectRef.current();
          },
          Math.min(1000 * reconnectAttemptsRef.current, 5000),
        );
      },
    });
    cancelRef.current = ctl.cancel;
    sendRef.current = ctl.send;
  }, [projectId, apply]);
  // Keep connectRef pointing at the latest `connect` so the onClose reconnect
  // closure can re-invoke it without taking `connect` as its own dependency
  // (which would recreate the socket on every render). Assigned in an effect,
  // not during render. onClose only fires after a socket exists, by which time
  // this has run — so the initial no-op is never the one called.
  useEffect(() => {
    connectRef.current = connect;
  }, [connect]);

  const submit = useCallback(
    async (
      promptText: string,
      modelId: string,
      selections?: SelectedElement[],
      opts?: { skipClarify?: boolean },
    ) => {
      // Стрим в процессе — кладём в очередь (один слот, новый замещает старый).
      // Выделения переносим вместе с текстом, чтобы отложенный промпт сохранил контекст.
      if (streamingRef.current) {
        pendingRef.current = { text: promptText, modelId, selections, opts };
        setPendingPrompt(promptText);
        return;
      }
      streamingRef.current = true;

      // OPTIMISTIC INSERT — show user's prompt and an empty assistant placeholder
      // in the chat INSTANTLY, before the HTTP POST round-trip completes. Without
      // this the UI is silent for the 1–2s the api spends inserting rows + we
      // re-fetch them, which feels like "did my click register?". The temp ids
      // are deterministic prefixes — `last assistant.tokens_out === null` makes
      // ChatPanel.isStreaming flip true immediately, so the Stop button and
      // streaming hint appear without waiting on the network.
      const tempUserId = `__opt_user_${Date.now()}`;
      const tempAssistantId = `__opt_asst_${Date.now()}`;
      qc.setQueryData<Message[]>(["messages", projectId], (prev) => [
        ...(prev ?? []),
        {
          id: tempUserId,
          project_id: projectId,
          role: "user",
          content: promptText,
          model_id: modelId,
          snapshot_id: null,
          tokens_in: 0,
          tokens_out: 0,
          selected_elements: selections ?? null,
          created_at: new Date().toISOString(),
        } as Message,
        {
          id: tempAssistantId,
          project_id: projectId,
          role: "assistant",
          content: "",
          model_id: modelId,
          snapshot_id: null,
          tokens_in: null,
          tokens_out: null,
          created_at: new Date().toISOString(),
        } as Message,
      ]);

      // Real prod path: open WS BEFORE POST so we don't race the first chunk.
      if (!USE_MOCKS) {
        cancelRef.current?.();
        connect();
      }

      // Helper: collapse the optimistic placeholder into an error state so
      // the user gets an explicit message instead of a stuck "AI читает
      // контекст" spinner. Called from both the catch block (POST failed)
      // and the watchdog timer (no WS event for 90s).
      const _failPrompt = (reason: string, detail?: string) => {
        qc.setQueryData<Message[]>(["messages", projectId], (prev) =>
          (prev ?? []).map((m) =>
            m.id === tempAssistantId
              ? {
                  ...m,
                  content: `[Ошибка: ${reason}${detail ? ` — ${detail}` : ""}]`,
                  tokens_out: 0,
                  tokens_in: 0,
                }
              : m,
          ),
        );
        qc.removeQueries({
          queryKey: ["passes", projectId, tempAssistantId],
        });
        streamingRef.current = false;
        cancelRef.current?.();
        cancelRef.current = null;
        toast.error("Генерация не запустилась", {
          description: detail
            ? `${reason}: ${detail.slice(0, 200)}`
            : reason,
          duration: 10_000,
        });
        fireQueued();
      };

      let message_id: string;
      try {
        const resp = await sendPrompt(
          projectId,
          promptText,
          modelId,
          selections,
          opts,
        );
        message_id = resp.message_id;
        // Record how the server will handle this turn ("edit" = surgical, keep
        // the current preview; "build" = full (re)generation). PreviewFrame reads
        // this cache to avoid morphing a diff-stream into a blanked preview.
        qc.setQueryData(
          ["turn-mode", projectId, message_id],
          resp.mode ?? "build",
        );
        // Progressive-discovery quick replies: stash the chip answers for THIS
        // question keyed by its message id. ChatPanel renders them under the
        // streamed question (only while it's the latest message) so the user can
        // tap an answer. Client-only cache — chips are a convenience for the live
        // turn, not persisted; the free-text input is always there as a fallback.
        if (resp.choices && resp.choices.length > 0) {
          qc.setQueryData(["discovery-choices", projectId, message_id], {
            choices: resp.choices,
            allowCustom: resp.allow_custom ?? true,
          });
        }
      } catch (e) {
        // sendPrompt failed BEFORE the backend even spawned _process_prompt
        // — network error, 4xx (wallet_empty, not_found), 5xx, timeout.
        // Surface to user; placeholder turns into an explicit error row.
        const errMsg =
          e instanceof Error ? e.message : "не удалось отправить промпт";
        _failPrompt("POST /prompt не прошёл", errMsg);
        return;
      }

      // Swap the optimistic assistant row for the real id (so incoming
      // `llm.chunk` events for `event.data.message_id` actually find the row).
      // The user row is left as-is — the refetch below replaces both with the
      // canonical server-side records.
      qc.setQueryData<Message[]>(["messages", projectId], (prev) =>
        (prev ?? []).map((m) =>
          m.id === tempAssistantId ? { ...m, id: message_id } : m,
        ),
      );

      // Now refetch in the background to swap optimistic rows for canonical
      // server records (ids, created_at). Stays non-blocking so streaming
      // chunks already landing via WS aren't held up.
      qc.invalidateQueries({ queryKey: ["messages", projectId] });

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

      // Watchdog: if NO WS event lands on the real message id within
      // WATCHDOG_MS, surface an error. Catches the "POST accepted, but
      // _process_prompt died silently and never published anything"
      // failure mode. The backend now also calls _emergency_error in
      // _on_done so this is a belt-and-suspenders check — both ends
      // protect the user from a stuck spinner.
      // Watchdog measures SILENCE, not total elapsed time: a long build
      // (brief → writer → images → design-judge) is fine as long as events
      // keep arriving. Reset on every update for this message; fire only
      // after WATCHDOG_MS of true silence (a dead/stuck task).
      // Silence watchdog (shared with the reconnect path): if the message goes
      // quiet too long while still streaming, surface an explicit error instead
      // of a stuck spinner. Mocks set tokens_out directly, so this also releases
      // streamingRef on their completion.
      watchMessage(message_id, () =>
        _failPrompt(
          "Нет ответа от модели",
          "слишком долго без ответа — попробуй ещё раз или сменить модель",
        ),
      );
    },
    [projectId, projectSlug, qc, apply, connect, watchMessage, fireQueued],
  );

  // Сохраняем актуальный submit в ref, чтобы fireQueued мог его вызвать,
  // не замыкаясь на устаревшую версию (useCallback пересоздаётся каждый рендер).
  submitRef.current = submit;

  // Reconnect to an in-flight generation we did NOT start in this mount — i.e.
  // the page was refreshed mid-build. THE fix for "F5 → realtime freezes": the
  // old code only opened the WS inside submit(), so after reload nobody listened
  // and the preview stalled forever. We detect the dangling assistant message
  // (real id, tokens_out === null) and reopen the socket; the server replays the
  // buffer via stream.sync. Guarded so it never double-connects.
  useEffect(() => {
    if (USE_MOCKS) return;
    const failReconnect = (messageId: string) => {
      qc.setQueryData<Message[]>(["messages", projectId], (prev) =>
        (prev ?? []).map((m) =>
          m.id === messageId
            ? {
                ...m,
                content:
                  m.content ||
                  "[Ошибка: соединение потеряно — обнови страницу или повтори промпт]",
                tokens_out: 0,
                tokens_in: 0,
              }
            : m,
        ),
      );
      streamingRef.current = false;
      cancelRef.current?.();
      cancelRef.current = null;
      toast.error("Соединение со стримом потеряно", {
        description: "не удалось досмотреть генерацию — обнови страницу",
        duration: 8_000,
      });
    };
    const maybeReconnect = () => {
      if (cancelRef.current) return; // already have a socket
      if (streamingRef.current) return; // submit owns the active stream
      const msgs = qc.getQueryData<Message[]>(["messages", projectId]);
      const last = msgs?.[msgs.length - 1];
      if (
        last &&
        last.role === "assistant" &&
        last.tokens_out === null &&
        !last.id.startsWith("__opt_") // real server id, not an optimistic row
      ) {
        streamingRef.current = true;
        reconnectAttemptsRef.current = 0;
        connect();
        watchMessage(last.id, () => failReconnect(last.id));
      }
    };
    maybeReconnect();
    const unsub = qc.getQueryCache().subscribe((evt) => {
      if (
        evt.type === "updated" &&
        evt.query.queryKey[0] === "messages" &&
        (evt.query.queryKey[1] as string) === projectId
      ) {
        maybeReconnect();
      }
    });
    return unsub;
  }, [projectId, qc, connect, watchMessage]);

  const cancel = useCallback(() => {
    // 1) Рвём WS — фронт перестаёт получать чанки.
    cancelRef.current?.();
    cancelRef.current = null;
    sendRef.current = null;
    streamingRef.current = false;

    // 2) Помечаем последнее ассистентское сообщение завершённым, чтобы
    //    ChatPanel.isStreaming (читает tokens_out из кэша) сразу разблокировался.
    //    Бэкенд может ещё какое-то время дописывать в БД — это TODO для
    //    отдельного /messages/:id/cancel-эндпоинта; пока best-effort на фронте.
    let cancelledMessageId: string | null = null;
    qc.setQueryData<Message[]>(["messages", projectId], (prev) =>
      (prev ?? []).map((m, i, arr) => {
        if (
          i === arr.length - 1 &&
          m.role === "assistant" &&
          m.tokens_out === null
        ) {
          cancelledMessageId = m.id;
          return {
            ...m,
            content: m.content + "\n\n[Отменено пользователем]",
            tokens_out: m.tokens_out ?? 0,
            tokens_in: m.tokens_in ?? 0,
          };
        }
        return m;
      }),
    );
    // B.3 — drop the progress entry for the cancelled message so the bar
    // doesn't keep showing "Шаг 2/4" after the user pressed Стоп.
    if (cancelledMessageId) {
      qc.removeQueries({
        queryKey: ["passes", projectId, cancelledMessageId],
      });
    }

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
