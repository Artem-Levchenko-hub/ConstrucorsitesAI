"use client";

import { create } from "zustand";

/** The element currently being styled, with its CURRENT computed values (so the
 *  panel can show what it's editing). */
export type StyleSelected = {
  selector: string;
  tag: string;
  color: string;
  backgroundColor: string;
  borderColor: string;
  fontFamily: string;
  /** Current <img> source (empty for non-images) — drives "replace image". */
  src?: string;
  /** All image sources at the click point (carousel/slider stacks several) so
   *  the panel can offer a chooser instead of only the topmost. */
  srcs?: string[];
  /** Direct text editing: set when the element is pure text (no child elements).
   *  `editText` is the current text; `textIndex` disambiguates repeated labels. */
  editableText?: boolean;
  editText?: string;
  textIndex?: number;
  /** Exact source HTML of the element + occurrence index — for HARD delete
   *  (surgical cut from index.html). Empty when too big to match safely. */
  outerHTML?: string;
  htmlIndex?: number;
  /** Prev/next sibling source HTML + index — for move up/down (swap). Empty
   *  string when there's no sibling in that direction. */
  prevHTML?: string;
  prevIndex?: number;
  nextHTML?: string;
  nextIndex?: number;
  rect?: { x: number; y: number; width: number; height: number };
};

/** Pending per-element edit (payload keys; underscores match the API). */
export type ElementEdit = {
  color?: string;
  background_color?: string;
  border_color?: string;
  font_family?: string;
};

type StyleEditState = {
  /** Style-mode active — clicks select an element to recolor / restyle. */
  styleMode: boolean;
  /** The element under edit (null = panel closed). */
  selected: StyleSelected | null;
  /** Pending site-wide token edits: css-var → hex. */
  tokens: Record<string, string>;
  /** Pending per-element edits: selector → edit. */
  elements: Record<string, ElementEdit>;
  /** Unsaved changes exist. */
  dirty: boolean;

  setStyleMode: (on: boolean) => void;
  selectElement: (el: StyleSelected) => void;
  setElementProp: (
    selector: string,
    key: keyof ElementEdit,
    value: string | null,
  ) => void;
  setToken: (varName: string, value: string | null) => void;
  /** Full reset (mode off / cancel) — also closes the panel. */
  clearAll: () => void;
  /** After a successful save: drop pending edits + dirty, keep mode + selection.
   *  Backend merges, so the next edit starts empty and still accumulates. */
  markSaved: () => void;
};

export const useStyleEditStore = create<StyleEditState>((set) => ({
  styleMode: false,
  selected: null,
  tokens: {},
  elements: {},
  dirty: false,

  setStyleMode: (on) =>
    set(on ? { styleMode: true } : { styleMode: false, selected: null }),
  selectElement: (el) => set({ selected: el }),
  setElementProp: (selector, key, value) =>
    set((s) => {
      const next = { ...(s.elements[selector] ?? {}) };
      if (value == null || value === "") delete next[key];
      else next[key] = value;
      const elements = { ...s.elements };
      if (Object.keys(next).length) elements[selector] = next;
      else delete elements[selector];
      return { elements, dirty: true };
    }),
  setToken: (varName, value) =>
    set((s) => {
      const tokens = { ...s.tokens };
      if (value == null || value === "") delete tokens[varName];
      else tokens[varName] = value;
      return { tokens, dirty: true };
    }),
  clearAll: () =>
    set({ selected: null, tokens: {}, elements: {}, dirty: false }),
  markSaved: () => set({ tokens: {}, elements: {}, dirty: false }),
}));
