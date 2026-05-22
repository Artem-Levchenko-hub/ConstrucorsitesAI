"use client";

import { useEffect, useRef } from "react";
import { useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Github, Loader2, Unplug } from "lucide-react";
import { toast } from "sonner";
import {
  disconnectGithub,
  getGithubConnectUrl,
  getGithubStatus,
} from "@/lib/api/github";
import { Button } from "@/components/ui/button";

export function GithubConnectionCard() {
  const qc = useQueryClient();
  const params = useSearchParams();
  const toasted = useRef(false);

  // Surface the OAuth round-trip outcome (?github=connected|denied|error) once.
  useEffect(() => {
    if (toasted.current) return;
    const outcome = params.get("github");
    if (!outcome) return;
    toasted.current = true;
    if (outcome === "connected") toast.success("GitHub подключён");
    else if (outcome === "denied") toast.error("Доступ к GitHub отклонён");
    else if (outcome === "error") toast.error("Не удалось подключить GitHub");
    qc.invalidateQueries({ queryKey: ["github-status"] });
  }, [params, qc]);

  const { data: status, isPending } = useQuery({
    queryKey: ["github-status"],
    queryFn: getGithubStatus,
    retry: false,
  });

  const connectMut = useMutation({
    mutationFn: getGithubConnectUrl,
    onSuccess: (r) => {
      window.location.href = r.authorize_url;
    },
    onError: (err: unknown) => {
      toast.error("Не удалось начать подключение", {
        description: err instanceof Error ? err.message : "ошибка",
      });
    },
  });

  const disconnectMut = useMutation({
    mutationFn: disconnectGithub,
    onSuccess: () => {
      toast.success("GitHub отключён");
      qc.invalidateQueries({ queryKey: ["github-status"] });
    },
    onError: (err: unknown) => {
      toast.error("Не удалось отключить", {
        description: err instanceof Error ? err.message : "ошибка",
      });
    },
  });

  return (
    <div className="rounded-xl border border-border-default bg-surface-base p-6 space-y-4">
      <div className="flex items-start gap-3">
        <Github className="h-5 w-5 mt-0.5 text-fg-primary" />
        <div className="space-y-1">
          <h2 className="text-base font-semibold leading-none">GitHub</h2>
          <p className="text-sm text-fg-secondary">
            Выгружайте проекты в свой GitHub-репозиторий одним нажатием.
          </p>
        </div>
      </div>

      {isPending ? (
        <div className="flex items-center gap-2 text-sm text-fg-tertiary">
          <Loader2 className="h-4 w-4 animate-spin" /> Проверяем подключение…
        </div>
      ) : status?.connected ? (
        <div className="flex items-center justify-between gap-4">
          <p className="text-sm text-fg-secondary">
            Подключён как{" "}
            <span className="font-medium text-fg-primary">
              @{status.github_username}
            </span>
          </p>
          <Button
            variant="secondary"
            size="sm"
            disabled={disconnectMut.isPending}
            onClick={() => disconnectMut.mutate()}
            className="gap-1.5"
          >
            {disconnectMut.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Unplug className="h-4 w-4" />
            )}
            Отключить
          </Button>
        </div>
      ) : (
        <Button
          disabled={connectMut.isPending}
          onClick={() => connectMut.mutate()}
          className="gap-1.5"
        >
          {connectMut.isPending ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Github className="h-4 w-4" />
          )}
          Подключить GitHub
        </Button>
      )}
    </div>
  );
}
