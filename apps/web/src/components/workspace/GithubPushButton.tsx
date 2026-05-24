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

  if (isPending) {
    return (
      <Button
        size="sm"
        variant="secondary"
        disabled
        className="gap-1.5 h-7 px-2.5 text-xs"
      >
        <Loader2 className="h-3 w-3 animate-spin" />
        GitHub
      </Button>
    );
  }

  if (!data?.connected) {
    return (
      <Button
        asChild
        size="sm"
        variant="secondary"
        className="gap-1.5 h-7 px-2.5 text-xs"
        title="Подключи GitHub, чтобы заливать проекты в свои репозитории"
      >
        <Link href="/account">
          <Github className="h-3 w-3" />
          Подключить GitHub
        </Link>
      </Button>
    );
  }

  return (
    <>
      <Button
        size="sm"
        variant="secondary"
        onClick={() => setOpen(true)}
        className="gap-1.5 h-7 px-2.5 text-xs"
        title={`Залить проект в репозиторий на github.com/${data.login}`}
      >
        <Github className="h-3 w-3" />
        На GitHub
      </Button>
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
