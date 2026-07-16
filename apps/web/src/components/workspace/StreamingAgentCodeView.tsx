"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { motion, AnimatePresence } from "framer-motion";
import { formatBytes } from "@/lib/parse-assistant";
import { cn } from "@/lib/utils";
import { fileIcon } from "@/lib/file-icons";
import type { AgentStep } from "@/lib/api/types";

/**
 * Live "building before your eyes" code view for AGENTIC / full-stack builds.
 *
 * A fullstack build writes files via agent TOOLS (`write_file`), not `<file>`
 * blocks in the chat stream — so the freeform `StreamingCodeView` (which parses
 * `<file>` out of `content`) would sit on its empty state the whole build. Here
 * we drive the view off the live `agent.step` stream instead: `usePromptStream`
 * pushes every step into `["agent-steps", projectId, messageId]` as it arrives
 * (whether or not this tab is mounted), so opening «Код» mid-build shows every
 * file already written plus the one being written right now.
 *
 * The step `detail` for a write carries the file content (capped ~1400 chars on
 * the backend), enough to watch code stream in; the committed CodeView shows the
 * full file once the build finishes.
 */

type LiveFile = { path: string; body: string; chars: number; index: number };

export function StreamingAgentCodeView({
  projectId,
  messageId,
}: {
  projectId: string;
  messageId: string;
}) {
  const qc = useQueryClient();
  const { data: steps } = useQuery<AgentStep[]>({
    queryKey: ["agent-steps", projectId, messageId],
    // Pushed via setQueryData from usePromptStream's `agent.step` handler; this
    // observer just re-renders on each push (same pattern as AgentTranscript).
    queryFn: () =>
      qc.getQueryData<AgentStep[]>(["agent-steps", projectId, messageId]) ?? [],
    enabled: !!projectId && !!messageId,
    staleTime: Infinity,
  });

  // Files written so far, in first-appearance order, latest content per path.
  const { files, writingPath } = useMemo(() => {
    const byPath = new Map<string, LiveFile>();
    let order = 0;
    // The path being written RIGHT NOW — set on a write, cleared by any later
    // non-write progress step (build/verify), so the «пишется…» pulse doesn't
    // linger through the post-write compile+prove tail.
    let writingNow: string | null = null;
    for (const s of steps ?? []) {
      const path = (s.path ?? "").trim();
      const tool = s.tool ?? "";
      // Only a SUCCESSFUL write makes a file appear. read_file/grep/list_dir also
      // carry a path but aren't creations; a FAILED write (edit_file SEARCH miss —
      // routine in the build→fix loop) carries error text in `detail`, which must
      // never clobber the good content already shown for that path.
      const isWrite =
        (tool === "write_file" || tool === "edit_file") &&
        !!path &&
        s.kind === "step" &&
        s.ok !== false;
      if (!isWrite) {
        // A real non-write step (build/read/verify/probe) means nothing is being
        // written at this instant. Meta rows (escalate/retry/stalled) don't clear.
        if (s.kind === "step") writingNow = null;
        continue;
      }
      const detail = s.detail ?? "";
      // The write header «N символов записано:» carries the REAL full size even
      // when the body itself is capped — use it for the badge, strip it from code.
      const m = /^(\d+)\s+символов записано:/.exec(detail);
      const headerChars = m ? Number(m[1]) : null;
      const body = detail.replace(/^\d+\s+символов записано:\n\n/, "");
      const existing = byPath.get(path);
      byPath.set(path, {
        path,
        body: body || existing?.body || "",
        chars: headerChars ?? existing?.chars ?? new Blob([body]).size,
        index: existing ? existing.index : order++,
      });
      writingNow = path;
    }
    const arr = [...byPath.values()].sort((a, b) => a.index - b.index);
    return { files: arr, writingPath: writingNow };
  }, [steps]);

  const [pinned, setPinned] = useState<string | null>(null);
  const activePath =
    pinned && files.some((f) => f.path === pinned) ? pinned : writingPath;
  const active = files.find((f) => f.path === activePath) ?? null;
  const isWritingActive = active?.path === writingPath;

  const preRef = useRef<HTMLPreElement>(null);
  useEffect(() => {
    if (isWritingActive && preRef.current) {
      preRef.current.scrollTop = preRef.current.scrollHeight;
    }
  }, [active?.body, isWritingActive]);

  if (files.length === 0) {
    return (
      <div className="h-full flex flex-col items-center justify-center gap-3 text-fg-tertiary">
        <span className="relative flex items-center justify-center">
          <span className="absolute inline-flex h-10 w-10 rounded-full bg-accent/20 animate-ping" />
          <span className="relative inline-flex h-3 w-3 rounded-full bg-accent" />
        </span>
        <span className="text-sm font-medium text-fg-secondary">
          AI пишет файлы приложения…
        </span>
        <span className="text-xs text-fg-tertiary">
          Появятся здесь по мере создания
        </span>
      </div>
    );
  }

  const activeName = active ? (active.path.split("/").pop() ?? active.path) : "";
  const { Icon: ActiveIcon, color: activeColor } = fileIcon(activeName);

  return (
    <div className="h-full flex bg-surface-base">
      <div className="w-60 shrink-0 border-r border-border-subtle flex flex-col overflow-hidden">
        <div className="h-9 px-3 flex items-center gap-1.5 border-b border-border-subtle/60">
          <span className="relative flex h-2 w-2">
            <span className="absolute inline-flex h-full w-full rounded-full bg-accent/60 animate-ping" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-accent" />
          </span>
          <span className="text-xs font-mono text-fg-tertiary uppercase tracking-wider">
            Файлы · пишутся
          </span>
          <span className="ml-auto text-[11px] font-mono text-fg-tertiary">
            {files.length}
          </span>
        </div>
        <div className="flex-1 overflow-y-auto py-1.5 scrollbar-elegant">
          <AnimatePresence initial={false}>
            {files.map((f) => {
              const isActive = f.path === activePath;
              const isWriting = f.path === writingPath;
              const { Icon, color } = fileIcon(f.path.split("/").pop() ?? f.path);
              return (
                <motion.button
                  key={f.path}
                  type="button"
                  initial={{ opacity: 0, x: -6 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ duration: 0.2 }}
                  onClick={() => setPinned(f.path)}
                  className={cn(
                    "w-full text-left px-3 py-1.5 flex items-center gap-2 transition-colors",
                    isActive
                      ? "bg-surface-overlay text-fg-primary"
                      : "text-fg-secondary hover:bg-surface-raised hover:text-fg-primary",
                  )}
                >
                  <Icon className={cn("h-3.5 w-3.5 shrink-0", color)} />
                  <span className="font-mono text-xs truncate flex-1 min-w-0">
                    {f.path}
                  </span>
                  {isWriting ? (
                    <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse shrink-0" />
                  ) : (
                    <span className="text-[10px] font-mono text-fg-tertiary shrink-0">
                      {formatBytes(f.chars)}
                    </span>
                  )}
                </motion.button>
              );
            })}
          </AnimatePresence>
        </div>
      </div>

      <div className="flex-1 flex flex-col min-w-0">
        {/* Live "writing now" pulse line at the top of the stream. */}
        <div
          className={cn(
            "h-0.5 w-full bg-gradient-to-r from-transparent via-accent to-transparent transition-opacity duration-300",
            isWritingActive ? "opacity-100 animate-pulse" : "opacity-0",
          )}
        />
        <div className="h-9 px-3 flex items-center gap-2 shrink-0 border-b border-border-subtle/60">
          <ActiveIcon className={cn("h-3.5 w-3.5", activeColor)} />
          <span className="font-mono text-xs text-fg-primary truncate">
            {active?.path}
          </span>
          {isWritingActive && (
            <span className="text-[10px] font-mono text-accent uppercase tracking-wider">
              пишется…
            </span>
          )}
        </div>
        <pre
          ref={preRef}
          className="flex-1 overflow-auto m-0 p-3 text-[12px] leading-[1.55] font-mono text-fg-secondary bg-surface-base whitespace-pre-wrap break-words scrollbar-elegant"
        >
          {active?.body}
          {isWritingActive && (
            <span className="inline-block w-2 h-4 -mb-0.5 ml-0.5 bg-accent/80 animate-pulse align-middle" />
          )}
        </pre>
      </div>
    </div>
  );
}
