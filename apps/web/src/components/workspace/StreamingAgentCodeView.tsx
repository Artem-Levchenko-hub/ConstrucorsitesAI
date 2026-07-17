"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { motion } from "framer-motion";
import { FolderOpen } from "lucide-react";
import { formatBytes } from "@/lib/parse-assistant";
import { cn } from "@/lib/utils";
import { fileIcon } from "@/lib/file-icons";
import { buildFileTree, type TreeNode } from "@/lib/file-tree";
import type { AgentStep } from "@/lib/api/types";

/**
 * Live "building before your eyes" code view for AGENTIC / full-stack builds.
 *
 * A fullstack build touches files via agent TOOLS (read_file / write_file), not
 * `<file>` blocks in the chat stream — so the freeform `StreamingCodeView` would
 * sit on its empty state the whole build. Here we drive the view off the live
 * `agent.step` stream: `usePromptStream` pushes every step into
 * `["agent-steps", projectId, messageId]` as it arrives (whether or not this tab
 * is mounted), so opening «Код» mid-build shows the file tree lighting up as the
 * agent reads and writes — the file it's on RIGHT NOW glows and pulses, and
 * hovering any file explains why it's lit.
 */

type Activity = {
  kind: "read" | "write";
  action: string; // human step phrase, e.g. «Читаю компонент room»
  body: string; // file content (write) or read output
  chars: number;
  order: number;
};

