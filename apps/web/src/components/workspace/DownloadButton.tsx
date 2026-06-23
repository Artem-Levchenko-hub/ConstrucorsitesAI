"use client";

import { useState } from "react";
import { Download, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { downloadProjectFiles } from "@/lib/api/projects";

/**
 * 📥 «Скачать» — one obvious button (owner 2026-06-19): click → the project's real
 * files (code/site, straight from git) land as a `<slug>.zip`. No thinking, no
 * model-written download links, no setup. Lives in the TopBar next to Publish.
 */
export function DownloadButton({
  projectId,
  projectSlug,
}: {
  projectId: string;
  projectSlug?: string;
}) {
  const [busy, setBusy] = useState(false);

  const onClick = async () => {
    if (busy) return;
    setBusy(true);
    try {
      await downloadProjectFiles(projectId, projectSlug);
    } catch (e) {
      toast.error("Не удалось скачать", {
        description: e instanceof Error ? e.message : undefined,
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Button
      variant="ghost"
      size="sm"
      className="gap-1.5"
      onClick={onClick}
      disabled={busy}
      title="Скачать весь код проекта одним архивом (.zip)"
    >
      {busy ? (
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
      ) : (
        <Download className="h-3.5 w-3.5" />
      )}
      <span className="hidden 2xl:inline">Скачать</span>
    </Button>
  );
}
