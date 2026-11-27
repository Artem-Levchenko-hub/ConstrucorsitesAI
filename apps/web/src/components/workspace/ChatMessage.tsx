"use client";

import { useState } from "react";
import Image from "next/image";
import {
  Bot,
  User as UserIcon,
  FileCode2,
  PencilLine,
  ChevronRight,
  Loader2,
  Wand2,
  Check,
  Pause,
} from "lucide-react";
import { motion, AnimatePresence, useReducedMotion } from "framer-motion";
import type { Message } from "@/lib/api/types";
import { FONT_ROLE_LABELS } from "@/lib/api/font-assets";
import { EASE_OUT, fadeUp } from "@/lib/motion";
import { formatRelativeTime, cn } from "@/lib/utils";
import {
  parseAssistantContent,
  cleanChatProse,
  formatBytes,
  type AssistantPart,
  type AppErrorCategory,
} from "@/lib/parse-assistant";
import { SelectedChips } from "./SelectedChips";
import { ReferenceImageThumbnail } from "./ReferenceImageThumbnail";
import { PassProgressBar } from "./PassProgressBar";
import { AgentTranscript } from "./AgentTranscript";
import { RemixRecapCard } from "./RemixRecapCard";
import { Markdown } from "./Markdown";
import { GenerationFailureCard } from "./GenerationFailureCard";
import { IntegrationRequestCard } from "./IntegrationRequestCard";
import { recognizeBuiltinIntegrationRequest } from "@/lib/builtin-integration-prompts";
import { UserMessageContent } from "./UserMessageContent";
import { VersionChangesDisclosure } from "./VersionChangesDisclosure";

// Backend status lines streamed mid-generation (messages.py): acceptance retry
// and the freeform→catalog fallback. They arrive as `*…*` italic prose; we lift
// them out into a live "working" card instead of plain italic text.
const STATUS_RE = /\*+\s*(Приёмка|Свободная вёрстка)[^*\n]*/i;

const GENERATION_SUCCESS_HEADINGS = new Set([
  "Приложение готово.",
  "Готово — приложение собрано и проверено.",
  "Готово — правка применена и проверена.",
  "Готово — приложение собрано.",
  "Готово — правка применена.",
  "Готово — приложение собрано. Запуск превью проверен.",
  "Готово — правка применена. Сборка и запуск превью проверены.",
]);

const UNCONFIRMED_GENERATION_HEADINGS = {
  pending: "Подтверждаем готовую версию.",
  cancelled: "Работа остановлена.",
  failed: "Подготовка на паузе.",
};
type GenerationReportStatus = keyof typeof UNCONFIRMED_GENERATION_HEADINGS;

function unconfirmedGenerationContent({ content, snapshot_id }: Message, status: GenerationReportStatus): string {
  const heading = content.split("\n", 1)[0];
  if (!GENERATION_SUCCESS_HEADINGS.has(heading.trim())) return content;
  const provisional = snapshot_id ? "Версия сохранена." : UNCONFIRMED_GENERATION_HEADINGS[status];
  return provisional + content.slice(heading.length);
}

