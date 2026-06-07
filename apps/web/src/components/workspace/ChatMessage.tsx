"use client";

import { useState } from "react";
import {
  Bot,
  User as UserIcon,
  FileCode2,
  PencilLine,
  ChevronRight,
  Loader2,
  Wand2,
  Target,
  Palette,
  Layers,
  Info,
  Sparkles,
} from "lucide-react";
import { motion, AnimatePresence, useReducedMotion } from "framer-motion";
import type { Message } from "@/lib/api/types";
import { EASE_OUT, fadeUp } from "@/lib/motion";
import { formatRelativeTime, cn } from "@/lib/utils";
import {
  parseAssistantContent,
  formatBytes,
  type AssistantPart,
} from "@/lib/parse-assistant";
import { SelectedChips } from "./SelectedChips";
import { PassProgressBar } from "./PassProgressBar";

// The onboarding quiz folds its answers into the user prompt after this marker
// (see OnboardingQuiz.compile). We split on it to render the answers as chips
// instead of a wall of bullet text.
const QUIZ_MARKER = "— Бриф из опроса —";
// Backend status lines streamed mid-generation (messages.py): acceptance retry
// and the freeform→catalog fallback. They arrive as `*…*` italic prose; we lift
// them out into a live "working" card instead of plain italic text.
const STATUS_RE = /\*+\s*(Приёмка|Свободная вёрстка)[^*\n]*/i;

