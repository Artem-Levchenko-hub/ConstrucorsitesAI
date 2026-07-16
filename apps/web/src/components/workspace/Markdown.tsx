"use client";

import { Fragment, type ReactNode } from "react";
import { cn } from "@/lib/utils";

/**
 * Lightweight, dependency-free Markdown renderer for assistant chat prose.
 *
 * Why hand-rolled instead of `react-markdown`: a new runtime dependency here has
 * repeatedly broken the web build in this repo (sonner dep-drift), and the chat
 * only needs a small, well-known subset. Everything is rendered by building React
 * nodes — never `dangerouslySetInnerHTML` — so there is no HTML-injection surface.
 *
 * Supported: headings (#/##/###), unordered (-,*) and ordered (1.) lists,
 * fenced ``` code blocks, blockquotes (>), inline `code`, **bold**, *italic*,
 * and [links](url). Anything else falls through as a plain paragraph.
 */
export function Markdown({ text, className }: { text: string; className?: string }) {
  const blocks = parseBlocks(text);
  return (
    <div className={cn("space-y-2 text-fg-secondary", className)}>
      {blocks.map((b, i) => (
        <Block key={i} block={b} />
      ))}
    </div>
  );
}

/* ─────────────────────────── block model ──────────────────────────── */

type Block =
  | { kind: "h"; level: 1 | 2 | 3; text: string }
  | { kind: "p"; text: string }
  | { kind: "quote"; text: string }
  | { kind: "code"; text: string }
  | { kind: "ul"; items: string[] }
  | { kind: "ol"; items: string[] };

