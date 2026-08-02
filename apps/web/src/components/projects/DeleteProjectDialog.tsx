"use client";

import { useRef, type RefObject } from "react";
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
  returnFocusRef,
  successFocusRef,
}: {
  project: Project;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  returnFocusRef?: RefObject<HTMLButtonElement | null>;
  successFocusRef?: RefObject<HTMLElement | null>;
}) {
  const qc = useQueryClient();
  const deletedRef = useRef(false);

  const mutation = useMutation({
    mutationFn: () => deleteProject(project.id),
    onSuccess: () => {
      deletedRef.current = true;
      qc.setQueryData<Project[]>(["projects"], (current) =>
        current?.filter((item) => item.id !== project.id),
      );
      toast.success(`Проект «${project.name}» удалён`);
      onOpenChange(false);
      void qc.invalidateQueries({ queryKey: ["projects"], exact: true });
    },
    onError: () => {
      toast.error("Не удалось удалить проект. Попробуйте ещё раз.");
    },
  });

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (next) deletedRef.current = false;
        if (!mutation.isPending) onOpenChange(next);
      }}
    >
      <DialogContent
        data-light-shell
        className="border-border-default bg-surface-raised text-fg-primary"
        onCloseAutoFocus={(event) => {
          const targetRef = deletedRef.current ? successFocusRef : returnFocusRef;
          if (!targetRef) return;
          event.preventDefault();
          requestAnimationFrame(() => {
            if (targetRef.current?.isConnected) targetRef.current.focus();
          });
        }}
      >
        <DialogHeader>
          <DialogTitle>Удалить проект?</DialogTitle>
          <DialogDescription>
            Проект «{project.name}» исчезнет из Studio. Мы отключим его связь с
            MAX, рабочее окружение и удалим файлы проекта. Действие нельзя отменить.
          </DialogDescription>
        </DialogHeader>

        <DialogFooter>
          <Button
            type="button"
            variant="ghost"
            onClick={() => onOpenChange(false)}
            disabled={mutation.isPending}
            className="min-h-11"
            autoFocus
          >
            Отмена
          </Button>
          <Button
            type="button"
            variant="danger"
            onClick={() => mutation.mutate()}
            disabled={mutation.isPending}
            className="min-h-11"
          >
            {mutation.isPending ? "Удаление…" : "Удалить навсегда"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