export function ChatMessage({
  message,
  streaming,
  generationReportStatus,
  projectId,
  onFix,
  onSuggest,
  onRetry,
  continueFromDraft = false,
  onReturnToPrompt,
  presentation = "default",
}: {
  message: Message;
  streaming?: boolean;
  generationReportStatus?: GenerationReportStatus;
  /**
   * Required for the B.3 multipass progress bar — it subscribes to
   * `["passes", projectId, message.id]` in React Query cache. Optional
   * so callers can omit it for non-streaming chat replays / e2e
   * screenshots, in which case the progress bar simply never renders.
   */
  projectId?: string;
  /** Submit a follow-up "fix this error" prompt (wired from the error card's
   *  «Починить» button). Omitted in replays / screenshots → button hidden. */
  onFix?: (prompt: string) => void;
  /** Submit a starter-edit prompt from a fork recap card's one-tap chips.
   *  Omitted in replays / screenshots → chips render non-interactive. */
  onSuggest?: (prompt: string) => void;
  onRetry?: () => Promise<boolean>;
  continueFromDraft?: boolean;
  onReturnToPrompt?: () => void;
  presentation?: "default" | "studio";
}) {
  const isUser = message.role === "user";
  const studio = presentation === "studio";
  const studioUser = studio && isUser;
  const integrationRequest = isUser ? recognizeBuiltinIntegrationRequest(message.content) : null;
  const content = studio && message.role === "assistant" && !message.chat_question && generationReportStatus
    ? unconfirmedGenerationContent(message, generationReportStatus) : message.content;
  const parts: AssistantPart[] = isUser ? [] : parseAssistantContent(content);
  const { snapshot_id: snapshotId } = message;
  const showVersionChanges = studio && !streaming && projectId === message.project_id &&
    message.role === "assistant" && !message.chat_question &&
    message.generation_status === "completed" && !message.generation_failure &&
    GENERATION_SUCCESS_HEADINGS.has(content.trim());

  return (
    <motion.div
      variants={fadeUp}
      initial="hidden"
      animate="visible"
      data-chat-role={studio ? message.role : undefined}
      role={studioUser ? "article" : undefined}
      aria-label={studioUser ? "Ваше сообщение" : undefined}
      className={cn(
        "flex gap-3 px-4 py-3",
        studio && "px-8 py-3.5",
        studioUser && "justify-end",
      )}
    >
      {!studioUser && <div
        aria-hidden="true"
        className={cn(
          "h-7 w-7 rounded-full flex items-center justify-center shrink-0",
          isUser
            ? "bg-accent-subtle border border-accent/40"
            : "bg-surface-overlay border border-border-default",
          studio && "h-8 w-8 border-0 bg-accent-subtle",
        )}
      >
        {isUser ? (
          <UserIcon className="h-3.5 w-3.5 text-accent" />
        ) : studio ? (
          <Image src="/brand/assistant-robot.svg" alt="" width={32} height={32} className="size-full" />
        ) : (
          <Bot className="h-3.5 w-3.5 text-fg-secondary" />
        )}
      </div>}

      <div className={cn("min-w-0 space-y-1.5", studioUser ? "max-editor-user-message" : "flex-1")}>
        <div className={cn("flex items-center gap-2 text-xs", studioUser && "justify-end")}>
          {!studioUser && <span className="font-semibold text-fg-primary tracking-tight">
            {isUser ? "Вы" : "Yleum"}
          </span>}
          <time dateTime={message.created_at} className="text-fg-tertiary">
            {formatRelativeTime(message.created_at)}
          </time>
        </div>

        <div
          className={cn(
            "text-sm text-fg-primary leading-6 space-y-2",
            studio && (integrationRequest ? "w-full" : "max-editor-message-body w-full"),
          )}
        >
          {!isUser && streaming && projectId && (
            <PassProgressBar projectId={projectId} messageId={message.id} />
          )}

          {/* Live agentic-builder transcript (Claude-Code-style tool steps).
              Not gated on `streaming` — it self-hides when the message has no
              agent steps, and stays visible after the build so the user can
              review what the agent did. */}
          {!isUser && !message.chat_question && projectId && (
            <AgentTranscript
              projectId={projectId}
              messageId={message.id}
              streaming={!!streaming}
              initialSteps={message.agent_steps}
              startedAt={
                message.generation_started_at ??
                (streaming ? message.created_at : null)
              }
              finishedAt={message.generation_finished_at}
              generationStatus={message.generation_status}
            />
          )}

          {!isUser && message.generation_status === "failed" && message.generation_failure && (
            <GenerationFailureCard failure={message.generation_failure} onRetry={onRetry} continueFromDraft={continueFromDraft} onReturnToPrompt={onReturnToPrompt} />
          )}

          {isUser && !!message.reference_images?.length && <div className="flex flex-wrap gap-2" aria-label="Изображения в сообщении">
            {message.reference_images.map((image) => <ReferenceImageThumbnail key={image.id} projectId={message.project_id} image={image} />)}
          </div>}
          {isUser && !!message.font_selections?.length && <ul className="flex flex-wrap gap-2" aria-label="Шрифты в сообщении">
            {message.font_selections.map((font) => <li key={`${font.id}:${font.role}`} className="max-w-full break-words rounded-lg border border-border-subtle px-3 py-2 text-xs text-fg-secondary">
              <strong className="text-fg-primary">{font.family}</strong> · {FONT_ROLE_LABELS[font.role]}
            </li>)}
          </ul>}
          {integrationRequest ? (
            <IntegrationRequestCard request={integrationRequest} text={message.content} />
          ) : isUser ? (
            <UserMessageContent text={message.content} plain={studio} />
          ) : (
            parts.map((p, i) =>
              p.kind === "text" ? (
                <AssistantText key={i} text={p.text} streaming={!!streaming} studio={studio} />
              ) : p.kind === "remix" ? (
                <RemixRecapCard
                  key={i}
                  name={p.name}
                  dna={p.dna}
                  suggestions={p.suggestions}
                  onSuggest={onSuggest}
                />
              ) : p.kind === "app-error" ? (
                <AppErrorCard key={i} part={p} onFix={onFix} />
              ) : (
                <FileChip
                  key={i}
                  path={p.path}
                  body={p.body}
                  closed={p.closed}
                  variant={p.kind}
                />
              ),
            )
          )}

          {showVersionChanges && snapshotId && (
            <VersionChangesDisclosure key={`${projectId}:${snapshotId}`} projectId={projectId} snapshotId={snapshotId} />
          )}

          {streaming &&
            (parts.length === 0 ||
              parts[parts.length - 1].kind === "text") && (
              <span className="inline-block w-[6px] h-[14px] -mb-0.5 ml-0.5 bg-accent animate-pulse align-middle" />
            )}
        </div>

        {isUser &&
          message.selected_elements &&
          message.selected_elements.length > 0 && (
            <SelectedChips items={message.selected_elements} className="pt-1" />
          )}

        {!studio && !isUser &&
          message.tokens_out !== null &&
          message.tokens_in !== null && (
            <div className="text-[11px] font-mono text-fg-tertiary pt-1 flex items-center gap-2">
              <span className="tabular-nums">
                ↑ {message.tokens_in} · ↓ {message.tokens_out} tokens
              </span>
              {message.cost_rub != null && message.cost_rub > 0 && (
                <span
                  title="Списано с кошелька за эту генерацию"
                  className="text-fg-tertiary tabular-nums"
                >
                  · ≈ ₽{message.cost_rub.toFixed(2)}
                </span>
              )}
            </div>
          )}
      </div>
    </motion.div>
  );
}

