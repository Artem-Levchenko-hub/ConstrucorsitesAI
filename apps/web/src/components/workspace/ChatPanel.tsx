"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { AnimatePresence } from "framer-motion";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { PanelLeftClose } from "lucide-react";
import { toast } from "sonner";
import {
  connectAppIntegration,
  getIntegrationCatalog,
} from "@/lib/api/app-integrations";
import { listMessages } from "@/lib/api/messages";
import {
  getProductAdviceSnapshotId,
  requestProductAdvice,
  submitProductAdvice,
  type ProductAdviceItem,
} from "@/lib/api/product-advice";
import type {
  AgentStep,
  DesignPreview,
  SelectedElement,
  SurveyQuestion,
} from "@/lib/api/types";
import { ChatMessage } from "./ChatMessage";
import { PromptInput } from "./PromptInput";
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
  containsChatSecret,
  redactChatSecrets,
  resolveChatCredential,
} from "@/lib/max-chat-credentials";
import { MaxProductAdvisor } from "@/components/max/MaxProductAdvisor";

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
  mode = "default",
  basePath = `/projects/${projectId}`,
  embedded = false,
}: {
  projectId: string;
  projectSlug: string;
  mode?: "default" | "max";
  basePath?: string;
  embedded?: boolean;
}) {
  // Server orchestrates per-role models (Opus director, DeepSeek polish, …).
  // The client no longer picks a model; this label is just sent through for
  // the optimistic chat row and is ignored by the backend.
  const modelId = "topmix-v1";
  const { submit, cancel, cancelPending, pendingPrompt } = usePromptStream(
    projectId,
    projectSlug,
  );
  const toggleChat = useWorkspaceStore((s) => s.toggleChat);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const credentialSubmitPending = useRef(false);
  const qc = useQueryClient();

  const { data: messages, isPending } = useQuery({
    queryKey: ["messages", projectId],
    queryFn: () => listMessages(projectId),
  });

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
    ): Promise<boolean> => {
      const selectionText = selections
        .flatMap((selection) => Object.values(selection))
        .filter((value): value is string => typeof value === "string")
        .join("\n");
      const credentialSource = [text, selectionText].filter(Boolean).join("\n");
      if (mode !== "max" || !containsChatSecret(credentialSource)) {
        return submit(text, modelId, selections, opts);
      }
      if (credentialSubmitPending.current) {
        toast.info("Ключ уже подключается");
        return false;
      }

      credentialSubmitPending.current = true;
      try {
        const catalog = await qc.fetchQuery({
          queryKey: ["app-integrations", projectId],
          queryFn: () => getIntegrationCatalog(projectId),
          staleTime: 60_000,
        });
        const resolution = resolveChatCredential(
          credentialSource,
          catalog.providers,
          text,
        );
        if (resolution.kind === "needs_provider") {
          toast.error("Не понял, к какому провайдеру относится ключ", {
            description:
              "Напишите рядом точное название сервиса, например: AITUNNEL — ключ …",
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
        if (resolution.kind !== "match") return false;

        const { provider, secretField, secret, safePrompt } = resolution.value;
        await connectAppIntegration(projectId, provider.key, {
          [secretField.key]: secret,
        });
        await qc.invalidateQueries({
          queryKey: ["app-integrations", projectId],
        });
        toast.success(`${provider.name} подключён`, {
          description:
            "Ключ проверен и зашифрован. Агент получил только безопасную ссылку на интеграцию.",
        });
        const safeSelections = selections.map((selection) =>
          Object.fromEntries(
            Object.entries(selection).map(([key, value]) => [
              key,
              typeof value === "string"
                ? redactChatSecrets(value).replaceAll(
                    secret,
                    "[ключ сохранён в Omnia]",
                  )
                : value,
            ]),
          ) as SelectedElement,
        );
        return await submit(safePrompt, modelId, safeSelections, opts);
      } catch (error) {
        toast.error("Ключ не подключён", {
          description:
            error instanceof Error
              ? error.message
              : "Проверьте данные и повторите отправку.",
        });
        return false;
      } finally {
        credentialSubmitPending.current = false;
      }
    },
    [mode, modelId, projectId, qc, submit],
  );

  const handleSubmit = (text: string, selections: SelectedElement[]) =>
    submitWithCredentialIntake(text, selections);

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

  // Determine streaming state from data: an assistant message with
  // tokens_out === null is mid-stream.
  const last = messages?.[messages.length - 1];
  const isStreaming =
    last?.role === "assistant" && last.tokens_out === null;
  const streamingId = isStreaming ? last?.id : null;

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
  const showSurvey = !!survey && survey.length > 0 && !surveyDismissed;

  const adviceSnapshotId =
    mode === "max" ? getProductAdviceSnapshotId(messages ?? []) : null;
  const productAdvice = useQuery({
    queryKey: ["product-advice", projectId, adviceSnapshotId],
    queryFn: () => requestProductAdvice(projectId),
    enabled: !!adviceSnapshotId && !isStreaming && !showSurvey,
    staleTime: Infinity,
    retry: 1,
    refetchOnWindowFocus: false,
  });
  const [applyingAdviceId, setApplyingAdviceId] = useState<string | null>(null);

  const handleApplyAdvice = async (item: ProductAdviceItem) => {
    if (applyingAdviceId !== null) return;
    setApplyingAdviceId(item.id);
    try {
      await submitProductAdvice(item, submitWithCredentialIntake);
    } finally {
      setApplyingAdviceId(null);
    }
  };

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

  // Auto-scroll on new messages / chunks.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [messages?.length, last?.content, chips, productAdvice.data?.items.length]);

  // `/deep-research` entry hands the user's task over via `?p=`: auto-fire it
  // ONCE on a fresh (empty) project so they land mid-agent-run — the cloud
  // Claude Code experience — instead of re-typing it. `skipClarify` sends it
  // straight to the agent (no onboarding interview). Strip the param afterwards
  // so a refresh never replays the prompt.
  const autoFiredRef = useRef(false);
  useEffect(() => {
    if (autoFiredRef.current) return;
    if (messages === undefined) return; // wait for the first load
    const params = new URLSearchParams(window.location.search);
    const urlPrompt = params.get("p");
    let p = urlPrompt;
    if (urlPrompt && containsChatSecret(urlPrompt)) {
      window.history.replaceState(null, "", basePath);
      toast.error("Ключ из ссылки не принят", {
        description: "Вставьте название провайдера и ключ прямо в поле чата.",
      });
      p = null;
    }
    const starterStorageKey = `omnia:max:starter:${projectId}`;
    if (!p && params.get("starter") === "1") {
      p = window.sessionStorage.getItem(starterStorageKey);
    }
    if (p && p.trim() && messages.length === 0) {
      autoFiredRef.current = true;
      void submitWithCredentialIntake(p.trim(), [], {
        skipClarify: true,
        // Stable on the server across tabs/reloads/devices. Even if the handoff
        // effect somehow fires twice, reserve_generation_run replays this exact
        // run rather than creating a second generation.
        idempotencyKey: `max-starter-${projectId}`,
      }).then((submitted) => {
        if (submitted) {
          window.sessionStorage.removeItem(starterStorageKey);
          window.history.replaceState(null, "", basePath);
        } else {
          autoFiredRef.current = false;
        }
      });
    }
  }, [messages, submitWithCredentialIntake, basePath, projectId]);

  return (
    // h-full + min-h-0 нужны чтобы в grid-cell flex-колонка получила фиксированную
    // высоту и `flex-1 + overflow-y-auto` ниже реально срабатывал, а не растягивал
    // родителя (раньше из-за двойного скролла внутри ScrollArea инпут уезжал вниз).
    <div className={`flex h-full min-h-0 flex-col ${embedded ? "max-studio-chat bg-[#191b20]" : "border-r border-[#2b2d32] bg-[#121519]"}`}>
      {!embedded && (
        <div className="flex h-12 shrink-0 items-center justify-between border-b border-[#2b2d32] px-4">
          <span className="text-[10px] font-semibold uppercase tracking-[0.16em] text-slate-500">
            {mode === "max" ? "MAX-редактор" : "Чат"}
          </span>
          <button
            type="button"
            onClick={toggleChat}
            aria-label="Свернуть чат"
            title="Свернуть чат"
            className="-mr-1.5 flex h-6 w-6 items-center justify-center rounded text-fg-tertiary transition-colors hover:bg-surface-overlay hover:text-fg-secondary"
          >
            <PanelLeftClose className="h-3.5 w-3.5" />
          </button>
        </div>
      )}

      <div
        ref={scrollRef}
        className="flex-1 min-h-0 overflow-y-auto overflow-x-hidden overscroll-contain scrollbar-elegant"
      >
        {isPending && (
          <div className="p-4 space-y-3">
            <Skeleton className="h-16" />
            <Skeleton className="h-24" />
          </div>
        )}

        {!isPending && messages && messages.length === 0 && (
          <div className="p-6 text-center space-y-2">
            <div className="text-sm text-fg-secondary">
              {mode === "max"
                ? "Расскажите, что изменить в Mini App."
                : "Поговорим о вашем сайте."}
            </div>
            <div className="text-xs text-fg-tertiary leading-5">
              {mode === "max" ? (
                <>
                  Здесь нельзя переключить проект на обычный сайт: все правки
                  сохраняют MAX Bridge, мобильный интерфейс и интеграцию бота.
                </>
              ) : (
                <>
                  Опишите, что хотите создать. Например:
                  <br />
                  «Сделай лендинг для пиццерии с меню и формой заказа».
                </>
              )}
            </div>
          </div>
        )}

        {messages?.map((m) => (
          <ChatMessage
            key={m.id}
            message={m}
            streaming={m.id === streamingId}
            projectId={projectId}
            onFix={handleFix}
            onSuggest={handleSuggest}
            presentation={embedded ? "studio" : "default"}
          />
        ))}

        {mode === "max" &&
          !showSurvey &&
          !isStreaming &&
          productAdvice.data?.items.length ? (
            <MaxProductAdvisor
              items={productAdvice.data.items}
              applyingId={applyingAdviceId}
              onApply={handleApplyAdvice}
            />
          ) : null}

        {!showSurvey && chips && chips.choices.length > 0 && (
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

      <div className="shrink-0">
        <PromptInput
          onSubmit={handleSubmit}
          onCancel={cancel}
          onCancelPending={cancelPending}
          isStreaming={isStreaming}
          pendingPrompt={pendingPrompt}
          textareaRef={inputRef}
          placeholder={
            mode === "max"
              ? "Опишите правку или вставьте: AITUNNEL — ключ …"
              : undefined
          }
          ariaLabel={
            mode === "max"
              ? "Опишите изменение MAX Mini App"
              : undefined
          }
          className={embedded ? "max-studio-prompt" : undefined}
        />
      </div>

      {/* Onboarding survey popup — all planned questions at once (owner 2026-06-19). */}
      <AnimatePresence>
        {showSurvey && survey && (
          <OnboardingSurvey
            questions={survey}
            onDone={handleSurveyDone}
            onSkip={handleSurveySkip}
          />
        )}
      </AnimatePresence>
    </div>
  );
}
