"use client";

/**
 * Модалка «Залить проект в GitHub». Поля:
 *   - repo_name (default = project.slug — уже ASCII-safe, regex backend'а проходит)
 *   - private   (default = true — приватный безопаснее для AI-сгенерённого кода)
 *   - description
 *
 * Маппинг ошибок:
 *   - github_not_connected → редирект на /account (значит токен протух)
 *   - github_unavailable   → repo с таким именем уже есть → подсказка переименовать
 *   - project_empty        → в проекте нет current_snapshot — нечего пушить
 */

import { useMutation } from "@tanstack/react-query";
import { ExternalLink, Github, Loader2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { ApiError } from "@/lib/api/client";
import { pushProjectToGithub } from "@/lib/api/github";
import type { GithubPushResponse } from "@/lib/api/types";

const REPO_NAME_RE = /^[A-Za-z0-9._-]+$/;

export function GithubPushDialog({
  open,
  onOpenChange,
  projectId,
  projectSlug,
  githubLogin,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  projectId: string;
  projectSlug: string;
  githubLogin: string | null;
}) {
  const router = useRouter();
  const [repoName, setRepoName] = useState(projectSlug);
  const [isPrivate, setPrivate] = useState(true);
  const [description, setDescription] = useState("");
  const [result, setResult] = useState<GithubPushResponse | null>(null);

  // Сбрасываем форму при каждом открытии — иначе при повторном open покажется
  // успех предыдущего пуша.
  useEffect(() => {
    if (open) {
      setRepoName(projectSlug);
      setPrivate(true);
      setDescription("");
      setResult(null);
    }
  }, [open, projectSlug]);

  const repoNameValid = REPO_NAME_RE.test(repoName) && repoName.length <= 100;

  const pushMut = useMutation({
    mutationFn: () =>
      pushProjectToGithub(projectId, {
        repo_name: repoName,
        private: isPrivate,
        description: description || undefined,
      }),
    onSuccess: (data) => {
      setResult(data);
      toast.success("Проект залит в GitHub", { description: data.full_name });
    },
    onError: (err: unknown) => {
      if (err instanceof ApiError) {
        if (err.code === "github_not_connected") {
          toast.error("GitHub отключён — подключи заново");
          onOpenChange(false);
          router.push("/account");
          return;
        }
        if (err.code === "github_unavailable") {
          toast.error("Не удалось создать репозиторий", {
            description: "Возможно, репо с таким именем уже есть — переименуй.",
          });
          return;
        }
        if (err.code === "project_empty") {
          toast.error("В проекте ещё нет файлов", {
            description: "Сгенерируй сайт хотя бы одним промптом и попробуй снова.",
          });
          return;
        }
        toast.error("Не удалось залить в GitHub", { description: err.message });
        return;
      }
      const msg = err instanceof Error ? err.message : "сетевая ошибка";
      toast.error("Не удалось залить в GitHub", { description: msg });
    },
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[480px]">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Github className="h-4 w-4" />
            Залить проект в GitHub
          </DialogTitle>
          <DialogDescription>
            Создадим новый репозиторий
            {githubLogin && (
              <>
                {" "}у{" "}
                <span className="font-mono text-fg-secondary">
                  @{githubLogin}
                </span>
              </>
            )}{" "}
            и зальём текущий снапшот проекта.
          </DialogDescription>
        </DialogHeader>

        {result ? (
          <div className="space-y-3 py-2">
            <div className="text-sm text-fg-primary">
              Готово — репозиторий{" "}
              <span className="font-mono">{result.full_name}</span> создан.
            </div>
            <Button asChild className="w-full gap-2">
              <Link
                href={result.repo_url}
                target="_blank"
                rel="noopener noreferrer"
              >
                <ExternalLink className="h-4 w-4" />
                Открыть на GitHub
              </Link>
            </Button>
          </div>
        ) : (
          <div className="space-y-4 py-2">
            <div className="space-y-1.5">
              <Label htmlFor="repo-name">Имя репозитория</Label>
              <Input
                id="repo-name"
                value={repoName}
                onChange={(e) => setRepoName(e.target.value)}
                placeholder="my-awesome-site"
                maxLength={100}
                spellCheck={false}
                autoComplete="off"
              />
              <p className="text-[11px] text-fg-tertiary">
                Латиница, цифры, точка, тире, нижнее подчёркивание. До 100
                символов.
              </p>
              {!repoNameValid && repoName.length > 0 && (
                <p className="text-[11px] text-danger">
                  Недопустимые символы — разрешены только {`a-z A-Z 0-9 . _ -`}.
                </p>
              )}
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="repo-description">Описание (необязательно)</Label>
              <Textarea
                id="repo-description"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="Сайт, созданный в Omnia.AI"
                maxLength={350}
                rows={2}
              />
            </div>

            <label className="flex items-center gap-2 text-sm cursor-pointer select-none">
              <input
                type="checkbox"
                checked={isPrivate}
                onChange={(e) => setPrivate(e.target.checked)}
                className="h-4 w-4 rounded border-border-default accent-accent"
              />
              <span>Приватный репозиторий</span>
            </label>
          </div>
        )}

        <DialogFooter>
          {result ? (
            <Button variant="secondary" onClick={() => onOpenChange(false)}>
              Закрыть
            </Button>
          ) : (
            <>
              <Button
                variant="secondary"
                onClick={() => onOpenChange(false)}
                disabled={pushMut.isPending}
              >
                Отмена
              </Button>
              <Button
                onClick={() => pushMut.mutate()}
                disabled={!repoNameValid || pushMut.isPending}
                className="gap-2"
              >
                {pushMut.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Github className="h-4 w-4" />
                )}
                Залить
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
