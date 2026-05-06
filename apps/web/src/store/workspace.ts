"use client";

import { create } from "zustand";

type WorkspaceState = {
  selectedModelId: string;
  selectedSnapshotId: string | null;
  chatCollapsed: boolean;
  timelineCollapsed: boolean;

  setModel: (id: string) => void;
  selectSnapshot: (id: string | null) => void;
  toggleChat: () => void;
  toggleTimeline: () => void;
};

export const useWorkspaceStore = create<WorkspaceState>((set) => ({
  // Default to gigachat-2 because that's the only provider with a configured
  // API key on the demo server. Users can switch in the ModelSelector dropdown.
  selectedModelId: "gigachat-2",
  selectedSnapshotId: null,
  chatCollapsed: false,
  timelineCollapsed: false,

  setModel: (id) => set({ selectedModelId: id }),
  selectSnapshot: (id) => set({ selectedSnapshotId: id }),
  toggleChat: () => set((s) => ({ chatCollapsed: !s.chatCollapsed })),
  toggleTimeline: () =>
    set((s) => ({ timelineCollapsed: !s.timelineCollapsed })),
}));