/* ─────────────────────── assistant text + status card ─────────────────── */

/** Renders an assistant prose chunk, lifting `*Приёмка…*` / `*Свободная
 *  вёрстка…*` status lines out into a live StatusCard. */
function AssistantText({
  text: rawText,
  streaming,
  studio = false,
}: {
  text: string;
  streaming?: boolean;
  studio?: boolean;
}) {
  // Safety net: never render leaked raw code as chat prose, even if the
  // server-side honesty pass is off or the row predates it. Mirrors
  // clean_chat_content on the backend.
  const text = cleanChatProse(rawText);
  if (!text) return null;
  if (!STATUS_RE.test(text)) {
    return <Markdown text={text} collapseTechnical={studio && !streaming} className="break-words text-fg-secondary" />;
  }
  // Split into prose / status segments, preserving order.
  const blocks: { kind: "p" | "s"; text: string }[] = [];
  let prose: string[] = [];
  const flush = () => {
    if (prose.join("\n").trim()) blocks.push({ kind: "p", text: prose.join("\n") });
    prose = [];
  };
  for (const line of text.split("\n")) {
    if (STATUS_RE.test(line)) {
      flush();
      blocks.push({ kind: "s", text: line });
    } else {
      prose.push(line);
    }
  }
  flush();

  return (
    <div className="space-y-2">
      {blocks.map((b, i) =>
        b.kind === "s" ? (
          <StatusCard key={i} raw={b.text} streaming={streaming} />
        ) : (
          <Markdown
            key={i}
            text={b.text.trim()}
            collapseTechnical={studio && !streaming}
            className="break-words text-fg-secondary"
          />
        ),
      )}
    </div>
  );
}

