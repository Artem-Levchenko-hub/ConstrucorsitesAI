"use client";

import { useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState, type Ref } from "react";
import { AnimatePresence } from "framer-motion";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, PanelLeftClose } from "lucide-react";
import { toast } from "sonner";
import {
  connectAppIntegration,
  getIntegrationCatalog,
  setAitunnelAi,
  setNutritionAiKey,
} from "@/lib/api/app-integrations";
import { getLatestGeneration, listMessages } from "@/lib/api/messages";
import { getMaxProjectConfig } from "@/lib/api/max-studio";
import {
  getProductAdviceSnapshotId,
} from "@/lib/api/product-advice";
import type {
  AgentStep,
  GenerationRun,
  DesignPreview,
  SelectedElement,
  SurveyQuestion,
} from "@/lib/api/types";
import { ChatMessage } from "./ChatMessage";
import { PromptInput, type PromptInputHandle, type PromptReferenceSubmit, type PromptInputSubmitResult } from "./PromptInput";
import { DiscoveryChips } from "./DiscoveryChips";
import { DiscoveryFrame } from "./DiscoveryFrame";
import { OnboardingSurvey } from "./OnboardingSurvey";
import {
  usePromptStream,
  type PromptSubmitOptions,
} from "@/hooks/usePromptStream";
import { Skeleton } from "@/components/ui/skeleton";
import { useWorkspaceStore } from "@/store/workspace";
import { restorePersistedAgentSteps } from "@/lib/agent-steps";
import {
  buildAitunnelChatPrompt,
  containsChatSecret,
  getAitunnelSettingsCommand,
  redactChatSecrets,
  requestsAitunnelAppAi,
  resolveChatCredential,
} from "@/lib/max-chat-credentials";
import { MaxChatComposer } from "@/components/max/MaxChatComposer";
import { useOwnerChatQuota } from "@/hooks/useOwnerChatQuota";
import { FREE_CHAT_UPGRADE_MESSAGE } from "@/lib/owner-chat-quota";
import { OwnerChatQuotaNotice } from "./OwnerChatQuotaNotice";
import { useChatScroll } from "@/hooks/useChatScroll";
import { Button } from "@/components/ui/button";
import { bindMessageGeneration, generationContinuationForMessage, isGenerationActive } from "@/lib/generation-lifecycle";
import { isChatMessageStreaming } from "@/lib/chat-message-status";
import { readMaxLaunch } from "@/lib/max-launch-runner";
import type { MaxAdaptationAttachment } from "@/lib/use-max-adaptation";
import type { Message, Snapshot } from "@/lib/api/types";
import { DEFAULT_GENERATION_ENGINE } from "@/lib/generation-engine";

export type ChatPanelAdaptationHandle = {
  submitAdaptation: (attachment: MaxAdaptationAttachment) => Promise<boolean>;
};

type DiscoveryChoices = {
  choices: string[];
  allowCustom: boolean;
  multiSelect: boolean;
  // Onboarding-frame metadata (NORTH STAR pillar 2): position in the planned
  // batch + the inferred niche, so the question renders as a guided popup
  // («Вопрос N из M» + niche banner) instead of a bare chat row.
  questionIndex?: number | null;
  questionTotal?: number | null;
  niche?: string | null;
  // Answer-recap chips of what the user has said so far (pillar 2 — «вас услышали»).
  recap?: string[] | null;
  // LIVE design-preview tokens (pillars 2×3 — «покажи ЧТО построим»).
  designPreview?: DesignPreview | null;
};

