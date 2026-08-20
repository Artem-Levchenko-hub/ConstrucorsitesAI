"use client";

import { create } from "zustand";
import type { SelectedElement } from "@/lib/api/types";

export type PickedElement = SelectedElement & { id: string };

type InspectorState = {
  projectScope: string | null;
  editorSession: string | null;
  inspectMode: boolean;
  selections: PickedElement[];
  scopeToProject: (projectId: string, editorSession?: string) => void;
  releaseProjectScope: (projectId: string, editorSession?: string) => void;
  setInspectMode: (on: boolean) => void;
  toggleInspectMode: () => void;
  addSelection: (el: PickedElement) => void;
  setComment: (id: string, comment: string) => void;
  removeSelection: (id: string) => void;
  clear: () => void;
};

export const useInspectorStore = create<InspectorState>((set) => ({
  projectScope: null,
  editorSession: null,
  inspectMode: false,
  selections: [],
  scopeToProject: (projectId, editorSession = "") =>
    set((state) =>
      state.projectScope === projectId &&
      state.editorSession === (editorSession || null)
        ? state
        : {
            projectScope: projectId,
            editorSession: editorSession || null,
            inspectMode: false,
            selections: [],
          },
    ),
  releaseProjectScope: (projectId, editorSession = "") =>
    set((state) =>
      state.projectScope === projectId &&
      (!editorSession || state.editorSession === editorSession)
        ? {
            projectScope: null,
            editorSession: null,
            inspectMode: false,
            selections: [],
          }
        : state,
    ),
  setInspectMode: (on) =>
    set((state) => (state.inspectMode === on ? state : { inspectMode: on })),
  toggleInspectMode: () => set((state) => ({ inspectMode: !state.inspectMode })),
  addSelection: (el) =>
    set((state) =>
      state.selections.some((item) => item.selector === el.selector)
        ? state
        : { selections: [...state.selections, el] },
    ),
  setComment: (id, comment) =>
    set((state) => ({
      selections: state.selections.map((item) =>
        item.id === id ? { ...item, comment } : item,
      ),
    })),
  removeSelection: (id) =>
    set((state) => ({
      selections: state.selections.filter((item) => item.id !== id),
    })),
  clear: () => set({ selections: [] }),
}));
