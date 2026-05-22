"use client";

/**
 * TopBar control for "Export to GitHub".
 *
 * Context-aware, mirroring RuntimeButton's compact style:
 *   - status unknown        → spinner
 *   - GitHub not connected   → link to /account to connect
 *   - connected              → push the project to GitHub (toast + open link)
 *
 * The export endpoint is synchronous, so the mutation result drives the toast;
 * no WS wiring needed here.
 */

import { useMutation, useQuery } from "@tanstack/react-query";
import { Github, Loader2 } from "lucide-react";
import Link from "next/link";
import { toast } from "sonner";
import { exportToGithub, getGithubStatus } from "@/lib/api/github";
import { Button } from "@/components/ui/button";

export function GithubExportButton({ projectId }: { projectId: string }) {
  const { data: status, isPending } = useQuery({
    queryKey: ["github-status"],
    queryFn: getGithubStatus,
    retry: false,
    staleTime: 60_000,
  });

  const exportMut = useMutation({
    mutationFn: () => exportToGithub(projectId),
    onSuccess: (r) => {
      toast.success("Выгружено на GitHub", {
        description: r.repo_full_name,
        action: {
          label: "Открыть",
          onClick: () =>
            window.open(r.repo_url, "_blank", "noopener,noreferrer"),
        },
      });
    },
    onError: (err: unknown) => {
      toast.error("Не удалось выгрузить на GitHub", {
        description: err instanceof Error ? err.message : "ошибка",
      });
    },
  });

  if (isPending) {
    return (
      <Button size="sm" variant="ghost" disabled className="h-7 px-2 text-xs">
        <Loader2 className="h-3 w-3 animate-spin" />
      </Button>
    );
  }

  if (!status?.connected) {
    return (
      <Button
        size="sm"
        variant="ghost"
        asChild
        className="gap-1.5 h-7 px-2.5 text-xs"
        title="Подключить GitHub в настройках аккаунта"
      >
        <Link href="/account">
          <Github className="h-3 w-3" />
          Подключить GitHub
        </Link>
      </Button>
    );
  }

  return (
    <Button
      size="sm"
      variant="ghost"
      disabled={exportMut.isPending}
      onClick={() => exportMut.mutate()}
      className="gap-1.5 h-7 px-2.5 text-xs"
      title={`Выгрузить проект на GitHub (@${status.github_username})`}
    >
      {exportMut.isPending ? (
        <Loader2 className="h-3 w-3 animate-spin" />
      ) : (
        <Github className="h-3 w-3" />
      )}
      На GitHub
    </Button>
  );
}
