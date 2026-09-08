"use client";

import { useState } from "react";
import { useSearchParams } from "next/navigation";
import { MaxProjectSetupDialog } from "./MaxProjectSetupDialog";
import type { SetupSection } from "./MaxProjectSetupSections";

/** URL-backed dialog: old links, Back and direct entry share the same editor. */
export function MaxEditorDataDialog({ projectId }: { projectId: string }) {
  const params = useSearchParams();
  const value = params?.get("data");
  const section: SetupSection | null = value === "details" || value === "content" || value === "owner" || value === "policies" ? value : null;
  // Close immediately, including before handing off to the AI confirmation.
  // Back/Forward and external links still own the selected entry.
  const [entry, setEntry] = useState({ source: section, selected: section, projectId });
  if (entry.source !== section || entry.projectId !== projectId) setEntry({ source: section, selected: section, projectId });
  return <MaxProjectSetupDialog projectId={projectId} display="header" open={entry.selected !== null} initialSection={entry.selected ?? "details"}
    onOpenChange={open => {
      setEntry({ source: section, selected: open ? "details" : null, projectId });
      const next = new URLSearchParams(params?.toString());
      if (open) next.set("data", "details"); else next.delete("data");
      const url = `/max/${projectId}${next.size ? `?${next}` : ""}`;
      // Native history updates keep the chat mounted and require no RSC fetch.
      if (open) window.history.pushState({ maxDataDialog: projectId }, "", url);
      else if (window.history.state?.maxDataDialog === projectId) window.history.back();
      else window.history.replaceState(null, "", url);
    }} />;
}