export function ChatPanel({
  projectId,
  projectSlug,
  ownerId,
  basePath = `/max/${projectId}`,
  currentSnapshotId,
  draftRef,
  adaptationRef,
}: {
  projectId: string;
  projectSlug: string;
  ownerId?: string;
  basePath?: string;
  currentSnapshotId?: string | null;
  draftRef?: Ref<PromptInputHandle>;
  adaptationRef?: Ref<ChatPanelAdaptationHandle>;
}) {
  // This historical optimistic label is separate from the captured coding engine.
  const modelId = "topmix-v1";
  const quota = useOwnerChatQuota();
  const engine = DEFAULT_GENERATION_ENGINE;
  const authoredPending = useRef(false);
  const { submit, cancel, cancelPending, retryPending, restoreExpiredPending, pendingPrompt, pendingPhase } = usePromptStream(
    projectId,
    projectSlug,
    ownerId,
  );
  const toggleChat = useWorkspaceStore((s) => s.toggleChat);
  const scrollRef = useRef<HTMLDivElement>(null);
  const scrollContentRef = useRef<HTMLDivElement>(null);
  const chatFooterRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const composerDraftRef = useRef<PromptInputHandle>(null);
  useImperativeHandle(draftRef, () => ({
    insertDraft: text => composerDraftRef.current?.insertDraft(text) ?? false,
  }), []);
  const clearStarterHandoff = useCallback(() => {
    // Local housekeeping must not turn a confirmed paid submit into uncertainty.
    try { window.sessionStorage.removeItem(`omnia:max:starter:${projectId}`); }
    catch { /* The accepted server receipt remains authoritative. */ }
    try {
      if (window.location.pathname === basePath) window.history.replaceState(null, "", basePath);
    } catch { /* The run key still prevents a second generation. */ }
  }, [basePath, projectId]);
  const credentialSubmitPending = useRef(false);
  const adaptationSubmitPending = useRef(false);
  const acceptedAdaptations = useRef(new Set<string>());
  const qc = useQueryClient();

  const { data: history, isPending, isFetchedAfterMount, isSuccess } = useQuery({
    queryKey: ["messages", projectId],
    queryFn: () => listMessages(projectId),
    refetchOnMount: query => query.state.data?.length === 0 ? "always" : true,
  });
  // AgentTranscript owns the fetch/poll; observe its project cache so the whole
  // message (failure card, streaming state and timer) uses the same exact run.
  const latest = useQuery({ queryKey: ["generation", projectId], queryFn: () => getLatestGeneration(projectId), enabled: false });
  const messages = useMemo(() => history?.map(message => bindMessageGeneration(message, latest.data, projectId)), [history, latest.data, projectId]);

  // Re-hydrate the agentic transcript from history: the backend persists each
  // assistant reply's steps on `message.agent_steps`, so after a reload we seed
  // the ["agent-steps",…] cache AgentTranscript reads from. Only seed when the
  // cache has no live steps for that message — never clobber an active stream.
  useEffect(() => {
    if (!messages) return;
    for (const m of messages) {
      if (!m.agent_steps || m.agent_steps.length === 0) continue;
      const key = ["agent-steps", projectId, m.id];
      const current = qc.getQueryData<AgentStep[]>(key);
      const restored = restorePersistedAgentSteps(current, m.agent_steps);
      if (restored !== current) qc.setQueryData(key, restored);
    }
  }, [messages, projectId, qc]);

  // Zero-friction onboarding (P1): no blocking quiz modal. The very first prompt
  // submits straight through — the server runs a progressive in-chat discovery
  // (one short question at a time) before the first build. Every later prompt
  // submits the same way.
  const submitWithCredentialIntake = useCallback(
    async (
      text: string,
      selections: SelectedElement[],
      opts?: PromptSubmitOptions,
      composer?: { replay: boolean },
    ): Promise<boolean> => {
      const replay = composer?.replay ?? Boolean(opts?.idempotencyKey);
      const command = composer && !selections.length && !opts?.referenceImages?.length && !opts?.fontSelections?.length
        ? getAitunnelSettingsCommand(text)
        : null;
      // Capture fresh choices before async work. A legacy stable-key replay with
      // no choice must let the server recover its original accepted engine.
      if (opts?.generationEngine === undefined && !opts?.continuation && !replay) {
        opts = { ...opts, generationEngine: engine };
      }
      // An explicit stable key may be an already accepted request whose response
      // was lost; only the server can adjudicate that replay. Fresh manual sends
      // must not become a second queued message on Free.
      if (command === null && !replay && (quota.exhausted || (quota.limited && authoredPending.current))) {
        toast.info(FREE_CHAT_UPGRADE_MESSAGE);
        return false;
      }
      const selectionText = selections
        .flatMap((selection) => Object.values(selection))
        .filter((value): value is string => typeof value === "string")
        .join("\n");
      const credentialSource = [text, selectionText].filter(Boolean).join("\n");
      let resolution = resolveChatCredential(credentialSource, [], text);
      // A new grant may come from the sent composer text. URL starters and
      // inspector metadata can carry task data without authorizing that grant.
      const allowAppAi = Boolean(composer)
        && (resolution.kind === "none" || resolution.kind === "aitunnel_key")
        && requestsAitunnelAppAi(text, resolution.kind === "aitunnel_key" ? resolution.value.secret : undefined);
      const updateSavedKey = command !== null || (resolution.kind === "none" && allowAppAi);
      let safePrompt = text;
      let safeSelections = selections;

      if (updateSavedKey || resolution.kind !== "none") {
        if (credentialSubmitPending.current) {
          toast.info("Дождитесь завершения настройки ключа");
          return false;
        }
        credentialSubmitPending.current = true;
        const queryKey = ["app-integrations", projectId];
        let secret: string | undefined;
        try {
          if (updateSavedKey) {
            const requested = command ?? true;
            const { enabled } = await setAitunnelAi(projectId, requested);
            if (enabled !== requested) throw new Error("Сервер не подтвердил изменение доступа");
            toast.success(enabled ? "AITunnel включён для ИИ приложения" : "AITunnel выключен для ИИ приложения");
            if (command === null) safePrompt = buildAitunnelChatPrompt(text, true);
          } else {
            if (resolution.kind === "needs_provider") {
              const catalog = await qc.fetchQuery({ queryKey, queryFn: () => getIntegrationCatalog(projectId), staleTime: 60_000 });
              resolution = resolveChatCredential(credentialSource, catalog.providers, text);
            }
            if (resolution.kind === "needs_provider") {
              toast.error("Не понял, к какому провайдеру относится ключ", {
                description: "Напишите рядом точное название сервиса, например: ЮKassa — ключ …",
              });
              return false;
            }
            if (resolution.kind === "needs_fields") {
              const fields = resolution.labels.length
                ? ` Нужны также: ${resolution.labels.join(", ")}.`
                : " Для этого сервиса нужно дополнительное подключение.";
              toast.error(`Не хватает данных для ${resolution.provider.name}`, {
                description: `${fields} Откройте «Интеграции» для завершения.`,
              });
              return false;
            }
            if (resolution.kind === "none") return false;
            ({ secret, safePrompt } = resolution.value);
            if (resolution.kind === "aitunnel_key") {
              const { enabled } = await setNutritionAiKey(projectId, secret, allowAppAi);
              if (enabled !== true) throw new Error("Не удалось сохранить ключ AITunnel");
              if (allowAppAi) safePrompt = buildAitunnelChatPrompt(text, true, secret);
              toast.success(allowAppAi ? "Ключ AITunnel сохранён для ИИ приложения" : "Ключ AITunnel сохранён для оценки КБЖУ");
            } else {
              const { provider, secretField } = resolution.value;
              await connectAppIntegration(projectId, provider.key, { [secretField.key]: secret });
              toast.success(`${provider.name} подключён`, {
                description: "Ключ проверен и зашифрован. Агент получил безопасную ссылку на интеграцию.",
              });
            }
            safeSelections = selections.map((selection) =>
              Object.fromEntries(Object.entries(selection).map(([key, value]) => [
                key, typeof value === "string" ? redactChatSecrets(value, secret) : value,
              ])) as SelectedElement,
            );
          }
          void qc.invalidateQueries({ queryKey });
        } catch (error) {
          toast.error(updateSavedKey ? "Не удалось изменить доступ AITunnel к ИИ приложения" : "Ключ не подключён", {
            description: error instanceof Error ? redactChatSecrets(error.message, secret) : "Проверьте данные и повторите отправку.",
          });
          return false;
        } finally {
          credentialSubmitPending.current = false;
        }
      }
      if (command !== null) return true;
      if (quota.limited) authoredPending.current = true;
      try { return await submit(safePrompt, modelId, safeSelections, opts); }
      finally { authoredPending.current = false; }
    },
    [modelId, projectId, qc, submit, quota.exhausted, quota.limited, engine],
  );

  const handleSubmit = async (text: string, selections: SelectedElement[], { idempotencyKey, referenceImages, fontSelections, generationEngine }: PromptReferenceSubmit, replay: boolean): Promise<PromptInputSubmitResult> => {
    let referencesExpired = false;
    const accepted = await submitWithCredentialIntake(text, selections, {
      idempotencyKey,
      ...(generationEngine ? { generationEngine } : {}),
      ...(referenceImages.length ? { referenceImages, onReferenceImagesExpired: () => { referencesExpired = true; } } : {}),
      ...(fontSelections?.length ? { fontSelections } : {}),
    }, { replay });
    return referencesExpired ? { accepted: false, reason: "reference_images_expired" } : accepted;
  };

  // Only an explicit button calls this handle. Restoring its durable attachment
  // on mount never sends a model request and never replaces the user's draft.
  useImperativeHandle(adaptationRef, () => ({
    async submitAdaptation(attachment) {
      const reference = attachment.reference;
      const head = qc.getQueryData<Snapshot[]>(["snapshots", projectId])?.[0]?.id ?? currentSnapshotId;
      if (attachment.projectId !== projectId || reference.expected_draft_snapshot_id !== head) {
        toast.error("Черновик изменился. Подготовьте восстановление выбранной версии заново.");
        return false;
      }
      if (acceptedAdaptations.current.has(reference.operation_id)) return true;
      const history = qc.getQueryData<Message[]>(["messages", projectId]);
      if (adaptationSubmitPending.current || credentialSubmitPending.current || isPending
        || pendingPrompt || isChatMessageStreaming(history?.[history.length - 1])) {
        toast.info("Дождитесь завершения текущего запроса и повторите адаптацию.");
        return false;
      }
      try {
        if (readMaxLaunch(projectId)) {
          toast.info("Сначала проверьте результат публикации приложения.");
          return false;
        }
        adaptationSubmitPending.current = true;
        const accepted = await submitWithCredentialIntake(attachment.prompt, [], {
          restorationAdaptation: reference,
          idempotencyKey: `restoration-adapt:${reference.operation_id}`,
          skipClarify: true,
        });
        if (accepted) acceptedAdaptations.current.add(reference.operation_id);
        return accepted;
      } catch {
        toast.error("Не удалось подтвердить запуск адаптации. Повторите попытку — второй запрос не будет создан.");
        return false;
      } finally {
        adaptationSubmitPending.current = false;
      }
    },
  }), [projectId, currentSnapshotId, qc, isPending, pendingPrompt, submitWithCredentialIntake]);

  // «Починить» on an error card → submit a follow-up fix prompt through the
  // normal pipeline (surgical edit / rebuild as the triage decides).
  const handleFix = (prompt: string) => {
    void submitWithCredentialIntake(prompt, []);
  };

  // A fork recap card's one-tap starter edit → submit it as the remixer's first
  // prompt through the normal pipeline (the warm first move, pillar 4).
  const handleSuggest = (prompt: string) => {
    void submitWithCredentialIntake(prompt, []);
  };

  // Discovery chip tapped (or an inline «Другое» answer) → submit it as the
  // user's answer to the question. Used by both single-select and the joined
  // multi-select «Готово» submission (the card builds the combined string).
  const handlePickChoice = (choice: string) => {
    void submitWithCredentialIntake(choice, []);
  };

  // «Я готов — постройте сейчас» — leave the onboarding popup early and build now.
  // Submitting an explicit build-now phrase trips the server's wants_build_now
  // floor, so the next turn generates instead of asking another question. The
  // user is never trapped in the interview (NORTH STAR pillar 2 — явный skip).
  const handleSkip = () => {
    void submitWithCredentialIntake("Постройте сейчас", []);
  };

  // Durable server status wins; interrupted runs may never receive usage totals.
  const last = messages?.[messages.length - 1];
  const isStreaming = isChatMessageStreaming(last);
  const streamingId = isStreaming ? last?.id : null;
  const generationActive = latest.data?.project_id === projectId && isGenerationActive(latest.data);
  const retryAvailable = !quota.exhausted && !isPending && !pendingPrompt && !isStreaming && !generationActive;
  const continuation = generationContinuationForMessage(latest.data, last, projectId);
  const retryLatestRequest = () => {
    if (!continuation) return submitWithCredentialIntake("Попробуй ещё раз", []);
    const current = generationContinuationForMessage(
      qc.getQueryData<GenerationRun>(["generation", projectId]),
      qc.getQueryData<Message[]>(["messages", projectId])?.at(-1), projectId,
    );
    if (!current || JSON.stringify(current) !== JSON.stringify(continuation)) {
      toast.info("Черновик изменился. Дождитесь обновления и выберите продолжение заново.");
      return Promise.resolve(false);
    }
    // The exact source reference has one logical continuation, even after F5.
    // The server recovers its accepted engine; the QA selector cannot restyle it.
    return submitWithCredentialIntake("Продолжи сборку", [], {
      continuation: current,
      idempotencyKey: `continue:${current.run_id}:${current.assistant_message_id}:${current.expected_snapshot_id ?? "none"}`,
    });
  };
  const returnToPrompt = () => inputRef.current?.focus();

  // Progressive-discovery quick replies (P1): chips belong to the LATEST
  // assistant question. Reading the client cache the prompt hook populated on
  // the POST response (keyed by that message id) — via useQuery so the render
  // reacts the instant the hook stashes them. Showing only when the question is
  // the last message means the chips vanish on their own once the user answers.
  const lastAssistantId =
    last?.role === "assistant" && !last.id.startsWith("__opt_")
      ? last.id
      : null;
  const { data: chips } = useQuery<DiscoveryChoices | null>({
    queryKey: ["discovery-choices", projectId, lastAssistantId],
    queryFn: () =>
      qc.getQueryData<DiscoveryChoices>([
        "discovery-choices",
        projectId,
        lastAssistantId,
      ]) ?? null,
    enabled: !!lastAssistantId,
    staleTime: Infinity,
  });

  // Onboarding SURVEY (owner 2026-06-19 — «несколько вопросов сразу»): the whole
  // planned batch arrives on the first discovery turn (usePromptStream stashes it
  // keyed by project). Render it as ONE popup form instead of a chat turn per
  // question. Dismissed once answered/skipped (client-only, per session).
  const [surveyDismissed, setSurveyDismissed] = useState(false);
  const { data: survey } = useQuery<SurveyQuestion[] | null>({
    queryKey: ["onboarding-survey", projectId],
    queryFn: () =>
      qc.getQueryData<SurveyQuestion[]>(["onboarding-survey", projectId]) ?? null,
    staleTime: Infinity,
  });
  const showSurvey = !!survey && survey.length > 0 && !surveyDismissed && !quota.exhausted;

  const { data: currentConfig } = useQuery({
    queryKey: ["max-config", projectId],
    queryFn: () => getMaxProjectConfig(projectId),
  });
  const adviceSnapshotId = !showSurvey
    ? getProductAdviceSnapshotId(messages ?? [], currentSnapshotId) : null;
  const Composer = MaxChatComposer;

  const clearSurvey = () => {
    qc.setQueryData(["onboarding-survey", projectId], null);
    setSurveyDismissed(true);
  };
  // «Готово» — fire ONE build prompt with the combined answers + picked preset.
  // skip_clarify so the server builds straight away instead of re-interviewing.
  const handleSurveyDone = async (combined: string, presetId: string | null) => {
    const text = combined.trim() || "Постройте сейчас";
    const includesSecret = containsChatSecret(text);
    if (!includesSecret) clearSurvey();
    const submitted = await submitWithCredentialIntake(text, [], {
      skipClarify: true,
      designPresetId: presetId,
    });
    if (submitted && includesSecret) clearSurvey();
  };
  const handleSurveySkip = () => {
    clearSurvey();
    void submitWithCredentialIntake("Постройте сейчас", [], {
      skipClarify: true,
    });
  };

  const chatScroll = useChatScroll(scrollRef, `${messages?.length}:${last?.content}:${chips?.choices.join("|")}`, {
    contentRef: scrollContentRef,
    footerRef: chatFooterRef,
    scopeKey: projectId,
  });

  // Post the wizard brief once on an empty project. Across reloads the same
  // server key resolves a lost response without starting a second generation.
  const starterAttempted = useRef(false);
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const urlPrompt = params.get("p");
    if (urlPrompt && containsChatSecret(urlPrompt)) {
      window.history.replaceState(null, "", basePath);
      toast.error("Ключ из ссылки не принят", {
        description: "Вставьте название провайдера и ключ прямо в поле чата.",
      });
      return;
    }
    if (starterAttempted.current || !isSuccess || !messages || pendingPrompt) return;
    const prompt = (urlPrompt || (params.get("starter") === "1"
      ? window.sessionStorage.getItem(`omnia:max:starter:${projectId}`) : null))?.trim();
    if (!prompt) return;
    if (messages.length > 0) {
      // A reload may recover the accepted message before the POST response.
      if (messages.some(message => message.project_id === projectId && message.role === "user"
        && !message.id.startsWith("__opt_") && message.content === prompt)) clearStarterHandoff();
      return;
    }

    // A cached empty history can predate a submit from another tab.
    if (!isFetchedAfterMount) return;
    starterAttempted.current = true;
    void submitWithCredentialIntake(prompt, [], {
      skipClarify: true,
      idempotencyKey: `max-starter-${projectId}`,
    }).then(submitted => {
      if (submitted) clearStarterHandoff();
    });
  }, [messages, isSuccess, isFetchedAfterMount, pendingPrompt, basePath, projectId, submitWithCredentialIntake, clearStarterHandoff]);

  return (
    // Keep history scrolling within the editor's height in both chat layouts.
    <div className="flex h-full min-h-0 flex-col max-studio-chat max-editor-chat bg-surface-raised">
      <div
        ref={scrollRef}
        onScroll={chatScroll.onScroll}
        onClickCapture={(event) => {
          const summary = event.target instanceof Element ? event.target.closest("summary") : null;
          if (summary?.parentElement instanceof HTMLDetailsElement) chatScroll.pauseFollowing();
        }}
        className="max-chat-history flex-1 min-h-0 overflow-y-auto overflow-x-hidden overscroll-contain scrollbar-elegant"
      >
        <div ref={scrollContentRef} className="flow-root">
          {isPending && (
            <div className="p-4 space-y-3">
              <Skeleton className="h-16" />
              <Skeleton className="h-24" />
            </div>
          )}

          {!isPending && messages && messages.length === 0 && (
            <div className="max-editor-empty">
              <h2 className="text-sm text-fg-primary">Какое приложение создадим?</h2>
              <p className="text-xs text-fg-tertiary leading-5">
                Расскажите, для кого оно и что должен сделать пользователь.
                Например: «Каталог кофе с заказом к определённому времени».
              </p>
            </div>
          )}

          {messages?.map((m) => (
            <ChatMessage
              key={m.id}
              message={m}
              streaming={m.id === streamingId}
              projectId={projectId}
              onFix={quota.exhausted ? undefined : handleFix}
              onRetry={retryAvailable && m.id === last?.id ? retryLatestRequest : undefined}
              continueFromDraft={Boolean(continuation && m.id === last?.id)}
              onReturnToPrompt={!isPending && m.id === last?.id ? returnToPrompt : undefined}
              onSuggest={quota.exhausted ? undefined : handleSuggest}
              presentation="studio"
            />
          ))}

          {!quota.exhausted && !showSurvey && chips && chips.choices.length > 0 && (
            <DiscoveryFrame
              key={lastAssistantId}
              niche={chips.niche ?? null}
              questionIndex={chips.questionIndex ?? null}
              questionTotal={chips.questionTotal ?? null}
              recap={chips.recap ?? null}
              designPreview={chips.designPreview ?? null}
              onSkip={handleSkip}
            >
              <DiscoveryChips
                choices={chips.choices}
                allowCustom={chips.allowCustom}
                multiSelect={chips.multiSelect}
                onPick={handlePickChoice}
              />
            </DiscoveryFrame>
          )}
        </div>
      </div>

      <div ref={chatFooterRef} className="max-chat-footer relative shrink-0">
        {!chatScroll.following && <div className="max-chat-latest">
          <Button type="button" variant="secondary" size="sm" onClick={chatScroll.scrollToLatest}>
            <ArrowDown aria-hidden="true" /> К последнему сообщению
          </Button>
        </div>}
        <Composer
          key={projectId}
          draftRef={composerDraftRef}
          projectId={projectId}
          snapshotId={adviceSnapshotId}
          generationEngine={engine}
          contextVersion={currentConfig?.config_version}
          onSubmit={handleSubmit}
          sendDisabled={quota.exhausted || (quota.limited && isStreaming)}
          canSendWithoutQuota={(text) => getAitunnelSettingsCommand(text) !== null}
          quotaNotice={<OwnerChatQuotaNotice {...quota} />}
          onCancel={cancel}
          onCancelPending={cancelPending}
          onRetryPending={retryPending}
          onRestoreExpiredPending={restoreExpiredPending}
          isStreaming={isStreaming}
          pendingPrompt={pendingPrompt}
          pendingPhase={pendingPhase}
          textareaRef={inputRef}
          placeholder="Что создадим или изменим?"
          ariaLabel="Опишите изменение MAX Mini App"
          className="max-studio-prompt"
        />
      </div>

      {/* Onboarding survey popup — all planned questions at once (owner 2026-06-19). */}
      {!quota.exhausted && <AnimatePresence>
        {showSurvey && survey && (
          <OnboardingSurvey
            questions={survey}
            onDone={handleSurveyDone}
            onSkip={handleSurveySkip}
          />
        )}
      </AnimatePresence>}
    </div>
  );
}
