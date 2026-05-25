"use client";

/**
 * Кнопка «На GitHub» в TopBar. Рендерится на каждом проекте — гейт состояния
 * происходит на уровне юзера (User.github_token_enc на бэке), не проекта.
 *
 * 3 состояния:
 *   - isPending      → disabled spinner
 *   - !connected     → "Подключить GitHub" — линк на /account
 *   - connected      → "На GitHub" — открывает GithubPushDialog
 */

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { Github, Loader2 } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { getGithubStatus } from "@/lib/api/github";

import { GithubPushDialog } from "./GithubPushDialog";

export function GithubPushButton({
  projectId,
  projectSlug,
}: {
  projectId: string;
  projectSlug: string;
}) {
  const [open, setOpen] = useState(false);

  const { data, isPending } = useQuery({
    queryKey: ["github-status"],
    queryFn: getGithubStatus,
    retry: false,
    staleTime: 60_000,
  });

  // Icon-only button — text dropped per "интуитивно без лишних надписей".
  // Tooltip preserves discoverability (hover/keyboard-focus). Connection
  // state is now communicated by a tiny coloured dot in the corner of the
  // icon (green = connected, dim = not).
  const baseBtn =
    "relative inline-flex h-9 w-9 items-center justify-center rounded-xl border bg-surface-raised/60 backdrop-blur-md transition-all hover:bg-surface-raised";

  if (isPending) {
    return (
      <button
        type="button"
        disabled
        className={`${baseBtn} border-border-subtle text-fg-tertiary`}
        aria-label="GitHub status loading"
      >
        <Loader2 className="h-4 w-4 animate-spin" />
      </button>
    );
  }

  if (!data?.connected) {
    return (
      <Link
        href="/account"
        title="Подключить GitHub — потом сможешь заливать проекты в свои репозитории"
        aria-label="Подключить GitHub"
        className={`${baseBtn} border-border-default text-fg-secondary hover:text-fg-primary hover:border-border-strong group`}
      >
        <Github className="h-4 w-4" />
        {/* Disconnected — dim dot in the corner so the empty state isn't
            silent. Pulses very softly so the eye finds it without distraction. */}
        <span
          aria-hidden="true"
          className="absolute -top-0.5 -right-0.5 h-2 w-2 rounded-full bg-fg-muted ring-2 ring-surface-base"
        />
      </Link>
    );
  }

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        title={`На GitHub → github.com/${data.login}`}
        aria-label="Залить проект на GitHub"
        className={`${baseBtn} border-success/40 text-fg-primary hover:border-success/60 shadow-glow-success`}
      >
        <Github className="h-4 w-4" />
        {/* Connected — bright green dot. Pure visual signal, no extra label. */}
        <span
          aria-hidden="true"
          className="absolute -top-0.5 -right-0.5 h-2 w-2 rounded-full bg-success ring-2 ring-surface-base"
        />
      </button>
      <GithubPushDialog
        open={open}
        onOpenChange={setOpen}
        projectId={projectId}
        projectSlug={projectSlug}
        githubLogin={data.login}
      />
    </>
  );
}
