"use client";

import { create } from "zustand";

export type WorkspaceViewMode = "preview" | "code";

type WorkspaceState = {
  selectedSnapshotId: string | null;
  viewMode: WorkspaceViewMode;
  chatCollapsed: boolean;
  timelineCollapsed: boolean;

  selectSnapshot: (id: string | null) => void;
  setViewMode: (mode: WorkspaceViewMode) => void;
  toggleChat: () => void;
  toggleTimeline: () => void;
};

export const useWorkspaceStore = create<WorkspaceState>((set) => ({
  selectedSnapshotId: null,
  viewMode: "preview",
  chatCollapsed: false,
  timelineCollapsed: false,

  selectSnapshot: (id) => set({ selectedSnapshotId: id }),
  setViewMode: (mode) => set({ viewMode: mode }),
  toggleChat: () => set((s) => ({ chatCollapsed: !s.chatCollapsed })),
  toggleTimeline: () =>
    set((s) => ({ timelineCollapsed: !s.timelineCollapsed })),
}));
