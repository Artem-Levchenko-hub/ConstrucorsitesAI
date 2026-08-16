"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
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
}: {
  project: Project;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const qc = useQueryClient();

  const mutation = useMutation({
    mutationFn: () => deleteProject(project.id),
    onSuccess: () => {
      toast.success(`Проект «${project.name}» удалён`);
      qc.invalidateQueries({ queryKey: ["projects"] });
      onOpenChange(false);
    },
    onError: () => {
      toast.error("Не удалось удалить проект. Попробуйте ещё раз.");
    },
  });

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!mutation.isPending) onOpenChange(next);
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Удалить проект?</DialogTitle>
          <DialogDescription>
            Это безвозвратно удалит проект «{project.name}», все его снапшоты,
            файлы и — для приложений — рабочий контейнер с данными. Действие
            нельзя отменить.
          </DialogDescription>
        </DialogHeader>

        <DialogFooter>
          <Button
            type="button"
            variant="ghost"
            onClick={() => onOpenChange(false)}
            disabled={mutation.isPending}
          >
            Отмена
          </Button>
          <Button
            type="button"
            variant="danger"
            onClick={() => mutation.mutate()}
            disabled={mutation.isPending}
            autoFocus
          >
            {mutation.isPending ? "Удаление…" : "Удалить навсегда"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
