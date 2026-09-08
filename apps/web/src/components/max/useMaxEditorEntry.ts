"use client";

import { useState } from "react";
import { useSearchParams } from "next/navigation";
import { readMaxEditorEntry, type MaxEditorEntry } from "@/lib/max-editor-entry";

/** Modal switches use native history so the running chat and preview never unmount. */
export function useMaxEditorEntry(projectId: string) {
  const params = useSearchParams();
  const source = readMaxEditorEntry(new URLSearchParams(params?.toString()));
  const [entry, setEntry] = useState({ source, selected: source, projectId });
  if (entry.source !== source || entry.projectId !== projectId) setEntry({ source, selected: source, projectId });
  function select(selected: MaxEditorEntry | null) {
    setEntry({ source, selected, projectId });
    const next = new URLSearchParams(params?.toString());
    next.delete("data"); next.delete("panel");
    const isTransient = selected === "navigation" || selected === "tools" || selected === "preview";
    if (selected?.startsWith("data:")) next.set("data", selected.slice(5));
    else if (selected && !isTransient) next.set("panel", selected);
    const url = `/max/${projectId}${next.size ? `?${next}` : ""}`;
    const owned = window.history.state?.maxEditorModal === projectId;
    if (!selected) {
      if (owned) window.history.back();
      else if (source) window.history.replaceState(null, "", url);
    } else if (!isTransient) {
      // Next copies its router internals. Passing __NA/_N back would bypass its search-param update.
      const state = !source || owned ? { maxEditorModal: projectId } : null;
      if (source || owned) window.history.replaceState(state, "", url);
      else window.history.pushState(state, "", url);
    }
  }
  return { entry: entry.selected, select };
}