const READ_TOOLS = new Set(["read_file", "grep", "list_dir"]);
const WRITE_TOOLS = new Set(["write_file", "edit_file"]);
const WRITE_HEADER = /^(\d+)\s+символов записано:\n\n/;

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
    queryFn: () =>
      qc.getQueryData<AgentStep[]>(["agent-steps", projectId, messageId]) ?? [],
    enabled: !!projectId && !!messageId,
    staleTime: Infinity,
  });

  // Files the agent has touched, latest activity per path. `currentPath` is the
  // file it's on right now; `activeNow` is false once it moves to a non-file
  // step (build/verify), so the pulse doesn't linger through the compile tail.
  const { tree, byPath, currentPath, activeNow, currentAction } = useMemo(() => {
    const map = new Map<string, Activity>();
    let order = 0;
    let cur: string | null = null;
    let live = false;
    let action = "";
    for (const s of steps ?? []) {
      const path = (s.path ?? "").trim();
      const tool = s.tool ?? "";
      const isRead = READ_TOOLS.has(tool);
      const isWrite = WRITE_TOOLS.has(tool);
      if (!path || s.kind !== "step" || (!isRead && !isWrite)) {
        // A real non-file step (build/probe/verify) → the agent isn't on a file.
        if (s.kind === "step") live = false;
        continue;
      }
      const kind: "read" | "write" = isWrite ? "write" : "read";
      const failedWrite = isWrite && s.ok === false;
      const detail = s.detail ?? "";
      const m = isWrite ? WRITE_HEADER.exec(detail) : null;
      const body = isWrite ? detail.replace(WRITE_HEADER, "") : detail;
      const existing = map.get(path);
      map.set(path, {
        kind,
        action: s.action || existing?.action || "",
        // A failed write carries error text — never clobber good content shown.
        body: failedWrite ? existing?.body ?? "" : body || existing?.body || "",
        chars: m ? Number(m[1]) : existing?.chars ?? new Blob([body]).size,
        order: existing ? existing.order : order++,
      });
      cur = path;
      live = true;
      action = s.action || "";
    }
    const entries = [...map.entries()]
      .sort((a, b) => a[1].order - b[1].order)
      .map(([path, data]) => ({ path, data }));
    return {
      tree: buildFileTree(entries),
      byPath: map,
      currentPath: cur,
      activeNow: live,
      currentAction: action,
    };
  }, [steps]);

  const [pinned, setPinned] = useState<string | null>(null);
  const activePath = pinned && byPath.has(pinned) ? pinned : currentPath;
  const active = activePath ? byPath.get(activePath) ?? null : null;
  const isActiveWriting =
    activeNow && activePath === currentPath && active?.kind === "write";

  const preRef = useRef<HTMLPreElement>(null);
  useEffect(() => {
    if (isActiveWriting && preRef.current) {
      preRef.current.scrollTop = preRef.current.scrollHeight;
    }
  }, [active?.body, isActiveWriting]);

  // Hover tooltip — fixed to the viewport so the sidebar's overflow never clips
  // it. Explains WHY a file is lit (reading / writing, and the agent's phrase).
  const [tip, setTip] = useState<{
    x: number;
    y: number;
    kind: "read" | "write";
    action: string;
    status: string;
  } | null>(null);

  if (byPath.size === 0) {
    return (
      <div className="h-full flex flex-col items-center justify-center gap-3 bg-surface-base text-fg-tertiary">
        <span className="relative flex items-center justify-center">
          <span className="absolute inline-flex h-12 w-12 rounded-full bg-accent/20 animate-ping" />
          <span className="relative inline-flex h-3.5 w-3.5 rounded-full bg-accent" />
        </span>
        <span className="text-sm font-medium text-fg-secondary">
          AI изучает проект…
        </span>
        <span className="text-xs text-fg-tertiary">
          Файлы загорятся здесь, как только агент их откроет
        </span>
      </div>
    );
  }

  const activeName =
    active && activePath ? activePath.split("/").pop() ?? activePath : "";
  const { Icon: ActiveIcon, color: activeColor } = fileIcon(activeName);

  const statusText = (path: string, a: Activity): string => {
    const now = activeNow && path === currentPath;
    if (a.kind === "write") return now ? "пишется сейчас" : "записан агентом";
    return now ? "читается сейчас" : "прочитан";
  };

  const renderNode = (node: TreeNode<Activity>, depth: number): ReactNode => {
    const pad = { paddingLeft: `${depth * 12 + 10}px` };
    if (node.kind === "dir") {
      return (
        <div key={node.path}>
          <div
            style={pad}
            className="flex items-center gap-1.5 py-1 pr-2 text-fg-tertiary"
          >
            <span className="w-3 shrink-0" />
            <FolderOpen className="h-3.5 w-3.5 shrink-0 text-amber-400/70" />
            <span className="font-mono text-xs truncate">{node.name}</span>
          </div>
          {node.children.map((c) => renderNode(c, depth + 1))}
        </div>
      );
    }
    const a = node.data;
    const { Icon, color } = fileIcon(node.name);
    const isCurrent = node.path === currentPath;
    const glow = isCurrent && activeNow;
    const activeSel = node.path === activePath;
    // Activity accent: writing = accent (brand), reading = amber.
    const dot = a.kind === "write" ? "bg-accent" : "bg-amber-400";
    const ring = a.kind === "write" ? "ring-accent/50" : "ring-amber-400/50";
    const tint = a.kind === "write" ? "bg-accent/10" : "bg-amber-400/10";
    return (
      <button
        key={node.path}
        type="button"
        style={pad}
        onClick={() => setPinned(node.path)}
        onMouseEnter={(e) =>
          setTip({
            x: e.clientX,
            y: e.clientY,
            kind: a.kind,
            action: a.action,
            status: statusText(node.path, a),
          })
        }
        onMouseMove={(e) =>
          setTip((t) => (t ? { ...t, x: e.clientX, y: e.clientY } : t))
        }
        onMouseLeave={() => setTip(null)}
        className={cn(
          "relative w-full text-left pr-2 py-1 flex items-center gap-1.5 transition-colors",
          glow && `${tint} ring-1 ${ring}`,
          !glow && activeSel && "bg-surface-overlay",
          !glow && !activeSel && "hover:bg-surface-raised",
        )}
      >
        <span className="w-3 shrink-0 flex items-center justify-center">
          {glow ? (
            <span className="relative flex h-1.5 w-1.5">
              <span
                className={cn(
                  "absolute inline-flex h-full w-full rounded-full opacity-70 animate-ping",
                  dot,
                )}
              />
              <span
                className={cn("relative inline-flex h-1.5 w-1.5 rounded-full", dot)}
              />
            </span>
          ) : (
            <span className={cn("h-1 w-1 rounded-full opacity-50", dot)} />
          )}
        </span>
        <Icon className={cn("h-3.5 w-3.5 shrink-0", color)} />
        <span
          className={cn(
            "font-mono text-xs truncate flex-1 min-w-0",
            glow || activeSel ? "text-fg-primary" : "text-fg-secondary",
          )}
        >
          {node.name}
        </span>
      </button>
    );
  };

  return (
    <div className="h-full flex bg-surface-base">
      {/* Fixed hover tooltip — escapes the sidebar overflow. */}
      {tip && (
        <div
          className="pointer-events-none fixed z-50 max-w-[240px] rounded-lg border border-border-default bg-surface-overlay px-2.5 py-1.5 shadow-lg"
          style={{ left: tip.x + 14, top: tip.y + 12 }}
        >
          <div className="flex items-center gap-1.5">
            <span
              className={cn(
                "h-1.5 w-1.5 rounded-full",
                tip.kind === "write" ? "bg-accent" : "bg-amber-400",
              )}
            />
            <span
              className={cn(
                "font-mono text-[10px] uppercase tracking-wider",
                tip.kind === "write" ? "text-accent" : "text-amber-400",
              )}
            >
              {tip.status}
            </span>
          </div>
          {tip.action && (
            <div className="mt-0.5 text-xs text-fg-secondary">{tip.action}</div>
          )}
        </div>
      )}

      <div className="w-60 shrink-0 border-r border-border-subtle flex flex-col overflow-hidden">
        <div className="h-9 px-3 flex items-center gap-2 border-b border-border-subtle/60">
          <span className="relative flex h-2 w-2">
            <span className="absolute inline-flex h-full w-full rounded-full bg-accent/60 animate-ping" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-accent" />
          </span>
          <span className="text-xs font-mono text-fg-secondary uppercase tracking-wider truncate">
            {activeNow && currentAction ? currentAction : "Агент работает"}
          </span>
          <span className="ml-auto text-[11px] font-mono text-fg-tertiary shrink-0">
            {byPath.size}
          </span>
        </div>
        <div className="flex-1 overflow-y-auto py-1.5 scrollbar-elegant">
          {tree.map((node) => renderNode(node, 0))}
        </div>
        {/* Legend — what the colours mean (живая расшифровка, не серо). */}
        <div className="flex items-center gap-3 border-t border-border-subtle/60 px-3 py-1.5">
          <span className="flex items-center gap-1.5 text-[10px] font-mono text-fg-tertiary">
            <span className="h-1.5 w-1.5 rounded-full bg-amber-400" /> читает
          </span>
          <span className="flex items-center gap-1.5 text-[10px] font-mono text-fg-tertiary">
            <span className="h-1.5 w-1.5 rounded-full bg-accent" /> пишет
          </span>
        </div>
      </div>

      <div className="flex-1 flex flex-col min-w-0">
        <div
          className={cn(
            "h-0.5 w-full bg-gradient-to-r from-transparent via-accent to-transparent transition-opacity duration-300",
            activeNow ? "opacity-100 animate-pulse" : "opacity-0",
          )}
        />
        <div className="h-9 px-3 flex items-center gap-2 shrink-0 border-b border-border-subtle/60">
          {active ? (
            <>
              <ActiveIcon className={cn("h-3.5 w-3.5", activeColor)} />
              <span className="font-mono text-xs text-fg-primary truncate">
                {activePath}
              </span>
              {activeNow && activePath === currentPath && (
                <motion.span
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  className={cn(
                    "text-[10px] font-mono uppercase tracking-wider shrink-0",
                    active.kind === "write" ? "text-accent" : "text-amber-400",
                  )}
                >
                  {active.kind === "write" ? "пишется…" : "читается…"}
                </motion.span>
              )}
              <span className="ml-auto text-[10px] font-mono text-fg-tertiary shrink-0">
                {formatBytes(active.chars)}
              </span>
            </>
          ) : (
            <span className="text-xs text-fg-tertiary">Выберите файл</span>
          )}
        </div>
        <pre
          ref={preRef}
          className="flex-1 overflow-auto m-0 p-3 text-[12px] leading-[1.55] font-mono text-fg-secondary bg-surface-base whitespace-pre-wrap break-words scrollbar-elegant"
        >
          {active?.body}
          {isActiveWriting && (
            <span className="inline-block w-2 h-4 -mb-0.5 ml-0.5 bg-accent/80 animate-pulse align-middle" />
          )}
        </pre>
      </div>
    </div>
  );
}
