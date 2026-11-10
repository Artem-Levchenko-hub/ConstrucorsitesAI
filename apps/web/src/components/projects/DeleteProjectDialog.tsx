"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { LoaderCircle } from "lucide-react";
import { toast } from "sonner";
import { ApiError } from "@/lib/api/client";
import { deleteProject } from "@/lib/api/projects";
import type { Project } from "@/lib/api/types";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

const projectLists = { queryKey: ["projects"] } as const;

/**
 * Confirm-to-delete. A single confirmation click — no type-the-name speed-bump
 * (owner: «просто удалить, и все»). Still a one-step guard so a destructive,
 * irreversible teardown (the live app + its data + git history) isn't a stray
 * click, but there is nothing to type.
 *
 * Controlled `open`/`onOpenChange` so the parent (the card's menu) owns the
 * trigger; this component only renders the dialog body.
 */
export function DeleteProjectDialog({
  project,
  open,
  onOpenChange,
  onCloseAutoFocus,
  onAccepted,
  maxStudio = false,
}: {
  project: Project;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCloseAutoFocus?: (event: Event) => void;
  onAccepted?: () => void;
  maxStudio?: boolean;
}) {
  const qc = useQueryClient();
  const mutation = useMutation({
    mutationFn: () => deleteProject(project.id),
    onSuccess: async (result) => {
      // An in-flight list read may predate the accepted deletion.
      await qc.cancelQueries(projectLists);
      onAccepted?.();
      qc.setQueriesData<Project[]>(projectLists, (list) => list?.filter(({ id }) => id !== project.id));
      void qc.invalidateQueries(projectLists);
      if (result.state === "deleting") {
        toast.success(`Проект «${project.name}» убран из списка`, {
          description: "Очистка продолжится в фоне.",
        });
      } else {
        toast.success(`Проект «${project.name}» удалён`);
      }
      onOpenChange(false);
    },
    onError: (error) => {
      // The acknowledgement can be lost after the server commits the intent.
      void qc.invalidateQueries(projectLists);
      if (error instanceof ApiError && error.code === "generation_draining") {
        toast.info("Сервис обновляется", {
          id: `project-delete-${project.id}`,
          description: "Подождите несколько минут и повторите удаление.",
        });
        return;
      }
      toast.error(error instanceof Error ? error.message : "Не удалось удалить проект. Попробуйте ещё раз.");
    },
  });
  const pending = mutation.isPending;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-max-studio={maxStudio || undefined} onCloseAutoFocus={onCloseAutoFocus}>
        <DialogHeader className="pr-8">
          <DialogTitle>{pending ? "Удаляем проект" : "Удалить проект?"}</DialogTitle>
          <DialogDescription aria-live="polite">
            {pending
              ? "Ждём подтверждения сервера. Как только он примет запрос, проект исчезнет из списка."
              : <>Это безвозвратно удалит проект «{project.name}», его версии, файлы и данные.</>}
          </DialogDescription>
        </DialogHeader>

        <DialogFooter className="gap-2">
          <Button
            type="button"
            variant="ghost"
            size="lg"
            onClick={() => onOpenChange(false)}
            autoFocus
          >
            {pending ? "Закрыть" : "Отмена"}
          </Button>
          <Button
            type="button"
            variant="danger"
            size="lg"
            className="disabled:opacity-100"
            onClick={() => mutation.mutate()}
            disabled={pending}
            aria-busy={pending}
          >
            {pending && <LoaderCircle aria-hidden="true" className="motion-safe:animate-spin" />}
            {pending ? "Удаление…" : "Удалить навсегда"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
