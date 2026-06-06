"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { FileCode2, Folder } from "lucide-react";
import { collectStreamingFilesPartial, formatBytes } from "@/lib/parse-assistant";
import { cn } from "@/lib/utils";

/**
 * Live "building before your eyes" code view for ANY stack — used while the
 * assistant streams, when a visual morph-preview isn't possible (fullstack /
 * React) or when the user opens the Код tab mid-build. Parses the partial files
 * out of the streaming `content` and shows the one currently being written,
 * caret blinking, auto-scrolling to the newest line. The committed CodeView
 * (snapshot-backed) takes over once generation finishes.
 *
 * Deliberately separate from CodeView (R-01): different data source (live
 * assistant text vs a fetched snapshot), so neither carries the other's
 * branches.
 */
export function StreamingCodeView({ content }: { content: string }) {
  // content grows monotonically — length is a cheap proxy to re-parse only when
  // new tokens arrived (same memo trick as StreamingPreviewFrame).
  const files = useMemo(
    () => collectStreamingFilesPartial(content),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [content.length],
  );
  const paths = useMemo(() => Object.keys(files), [files]);
  // The last parsed file is the one still being written.
  const writingPath = paths.length ? paths[paths.length - 1] : null;

  // Auto-follow the file being written unless the user pinned another.
  const [pinned, setPinned] = useState<string | null>(null);
  const active = pinned && paths.includes(pinned) ? pinned : writingPath;

  const preRef = useRef<HTMLPreElement>(null);
  useEffect(() => {
    // Keep the newest line in view while watching the active file stream in.
    if (active === writingPath && preRef.current) {
      preRef.current.scrollTop = preRef.current.scrollHeight;
    }
  }, [content.length, active, writingPath]);

  if (paths.length === 0) {
    return (
      <div className="h-full flex flex-col items-center justify-center gap-2 text-xs text-fg-tertiary">
        <FileCode2 className="h-5 w-5 animate-pulse" />
        <span>AI пишет код…</span>
      </div>
    );
  }

  const activeBody = active ? (files[active] ?? "") : "";
  const isWritingActive = active === writingPath;

  return (
    <div className="h-full flex bg-surface-base">
      <div className="w-56 shrink-0 border-r border-border-subtle flex flex-col overflow-hidden">
        <div className="h-9 px-3 flex items-center gap-1.5">
          <Folder className="h-3.5 w-3.5 text-fg-tertiary" />
          <span className="text-xs font-mono text-fg-tertiary uppercase tracking-wider">
            Файлы
          </span>
          <span className="ml-auto text-[11px] font-mono text-fg-tertiary">
            {paths.length}
          </span>
        </div>
        <div className="flex-1 overflow-y-auto py-1.5 scrollbar-elegant">
          {paths.map((path) => {
            const size = new Blob([files[path] ?? ""]).size;
            const isActive = path === active;
            const isWriting = path === writingPath;
            return (
              <button
                key={path}
                type="button"
                onClick={() => setPinned(path)}
                className={cn(
                  "w-full text-left px-3 py-1.5 flex items-center gap-2 transition-colors",
                  isActive
                    ? "bg-surface-overlay text-fg-primary"
                    : "text-fg-secondary hover:bg-surface-raised hover:text-fg-primary",
                )}
              >
                <FileCode2 className="h-3.5 w-3.5 shrink-0 text-fg-tertiary" />
                <span className="font-mono text-xs truncate flex-1 min-w-0">
                  {path}
                </span>
                {isWriting ? (
                  <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse shrink-0" />
                ) : (
                  <span className="text-[10px] font-mono text-fg-tertiary shrink-0">
                    {formatBytes(size)}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      </div>

      <div className="flex-1 flex flex-col min-w-0">
        <div className="h-9 px-3 flex items-center gap-2 shrink-0">
          <FileCode2 className="h-3.5 w-3.5 text-fg-tertiary" />
          <span className="font-mono text-xs text-fg-primary truncate">
            {active}
          </span>
          {isWritingActive && (
            <span className="text-[11px] text-accent font-mono animate-pulse">
              пишется…
            </span>
          )}
        </div>
        <pre
          ref={preRef}
          className="flex-1 overflow-auto m-0 p-3 text-[12px] leading-[1.55] font-mono text-fg-secondary bg-surface-base whitespace-pre scrollbar-elegant"
        >
          {activeBody}
          {isWritingActive && (
            <span className="inline-block w-[7px] h-[14px] -mb-[2px] bg-accent/80 animate-pulse" />
          )}
        </pre>
      </div>
    </div>
  );
}