export function ChatMessage({
  message,
  streaming,
  projectId,
}: {
  message: Message;
  streaming?: boolean;
  /**
   * Required for the B.3 multipass progress bar — it subscribes to
   * `["passes", projectId, message.id]` in React Query cache. Optional
   * so callers can omit it for non-streaming chat replays / e2e
   * screenshots, in which case the progress bar simply never renders.
   */
  projectId?: string;
}) {
  const isUser = message.role === "user";
  const quiz = isUser ? parseQuizBrief(message.content) : null;
  const parts: AssistantPart[] = isUser ? [] : parseAssistantContent(message.content);

  return (
    <motion.div
      variants={fadeUp}
      initial="hidden"
      animate="visible"
      className="flex gap-3 px-4 py-3"
    >
      <div
        className={
          isUser
            ? "h-7 w-7 rounded-full bg-accent-subtle border border-accent/40 flex items-center justify-center shrink-0"
            : "h-7 w-7 rounded-full bg-surface-overlay border border-border-default flex items-center justify-center shrink-0"
        }
      >
        {isUser ? (
          <UserIcon className="h-3.5 w-3.5 text-accent" />
        ) : (
          <Bot className="h-3.5 w-3.5 text-fg-secondary" />
        )}
      </div>

      <div className="flex-1 min-w-0 space-y-1.5">
        <div className="flex items-center gap-2 text-xs">
          <span className="font-semibold text-fg-primary tracking-tight">
            {isUser ? "Вы" : "Omnia"}
          </span>
          <span className="text-fg-tertiary">
            {formatRelativeTime(message.created_at)}
          </span>
        </div>

        <div className="text-sm text-fg-primary leading-6 space-y-2">
          {!isUser && streaming && projectId && (
            <PassProgressBar projectId={projectId} messageId={message.id} />
          )}

          {isUser ? (
            quiz ? (
              <QuizSummary idea={quiz.idea} items={quiz.items} />
            ) : (
              <UserBubble text={message.content} />
            )
          ) : (
            parts.map((p, i) =>
              p.kind === "text" ? (
                <AssistantText key={i} text={p.text} />
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

        {!isUser &&
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

/* ───────────────────────────── user message ───────────────────────────── */

/** The user's words in a soft accent-tinted speech bubble. */
function UserBubble({ text }: { text: string }) {
  return (
    <div className="inline-block max-w-full whitespace-pre-wrap break-words rounded-2xl rounded-tl-md border border-accent/20 bg-accent-subtle/50 px-3.5 py-2 text-sm leading-6 text-fg-primary">
      {text}
    </div>
  );
}

/* ───────────────────────────── quiz answers ───────────────────────────── */

type QuizItem = { label: string; value: string };

/** Extracts the original idea + the quiz answer bullets from a compiled brief. */
function parseQuizBrief(
  content: string,
): { idea: string; items: QuizItem[] } | null {
  const idx = content.indexOf(QUIZ_MARKER);
  if (idx === -1) return null;
  const idea = content.slice(0, idx).trim();
  const rest = content.slice(idx + QUIZ_MARKER.length);
  const items: QuizItem[] = [];
  for (const line of rest.split("\n")) {
    const m = line.match(/^\s*[•\-*]\s*([^:]+):\s*(.+)$/);
    if (m) items.push({ label: m[1].trim(), value: m[2].trim() });
  }
  return items.length ? { idea, items } : null;
}

function iconForLabel(label: string) {
  const l = label.toLowerCase();
  if (l.includes("задач")) return Target;
  if (l.includes("настроен") || l.includes("тон")) return Sparkles;
  if (l.includes("стиль") || l.includes("цвет") || l.includes("палитр"))
    return Palette;
  if (l.includes("блок") || l.includes("раздел")) return Layers;
  return Info;
}

const HEX_RE = /#[0-9a-fA-F]{3,8}/g;

/** Quiz answers rendered as tappable-looking chips, staggered in. */
function QuizSummary({ idea, items }: { idea: string; items: QuizItem[] }) {
  // Flatten "Обязательные блоки: A, B, C" into one chip per block.
  const chips: { label?: string; value: string; Icon: typeof Info; hexes?: string[] }[] =
    [];
  for (const it of items) {
    const Icon = iconForLabel(it.label);
    const l = it.label.toLowerCase();
    if (l.includes("блок") || l.includes("раздел")) {
      it.value
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean)
        .forEach((v) => chips.push({ value: v, Icon: Layers }));
    } else {
      const hexes = it.value.match(HEX_RE) ?? undefined;
      const value = it.value.replace(/\(палитра:[^)]*\)/i, "").trim();
      chips.push({ label: it.label, value, Icon, hexes: hexes ?? undefined });
    }
  }

  return (
    <div className="space-y-2">
      {idea && <UserBubble text={idea} />}
      <div className="rounded-2xl border border-border-subtle bg-surface-raised/60 p-2.5">
        <div className="mb-2 flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wider text-fg-tertiary">
          <Sparkles className="h-3 w-3 text-accent" />
          Ответы опроса
        </div>
        <div className="flex flex-wrap gap-1.5">
          {chips.map((c, i) => (
            <motion.span
              key={i}
              initial={{ opacity: 0, y: 4 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.2, ease: EASE_OUT, delay: i * 0.03 }}
              className="inline-flex max-w-full items-center gap-1.5 rounded-lg border border-border-subtle bg-surface-overlay/70 px-2 py-1"
            >
              <c.Icon className="h-3 w-3 shrink-0 text-accent" />
              <span className="flex min-w-0 flex-col leading-tight">
                {c.label && (
                  <span className="text-[9px] uppercase tracking-wide text-fg-tertiary">
                    {c.label}
                  </span>
                )}
                <span className="truncate text-xs text-fg-primary">
                  {c.value}
                </span>
              </span>
              {c.hexes && c.hexes.length > 0 && (
                <span className="ml-0.5 flex shrink-0 items-center gap-0.5">
                  {c.hexes.slice(0, 5).map((h, j) => (
                    <span
                      key={j}
                      className="h-3 w-3 rounded-[3px] border border-black/20 shadow-sm"
                      style={{ backgroundColor: h }}
                      title={h}
                    />
                  ))}
                </span>
              )}
            </motion.span>
          ))}
        </div>
      </div>
    </div>
  );
}

/* ─────────────────────── assistant text + status card ─────────────────── */

/** Renders an assistant prose chunk, lifting `*Приёмка…*` / `*Свободная
 *  вёрстка…*` status lines out into a live StatusCard. */
function AssistantText({ text }: { text: string }) {
  if (!STATUS_RE.test(text)) {
    return (
      <div className="whitespace-pre-wrap break-words text-fg-secondary">
        {text}
      </div>
    );
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
          <StatusCard key={i} raw={b.text} />
        ) : (
          <div
            key={i}
            className="whitespace-pre-wrap break-words text-fg-secondary"
          >
            {b.text.trim()}
          </div>
        ),
      )}
    </div>
  );
}

/** A live "the page is being reworked" card — shimmer sweep + spinning ring +
 *  pulsing dots. Replaces the old flat italic status line. Reduced-motion safe. */
function StatusCard({ raw }: { raw: string }) {
  const reduce = useReducedMotion();
  const clean = raw
    .replace(/\*/g, "")
    .replace(/\.{2,}$/, "")
    .replace(/^\s+|\s+$/g, "");
  const isAccept = /Приёмка/i.test(clean);
  const title = isAccept ? "Дорабатываю страницу" : "Пересобираю страницу";

  return (
    <motion.div
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, ease: EASE_OUT }}
      className="relative overflow-hidden rounded-xl border border-accent/25 bg-accent-subtle/40 px-3 py-2.5"
    >
      {!reduce && (
        <motion.span
          aria-hidden
          className="pointer-events-none absolute inset-y-0 -left-1/2 w-1/2 -skew-x-12 bg-gradient-to-r from-transparent via-accent/15 to-transparent"
          animate={{ x: ["0%", "420%"] }}
          transition={{ duration: 1.8, repeat: Infinity, ease: "easeInOut" }}
        />
      )}
      <div className="relative flex items-center gap-3">
        <span className="relative flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-accent/15">
          <Wand2 className="h-3.5 w-3.5 text-accent" />
          {!reduce && (
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
        <span aria-hidden className="ml-auto flex shrink-0 items-center gap-1">
          {[0, 1, 2].map((i) => (
            <motion.span
              key={i}
              className="h-1 w-1 rounded-full bg-accent/70"
              animate={reduce ? undefined : { opacity: [0.3, 1, 0.3] }}
              transition={{ duration: 1, repeat: Infinity, delay: i * 0.18 }}
            />
          ))}
        </span>
      </div>
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
