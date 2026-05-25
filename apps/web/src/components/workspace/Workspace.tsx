"use client";

import { useEffect } from "react";
import { motion } from "framer-motion";
import type { Project } from "@/lib/api/types";
import { useWorkspaceStore } from "@/store/workspace";
import { ChatPanel } from "./ChatPanel";
import { CollapsedRail } from "./CollapsedRail";
import { PreviewFrame } from "./PreviewFrame";
import { Timeline } from "./Timeline";

export function Workspace({ project }: { project: Project }) {
  const chatCollapsed = useWorkspaceStore((s) => s.chatCollapsed);
  const timelineCollapsed = useWorkspaceStore((s) => s.timelineCollapsed);
  const focusMode = useWorkspaceStore((s) => s.focusMode);
  const toggleChat = useWorkspaceStore((s) => s.toggleChat);
  const toggleTimeline = useWorkspaceStore((s) => s.toggleTimeline);
  const toggleFocusMode = useWorkspaceStore((s) => s.toggleFocusMode);
  const exitFocusMode = useWorkspaceStore((s) => s.exitFocusMode);

  // Global hotkeys. Ignored when focus is inside a text input so the
  // user can still type `[` / `]` in prompts / code. ESC always works
  // (consistent with palette / dialog behaviour).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      const inEditable =
        target !== null &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.isContentEditable);

      // ESC — always exits focus mode (even from a prompt input).
      if (e.key === "Escape" && focusMode) {
        e.preventDefault();
        exitFocusMode();
        return;
      }

      if (inEditable) return;

      // Cmd/Ctrl + \ — toggle focus mode
      if ((e.metaKey || e.ctrlKey) && e.key === "\\") {
        e.preventDefault();
        toggleFocusMode();
        return;
      }

      // Bare [ / ] — toggle side panels (suppressed while focused)
      if (!focusMode && e.key === "[" && !e.metaKey && !e.ctrlKey) {
        e.preventDefault();
        toggleChat();
        return;
      }
      if (!focusMode && e.key === "]" && !e.metaKey && !e.ctrlKey) {
        e.preventDefault();
        toggleTimeline();
        return;
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [focusMode, toggleChat, toggleTimeline, toggleFocusMode, exitFocusMode]);

  // ─── Focus mode — full-screen preview only ───────────────────────
  // Side panels gone, TopBar still visible (hiding it requires touching
  // the page-level layout; deferred to a follow-up). Preview gets the
  // whole canvas plus extra glow ring + hint pill.
  if (focusMode) {
    return (
      <motion.div
        layout
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        className="relative flex-1 min-h-0 p-6"
      >
        <PreviewFrame project={project} />
        <div className="pointer-events-none fixed bottom-4 left-1/2 -translate-x-1/2 z-50 px-3 py-1.5 rounded-full bg-surface-overlay/80 backdrop-blur border border-border-default text-[11px] text-fg-tertiary font-mono">
          <kbd className="font-mono text-[10px] mr-1.5 px-1 py-0.5 rounded bg-surface-raised border border-border-subtle text-fg-secondary">
            ESC
          </kbd>
          или
          <kbd className="font-mono text-[10px] mx-1.5 px-1 py-0.5 rounded bg-surface-raised border border-border-subtle text-fg-secondary">
            ⌘\
          </kbd>
          — выйти из focus mode
        </div>
      </motion.div>
    );
  }

  // ─── Normal layout — three columns with collapsible side panels ──
  // Grid columns set via inline style so framer-motion's `layout`
  // animation smoothly interpolates width when panels collapse / expand.
  const chatCol = chatCollapsed ? "44px" : "320px";
  const timelineCol = timelineCollapsed ? "44px" : "220px";

  return (
    <motion.div
      layout
      className="relative flex-1 grid min-h-0"
      style={{
        gridTemplateColumns: `${chatCol} minmax(0, 1fr) ${timelineCol}`,
      }}
      transition={{ duration: 0.28, ease: [0.16, 1, 0.3, 1] }}
    >
      {/* Ambient orbs — sit behind every panel via z-index: -1, glow
          through the semi-transparent panel backgrounds. Unchanged from
          the earlier workspace-ui-glow pass. */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute -top-40 -left-40 h-[32rem] w-[32rem] rounded-full"
        style={{
          background:
            "radial-gradient(circle, rgb(124 92 255 / 0.55) 0%, rgb(124 92 255 / 0.18) 40%, transparent 70%)",
          zIndex: -1,
          filter: "blur(50px)",
        }}
      />
      <div
        aria-hidden="true"
        className="pointer-events-none absolute -bottom-40 -right-40 h-[32rem] w-[32rem] rounded-full"
        style={{
          background:
            "radial-gradient(circle, rgb(92 184 255 / 0.50) 0%, rgb(92 184 255 / 0.15) 40%, transparent 70%)",
          zIndex: -1,
          filter: "blur(50px)",
        }}
      />
      <div
        aria-hidden="true"
        className="pointer-events-none absolute top-1/3 left-1/2 -translate-x-1/2 h-[24rem] w-[24rem] rounded-full"
        style={{
          background:
            "radial-gradient(circle, rgb(236 92 255 / 0.25) 0%, transparent 70%)",
          zIndex: -1,
          filter: "blur(60px)",
        }}
      />

      <div className="relative border-r border-border-subtle min-h-0">
        {chatCollapsed ? (
          <CollapsedRail
            label="Чат"
            side="left"
            hotkey="["
            onExpand={toggleChat}
            accentColor="violet"
          />
        ) : (
          <ChatPanel projectId={project.id} projectSlug={project.slug} />
        )}
      </div>

      <div className="relative min-h-0">
        <PreviewFrame project={project} />
      </div>

      <div className="relative border-l border-border-subtle min-h-0">
        {timelineCollapsed ? (
          <CollapsedRail
            label="История"
            side="right"
            hotkey="]"
            onExpand={toggleTimeline}
            accentColor="cyan"
          />
        ) : (
          <Timeline project={project} />
        )}
      </div>
    </motion.div>
  );
}