const H_RE = /^(#{1,3})\s+(.*)$/;
const UL_RE = /^\s*[-*]\s+(.*)$/;
const OL_RE = /^\s*\d+[.)]\s+(.*)$/;
const QUOTE_RE = /^\s*>\s?(.*)$/;
const FENCE_RE = /^\s*```/;

function parseBlocks(src: string): Block[] {
  const lines = src.replace(/\r\n/g, "\n").split("\n");
  const blocks: Block[] = [];
  let para: string[] = [];

  const flushPara = () => {
    const t = para.join("\n").trim();
    if (t) blocks.push({ kind: "p", text: t });
    para = [];
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    // Fenced code block — consume until the closing fence (or EOF).
    if (FENCE_RE.test(line)) {
      flushPara();
      const buf: string[] = [];
      i++;
      while (i < lines.length && !FENCE_RE.test(lines[i])) {
        buf.push(lines[i]);
        i++;
      }
      blocks.push({ kind: "code", text: buf.join("\n") });
      continue;
    }

    const h = H_RE.exec(line);
    if (h) {
      flushPara();
      blocks.push({ kind: "h", level: h[1].length as 1 | 2 | 3, text: h[2].trim() });
      continue;
    }

    if (UL_RE.test(line)) {
      flushPara();
      const items: string[] = [];
      while (i < lines.length && UL_RE.test(lines[i])) {
        items.push(UL_RE.exec(lines[i])![1]);
        i++;
      }
      i--;
      blocks.push({ kind: "ul", items });
      continue;
    }

    if (OL_RE.test(line)) {
      flushPara();
      const items: string[] = [];
      while (i < lines.length && OL_RE.test(lines[i])) {
        items.push(OL_RE.exec(lines[i])![1]);
        i++;
      }
      i--;
      blocks.push({ kind: "ol", items });
      continue;
    }

    if (QUOTE_RE.test(line)) {
      flushPara();
      const buf: string[] = [];
      while (i < lines.length && QUOTE_RE.test(lines[i])) {
        buf.push(QUOTE_RE.exec(lines[i])![1]);
        i++;
      }
      i--;
      blocks.push({ kind: "quote", text: buf.join("\n") });
      continue;
    }

    if (line.trim() === "") {
      flushPara();
      continue;
    }

    para.push(line);
  }
  flushPara();
  return blocks;
}

function Block({ block }: { block: Block }) {
  switch (block.kind) {
    case "h": {
      const cls =
        block.level === 1
          ? "text-[15px] font-semibold text-fg-primary"
          : block.level === 2
            ? "text-[14px] font-semibold text-fg-primary"
            : "text-[13px] font-semibold text-fg-primary";
      return <p className={cn("mt-1", cls)}>{renderInline(block.text)}</p>;
    }
    case "p":
      return (
        <p className="break-words leading-6">{renderInline(block.text)}</p>
      );
    case "quote":
      return (
        <blockquote className="border-l-2 border-accent/40 pl-3 text-fg-tertiary italic leading-6">
          {renderInline(block.text)}
        </blockquote>
      );
    case "code":
      return (
        <pre className="scrollbar-elegant overflow-auto rounded-lg border border-border-subtle bg-surface-base/70 p-2.5 font-mono text-[12px] leading-relaxed text-fg-secondary">
          <code>{block.text}</code>
        </pre>
      );
    case "ul":
      return (
        <ul className="ml-1 space-y-1">
          {block.items.map((it, i) => (
            <li key={i} className="flex gap-2 leading-6">
              <span className="mt-[9px] h-1 w-1 shrink-0 rounded-full bg-accent/70" />
              <span className="break-words">{renderInline(it)}</span>
            </li>
          ))}
        </ul>
      );
    case "ol":
      return (
        <ol className="ml-1 space-y-1">
          {block.items.map((it, i) => (
            <li key={i} className="flex gap-2 leading-6">
              <span className="shrink-0 font-mono text-[12px] text-accent/80">
                {i + 1}.
              </span>
              <span className="break-words">{renderInline(it)}</span>
            </li>
          ))}
        </ol>
      );
  }
}

/* ─────────────────────────── inline model ─────────────────────────── */

/**
 * Inline tokenizer. `code` spans are protected first (their contents are never
 * re-parsed), then links, bold, and italic — each matched as a non-overlapping
 * pass over the remaining plain-text runs. Output is a flat array of React nodes.
 */
function renderInline(text: string): ReactNode {
  return <InlinePass text={text} />;
}

const INLINE_RE =
  /(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*]+\*|_[^_]+_)|(\[[^\]]+\]\([^)\s]+\))/;

function InlinePass({ text }: { text: string }): ReactNode {
  const out: ReactNode[] = [];
  let rest = text;
  let key = 0;

  while (rest.length > 0) {
    const m = INLINE_RE.exec(rest);
    if (!m) {
      out.push(<Fragment key={key++}>{rest}</Fragment>);
      break;
    }
    if (m.index > 0) {
      out.push(<Fragment key={key++}>{rest.slice(0, m.index)}</Fragment>);
    }
    const tok = m[0];
    if (tok.startsWith("`")) {
      out.push(
        <code
          key={key++}
          className="rounded bg-surface-overlay px-1 py-0.5 font-mono text-[0.85em] text-fg-primary"
        >
          {tok.slice(1, -1)}
        </code>,
      );
    } else if (tok.startsWith("**")) {
      out.push(
        <strong key={key++} className="font-semibold text-fg-primary">
          {tok.slice(2, -2)}
        </strong>,
      );
    } else if (tok.startsWith("[")) {
      const linkM = /^\[([^\]]+)\]\(([^)\s]+)\)$/.exec(tok)!;
      const href = linkM[2];
      const safe = /^(https?:\/\/|\/)/.test(href);
      out.push(
        safe ? (
          <a
            key={key++}
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            className="text-accent underline decoration-accent/40 underline-offset-2 hover:decoration-accent"
          >
            {linkM[1]}
          </a>
        ) : (
          <Fragment key={key++}>{linkM[1]}</Fragment>
        ),
      );
    } else {
      // *italic* or _italic_
      out.push(
        <em key={key++} className="italic">
          {tok.slice(1, -1)}
        </em>,
      );
    }
    rest = rest.slice(m.index + tok.length);
  }
  return <>{out}</>;
}