/** Page-rework status. While the message is STREAMING it's a live "working" card
 *  (shimmer sweep + spinning ring + pulsing dots). Once the message is done it
 *  becomes a STATIC "done" card (check, no looping animation) — otherwise a
 *  finished generation would appear to rework the page forever (owner 2026-06-07).
 *  `active` also respects reduced-motion. */
function StatusCard({ raw, streaming }: { raw: string; streaming?: boolean }) {
  const reduce = useReducedMotion();
  const active = !!streaming && !reduce;
  const clean = raw
    .replace(/\*/g, "")
    .replace(/\.{2,}$/, "")
    .replace(/^\s+|\s+$/g, "");
  const isAccept = /Приёмка/i.test(clean);
  const title = streaming
    ? isAccept
      ? "Дорабатываю страницу"
      : "Пересобираю страницу"
    : isAccept
      ? "Страница доработана"
      : "Страница пересобрана";
  const Icon = streaming ? Wand2 : Check;

  return (
    <motion.div
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, ease: EASE_OUT }}
      className={cn(
        "relative overflow-hidden rounded-xl border px-3 py-2.5",
        streaming
          ? "border-accent/25 bg-accent-subtle/40"
          : "border-border-subtle bg-surface-raised/60",
      )}
    >
      {active && (
        <motion.span
          aria-hidden
          className="pointer-events-none absolute inset-y-0 -left-1/2 w-1/2 -skew-x-12 bg-gradient-to-r from-transparent via-accent/15 to-transparent"
          animate={{ x: ["0%", "420%"] }}
          transition={{ duration: 1.8, repeat: Infinity, ease: "easeInOut" }}
        />
      )}
      <div className="relative flex items-center gap-3">
        <span
          className={cn(
            "relative flex h-7 w-7 shrink-0 items-center justify-center rounded-full",
            streaming ? "bg-accent/15" : "bg-surface-overlay",
          )}
        >
          <Icon
            className={cn(
              "h-3.5 w-3.5",
              streaming ? "text-accent" : "text-fg-secondary",
            )}
          />
          {active && (
            <motion.span
              aria-hidden
              className="absolute inset-0 rounded-full border border-transparent border-t-accent"
              animate={{ rotate: 360 }}
              transition={{ duration: 1.1, repeat: Infinity, ease: "linear" }}
            />
          )}
        </span>
        <div className="min-w-0 flex-1">
          <div className="text-[13px] font-medium text-fg-primary">{title}</div>
          <div className="truncate text-[11px] text-fg-tertiary">{clean}</div>
        </div>
        {active && (
          <span aria-hidden className="ml-auto flex shrink-0 items-center gap-1">
            {[0, 1, 2].map((i) => (
              <motion.span
                key={i}
                className="h-1 w-1 rounded-full bg-accent/70"
                animate={{ opacity: [0.3, 1, 0.3] }}
                transition={{ duration: 1, repeat: Infinity, delay: i * 0.18 }}
              />
            ))}
          </span>
        )}
      </div>
    </motion.div>
  );
}

/* ───────────────────────────── app error card ─────────────────────────── */

const CATEGORY_LABEL: Record<AppErrorCategory, string> = {
  build: "Сборка",
  compile: "Компиляция",
  schema: "База данных",
  runtime: "Среда выполнения",
  client: "Браузер",
  incomplete: "Не завершено",
};

/** A calm next action with the exact app diagnostic behind disclosure.
 *  Both continuation and repair use the normal prompt admission pipeline. */
