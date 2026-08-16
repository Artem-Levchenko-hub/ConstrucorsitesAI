"use client";

import { create } from "zustand";
import type { SelectedElement } from "@/lib/api/types";

/**
 * A picked element in the composer, before the prompt is sent. The transient
 * `id` (assigned by the in-preview inspector) keys the chip and matches the
 * outline inside the iframe, so removing one chip can drop exactly one outline.
 * On send we strip `id` down to the wire `SelectedElement`.
 */
export type PickedElement = SelectedElement & { id: string };

type InspectorState = {
  /** Select-mode active — hover/click picking is live in the preview. */
  inspectMode: boolean;
  /** Picks attached to the next prompt, in pick order. */
  selections: PickedElement[];

  setInspectMode: (on: boolean) => void;
  toggleInspectMode: () => void;
  addSelection: (el: PickedElement) => void;
  setComment: (id: string, comment: string) => void;
  removeSelection: (id: string) => void;
  clear: () => void;
};

export const useInspectorStore = create<InspectorState>((set) => ({
  inspectMode: false,
  selections: [],

  setInspectMode: (on) => set({ inspectMode: on }),
  toggleInspectMode: () => set((s) => ({ inspectMode: !s.inspectMode })),
  addSelection: (el) =>
    set((s) =>
      // Dedupe by selector — re-clicking the same block shouldn't pile up chips.
      s.selections.some((x) => x.selector === el.selector)
        ? s
        : { selections: [...s.selections, el] },
    ),
  setComment: (id, comment) =>
    set((s) => ({
      selections: s.selections.map((x) => (x.id === id ? { ...x, comment } : x)),
    })),
  removeSelection: (id) =>
    set((s) => ({ selections: s.selections.filter((x) => x.id !== id) })),
  clear: () => set({ selections: [] }),
}));
