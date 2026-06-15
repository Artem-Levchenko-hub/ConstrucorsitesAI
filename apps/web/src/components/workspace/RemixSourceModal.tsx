"use client";

/**
 * V4 #3 — TRANSITIVE REMIX LINEAGE. The workspace remix badge is now a button:
 * click it to see WHICH project this one is a remix of (attribution + a link to
 * the source's public page) and to fork THIS version one step further — turning
 * the one-level `forked_from` record into a visible, walkable A→B→C→D chain
 * ("команда растёт сама", pillar 4).
 *
 * "Тоже ремикснуть" forks the CURRENT project (extends the chain), reusing the
 * same POST /fork seam the zero-signup public CTA uses; for the signed-in owner
 * the fork binds to them, then the workspace navigates into the new copy.
 */

import { GitFork, ExternalLink, Loader2, Sparkles } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { forkProject } from "@/lib/api/projects";
import { type RemixSource, REMIX_BADGE_LABEL } from "@/lib/project-lineage";
import { buildPublicUrl } from "@/lib/public-url";

export function RemixSourceModal({
  projectId,
  source,
}: {
  /** The CURRENT project — "Тоже ремикснуть" forks this one (chain step). */
  projectId: string;
  /** Resolved source attribution (name + optional public slug). */
  source: RemixSource;
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [forking, setForking] = useState(false);

  const sourceUrl = source.slug ? buildPublicUrl(source.slug) : null;

  async function remixThis() {
    if (forking) return;
    setForking(true);
    try {
      const fork = await forkProject(projectId);
      toast.success("Создан новый ремикс");
      // Hard navigation so the fresh project (and any anon session cookie the
      // fork set) loads cleanly in the workspace.
      router.push(`/projects/${fork.id}`);
    } catch {
      setForking(false);
      toast.error("Не удалось создать ремикс", {
        description: "Попробуйте ещё раз.",
      });
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <button
          type="button"
          title="Откуда этот ремикс — и сделать ещё один"
          className="shrink-0 cursor-pointer appearance-none rounded-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60"
        >
          <Badge
            variant="accent"
            className="gap-1 transition hover:brightness-110"
          >
            <GitFork className="h-3 w-3" />
            {REMIX_BADGE_LABEL}
          </Badge>
        </button>
      </DialogTrigger>

      <DialogContent className="sm:max-w-[460px]">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <GitFork className="h-4 w-4 text-accent" />
            Ремикс проекта
          </DialogTitle>
          <DialogDescription>
            Этот проект — ремикс{" "}
            <span className="font-medium text-fg-primary">{source.name}</span>.
            Откройте оригинал или сделайте ещё один ремикс — цепочка продолжится.
          </DialogDescription>
        </DialogHeader>

        {sourceUrl && (
          <div className="py-1">
            <Link
              href={sourceUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-2 rounded-md px-2.5 py-2 text-sm text-accent transition-colors hover:bg-surface-overlay"
            >
              <ExternalLink className="h-4 w-4" />
              Открыть оригинал — {source.name}
            </Link>
          </div>
        )}

        <DialogFooter>
          <Button variant="secondary" onClick={() => setOpen(false)}>
            Закрыть
          </Button>
          <Button onClick={remixThis} disabled={forking} className="gap-2">
            {forking ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Sparkles className="h-4 w-4" />
            )}
            {forking ? "Создаём…" : "Тоже ремикснуть"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