function AppErrorCard({
  part,
  onFix,
}: {
  part: Extract<AssistantPart, { kind: "app-error" }>;
  onFix?: (prompt: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const detail = part.body.trim();
  const diagnostic = [part.title, part.file ? `Файл: ${part.file}` : "", detail]
    .filter(Boolean).join("\n\n");
  const hasDetail = diagnostic.length > 0;
  const isIncomplete = part.category === "incomplete";

  const handleFix = () => {
    if (!onFix) return;
    const label = CATEGORY_LABEL[part.category] ?? "приложение";
    const lines = [`Исправь ошибку в приложении (${label}): ${part.title}.`];
    if (part.file) lines.push(`Файл: ${part.file}.`);
    if (detail) lines.push("", detail);
    onFix(lines.join("\n"));
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, ease: EASE_OUT }}
      role="status"
      className="overflow-hidden rounded-xl border border-border-default bg-surface-raised"
    >
      <div className="flex items-start gap-3 px-3 py-2.5">
        <span
          className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center text-fg-secondary"
        >
          <Pause aria-hidden="true" className="h-4 w-4" />
        </span>
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[13px] font-semibold text-fg-primary">
              Продолжим подготовку
            </span>
          </div>
          <div className="flex items-center gap-2 pt-0.5">
            {part.fixable && onFix && (
              <button
                type="button"
                onClick={() => (isIncomplete ? onFix("продолжи") : handleFix())}
                className="inline-flex min-h-11 items-center gap-1.5 rounded-xl border border-border-default px-3 text-sm font-medium text-fg-primary transition-colors hover:bg-surface-overlay"
              >
                <ChevronRight aria-hidden="true" className="h-3 w-3" />
                {isIncomplete ? "Продолжить сборку" : "Продолжить подготовку"}
              </button>
            )}
            {hasDetail && (
              <button
                type="button"
                aria-expanded={open}
                onClick={() => setOpen((o) => !o)}
                className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-xs text-fg-tertiary transition-colors hover:text-fg-secondary"
              >
                <ChevronRight
                  className={cn(
                    "h-3.5 w-3.5 transition-transform",
                    open && "rotate-90",
                  )}
                />
                {open ? "Скрыть детали" : "Детали"}
              </button>
            )}
          </div>
        </div>
      </div>

      <AnimatePresence initial={false}>
        {open && hasDetail && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2, ease: EASE_OUT }}
            className="overflow-hidden border-t border-border-subtle"
          >
            <pre className="scrollbar-elegant max-h-64 overflow-auto bg-surface-base/60 p-3 text-[11px] font-mono leading-relaxed text-fg-secondary">
              {diagnostic}
            </pre>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}

/* ───────────────────────────── file / edit chip ───────────────────────── */

function FileChip({
  path,
  body,
  closed,
  variant = "file",
}: {
  path: string;
  body: string;
  closed: boolean;
  variant?: "file" | "edit";
}) {
  const [open, setOpen] = useState(false);
  const size = new Blob([body]).size;
  const isEdit = variant === "edit";
  const Icon = isEdit ? PencilLine : FileCode2;
  // For an edit the body is a SEARCH/REPLACE diff — its byte size is noise; show
  // a plain "правка" label instead. The diff stays available behind the chevron.
  const meta = isEdit ? "правка" : formatBytes(size);

  return (
    <div className="rounded-lg border border-border-subtle bg-surface-raised overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-2 px-2.5 py-1.5 hover:bg-surface-overlay transition-colors"
      >
        <ChevronRight
          className={cn(
            "h-3.5 w-3.5 text-fg-tertiary transition-transform shrink-0",
            open && "rotate-90",
          )}
        />
        <Icon className="h-3.5 w-3.5 text-fg-secondary shrink-0" />
        <span className="font-mono text-xs text-fg-primary truncate">
          {isEdit ? `Правка · ${path}` : path}
        </span>
        <span className="ml-auto flex items-center gap-2 text-[11px] font-mono text-fg-tertiary shrink-0">
          {closed ? (
            meta
          ) : (
            <>
              <Loader2 className="h-3 w-3 animate-spin" />
              <span>{meta}</span>
            </>
          )}
        </span>
      </button>

      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2, ease: EASE_OUT }}
            className="overflow-hidden border-t border-border-subtle"
          >
            <pre className="text-[11px] font-mono text-fg-secondary leading-relaxed p-3 overflow-x-auto max-h-80 overflow-y-auto bg-surface-base scrollbar-elegant">
              {body}
            </pre>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
