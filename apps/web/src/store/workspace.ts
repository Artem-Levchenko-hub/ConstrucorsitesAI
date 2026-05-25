"use client";

import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

export type WorkspaceViewMode = "preview" | "code";

type WorkspaceState = {
  selectedModelId: string;
  selectedSnapshotId: string | null;
  viewMode: WorkspaceViewMode;

  // ─── Layout — persisted across reloads ─────────────────────────────
  // chatCollapsed / timelineCollapsed swap the side panels for a 44 px
  // vertical rail with a chevron toggle + breathing dot for activity.
  // focusMode hides both side panels AND the TopBar (revealed on hover
  // near the top edge); the prompt input floats centred at the bottom.
  // All three persisted to localStorage so the user's chosen workspace
  // shape survives F5 and tab restores. The Project / Snapshot selection
  // and runtime model deliberately do NOT persist — they're project-scoped.
  chatCollapsed: boolean;
  timelineCollapsed: boolean;
  focusMode: boolean;

  setModel: (id: string) => void;
  selectSnapshot: (id: string | null) => void;
  setViewMode: (mode: WorkspaceViewMode) => void;
  toggleChat: () => void;
  toggleTimeline: () => void;
  toggleFocusMode: () => void;
  exitFocusMode: () => void;
};

export const useWorkspaceStore = create<WorkspaceState>()(
  persist(
    (set) => ({
      // Default — claude-haiku-4-5 (быстрая Anthropic-модель через proxyapi).
      // Совпадает с DEFAULT_MODEL в apps/llm-gateway/deploy/full/.env на проде.
      selectedModelId: "claude-haiku-4-5",
      selectedSnapshotId: null,
      viewMode: "preview",
      chatCollapsed: false,
      timelineCollapsed: false,
      focusMode: false,

      setModel: (id) => set({ selectedModelId: id }),
      selectSnapshot: (id) => set({ selectedSnapshotId: id }),
      setViewMode: (mode) => set({ viewMode: mode }),
      toggleChat: () => set((s) => ({ chatCollapsed: !s.chatCollapsed })),
      toggleTimeline: () =>
        set((s) => ({ timelineCollapsed: !s.timelineCollapsed })),
      toggleFocusMode: () => set((s) => ({ focusMode: !s.focusMode })),
      exitFocusMode: () => set({ focusMode: false }),
    }),
    {
      name: "omnia-workspace-layout",
      storage: createJSONStorage(() => localStorage),
      // Only persist layout — model / snapshot / viewMode reset per-session
      // so a new project doesn't inherit stale selection.
      partialize: (s) => ({
        chatCollapsed: s.chatCollapsed,
        timelineCollapsed: s.timelineCollapsed,
        focusMode: s.focusMode,
      }),
      version: 1,
    },
  ),
);
