"use client";

import { useEffect, useRef, useState } from "react";
import { AnimatePresence } from "framer-motion";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { PanelLeftClose } from "lucide-react";
import { listMessages } from "@/lib/api/messages";
import type {
  DesignPreview,
  SelectedElement,
  SurveyQuestion,
} from "@/lib/api/types";
import { ChatMessage } from "./ChatMessage";
import { PromptInput } from "./PromptInput";
import { DiscoveryChips } from "./DiscoveryChips";
import { DiscoveryFrame } from "./DiscoveryFrame";
import { OnboardingSurvey } from "./OnboardingSurvey";
import { usePromptStream } from "@/hooks/usePromptStream";
import { Skeleton } from "@/components/ui/skeleton";
import { useWorkspaceStore } from "@/store/workspace";

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
}: {
  projectId: string;
  projectSlug: string;
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
  const qc = useQueryClient();

  const { data: messages, isPending } = useQuery({
    queryKey: ["messages", projectId],
    queryFn: () => listMessages(projectId),
  });

  // Zero-friction onboarding (P1): no blocking quiz modal. The very first prompt
  // submits straight through — the server runs a progressive in-chat discovery
  // (one short question at a time) before the first build. Every later prompt
  // submits the same way.
  const handleSubmit = (text: string, selections: SelectedElement[]) => {
    submit(text, modelId, selections);
  };

  // «Починить» on an error card → submit a follow-up fix prompt through the
  // normal pipeline (surgical edit / rebuild as the triage decides).
  const handleFix = (prompt: string) => {
    submit(prompt, modelId, []);
  };

  // A fork recap card's one-tap starter edit → submit it as the remixer's first
  // prompt through the normal pipeline (the warm first move, pillar 4).
  const handleSuggest = (prompt: string) => {
    submit(prompt, modelId, []);
  };

  // Discovery chip tapped (or an inline «Другое» answer) → submit it as the
  // user's answer to the question. Used by both single-select and the joined
  // multi-select «Готово» submission (the card builds the combined string).
  const handlePickChoice = (choice: string) => {
    submit(choice, modelId, []);
  };

  // «Я готов — постройте сейчас» — leave the onboarding popup early and build now.
  // Submitting an explicit build-now phrase trips the server's wants_build_now
  // floor, so the next turn generates instead of asking another question. The
  // user is never trapped in the interview (NORTH STAR pillar 2 — явный skip).
  const handleSkip = () => {
    submit("Постройте сейчас", modelId, []);
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

  const clearSurvey = () => {
    qc.setQueryData(["onboarding-survey", projectId], null);
    setSurveyDismissed(true);
  };
  // «Готово» — fire ONE build prompt with the combined answers + picked preset.
  // skip_clarify so the server builds straight away instead of re-interviewing.
  const handleSurveyDone = (combined: string, presetId: string | null) => {
    clearSurvey();
    submit(combined.trim() || "Постройте сейчас", modelId, [], {
      skipClarify: true,
      designPresetId: presetId,
    });
  };
  const handleSurveySkip = () => {
    clearSurvey();
    submit("Постройте сейчас", modelId, [], { skipClarify: true });
  };

  // Auto-scroll on new messages / chunks.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [messages?.length, last?.content, chips]);

  return (
    // h-full + min-h-0 нужны чтобы в grid-cell flex-колонка получила фиксированную
    // высоту и `flex-1 + overflow-y-auto` ниже реально срабатывал, а не растягивал
    // родителя (раньше из-за двойного скролла внутри ScrollArea инпут уезжал вниз).
    <div className="flex flex-col h-full min-h-0 bg-surface-panel-dark">
      <div className="shrink-0 px-4 h-10 flex items-center justify-between">
        <span className="text-xs font-mono text-fg-tertiary uppercase tracking-wider">
          Чат
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
              Поговорим о вашем сайте.
            </div>
            <div className="text-xs text-fg-tertiary leading-5">
              Опишите, что хотите создать. Например:
              <br />
              «Сделай лендинг для пиццерии с меню и формой заказа».
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
          />
        ))}

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
