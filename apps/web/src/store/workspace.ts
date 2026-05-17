"use client";

import { create } from "zustand";

export type WorkspaceViewMode = "preview" | "code";

type WorkspaceState = {
  selectedModelId: string;
  selectedSnapshotId: string | null;
  viewMode: WorkspaceViewMode;
  chatCollapsed: boolean;
  timelineCollapsed: boolean;

  setModel: (id: string) => void;
  selectSnapshot: (id: string | null) => void;
  setViewMode: (mode: WorkspaceViewMode) => void;
  toggleChat: () => void;
  toggleTimeline: () => void;
};

export const useWorkspaceStore = create<WorkspaceState>((set) => ({
  // Default — claude-haiku-4-5 (быстрая Anthropic-модель через proxyapi).
  // Совпадает с DEFAULT_MODEL в apps/llm-gateway/deploy/full/.env на проде.
  selectedModelId: "claude-haiku-4-5",
  selectedSnapshotId: null,
  viewMode: "preview",
  chatCollapsed: false,
  timelineCollapsed: false,

  setModel: (id) => set({ selectedModelId: id }),
  selectSnapshot: (id) => set({ selectedSnapshotId: id }),
  setViewMode: (mode) => set({ viewMode: mode }),
  toggleChat: () => set((s) => ({ chatCollapsed: !s.chatCollapsed })),
  toggleTimeline: () =>
    set((s) => ({ timelineCollapsed: !s.timelineCollapsed })),
}));
