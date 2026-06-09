"use client";

import { useState } from "react";
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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

/**
 * Type-to-confirm deletion. The owner must type the project's exact name before
 * the destructive action unlocks — a deliberate speed-bump for an irreversible
 * (from the user's side) teardown that removes the live app, its data, and its
 * git history.
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
  const [confirmText, setConfirmText] = useState("");
  const qc = useQueryClient();

  const mutation = useMutation({
    mutationFn: () => deleteProject(project.id),
    onSuccess: () => {
      toast.success(`Проект «${project.name}» удалён`);
      qc.invalidateQueries({ queryKey: ["projects"] });
      onOpenChange(false);
      setConfirmText("");
    },
    onError: () => {
      toast.error("Не удалось удалить проект. Попробуйте ещё раз.");
    },
  });

  const matches = confirmText.trim() === project.name.trim();

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!matches) return;
    mutation.mutate();
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!mutation.isPending) {
          onOpenChange(next);
          if (!next) setConfirmText("");
        }
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

        <form onSubmit={submit} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="confirm-name">
              Введите название проекта, чтобы подтвердить
            </Label>
            <Input
              id="confirm-name"
              value={confirmText}
              onChange={(e) => setConfirmText(e.target.value)}
              placeholder={project.name}
              autoComplete="off"
              autoFocus
            />
          </div>

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
              type="submit"
              variant="danger"
              disabled={!matches || mutation.isPending}
            >
              {mutation.isPending ? "Удаление…" : "Удалить навсегда"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
