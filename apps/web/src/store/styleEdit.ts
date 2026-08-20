"use client";

import { create } from "zustand";

export type StyleSelected = {
  selector: string;
  tag: string;
  color: string;
  backgroundColor: string;
  borderColor: string;
  fontFamily: string;
  src?: string;
  srcs?: string[];
  editableText?: boolean;
  editText?: string;
  textIndex?: number;
  outerHTML?: string;
  htmlIndex?: number;
  prevHTML?: string;
  prevIndex?: number;
  nextHTML?: string;
  nextIndex?: number;
  rect?: { x: number; y: number; width: number; height: number };
};

export type ElementEdit = {
  color?: string;
  background_color?: string;
  border_color?: string;
  font_family?: string;
};

type StyleEditState = {
  projectScope: string | null;
  editorSession: string | null;
  styleMode: boolean;
  selected: StyleSelected | null;
  tokens: Record<string, string>;
  elements: Record<string, ElementEdit>;
  dirty: boolean;
  scopeToProject: (projectId: string, editorSession?: string) => void;
  releaseProjectScope: (projectId: string, editorSession?: string) => void;
  setStyleMode: (on: boolean) => void;
  stopStylePicking: () => void;
  selectElement: (el: StyleSelected) => void;
  setElementProp: (
    selector: string,
    key: keyof ElementEdit,
    value: string | null,
  ) => void;
  setToken: (varName: string, value: string | null) => void;
  clearAll: () => void;
  markSaved: () => void;
};

export const useStyleEditStore = create<StyleEditState>((set) => ({
  projectScope: null,
  editorSession: null,
  styleMode: false,
  selected: null,
  tokens: {},
  elements: {},
  dirty: false,
  scopeToProject: (projectId, editorSession = "") =>
    set((state) =>
      state.projectScope === projectId &&
      state.editorSession === (editorSession || null)
        ? state
        : {
            projectScope: projectId,
            editorSession: editorSession || null,
            styleMode: false,
            selected: null,
            tokens: {},
            elements: {},
            dirty: false,
          },
    ),
  releaseProjectScope: (projectId, editorSession = "") =>
    set((state) =>
      state.projectScope === projectId &&
      (!editorSession || state.editorSession === editorSession)
        ? {
            projectScope: null,
            editorSession: null,
            styleMode: false,
            selected: null,
            tokens: {},
            elements: {},
            dirty: false,
          }
        : state,
    ),
  setStyleMode: (on) =>
    set((state) => {
      if (on) return state.styleMode ? state : { styleMode: true };
      return !state.styleMode && state.selected === null
        ? state
        : { styleMode: false, selected: null };
    }),
  stopStylePicking: () =>
    set((state) => (state.styleMode ? { styleMode: false } : state)),
  selectElement: (el) => set({ selected: el }),
  setElementProp: (selector, key, value) =>
    set((state) => {
      const next = { ...(state.elements[selector] ?? {}) };
      if (value == null || value === "") delete next[key];
      else next[key] = value;
      const elements = { ...state.elements };
      if (Object.keys(next).length) elements[selector] = next;
      else delete elements[selector];
      return { elements, dirty: true };
    }),
  setToken: (varName, value) =>
    set((state) => {
      const tokens = { ...state.tokens };
      if (value == null || value === "") delete tokens[varName];
      else tokens[varName] = value;
      return { tokens, dirty: true };
    }),
  clearAll: () =>
    set({ selected: null, tokens: {}, elements: {}, dirty: false }),
  markSaved: () => set({ tokens: {}, elements: {}, dirty: false }),
}));
