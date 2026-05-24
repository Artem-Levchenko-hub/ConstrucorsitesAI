"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { toast } from "sonner";
import { createProject } from "@/lib/api/projects";
import type { ProjectTemplate } from "@/lib/api/types";
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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";

const TEMPLATES: Array<{
  id: ProjectTemplate;
  label: string;
  hint: string;
}> = [
  { id: "blank", label: "Пустой", hint: "Начать с чистого листа" },
  { id: "landing", label: "Лендинг", hint: "Hero · фичи · CTA · форма" },
  { id: "portfolio", label: "Портфолио", hint: "Грид работ + о себе" },
  { id: "blog", label: "Блог", hint: "Список постов + страница поста" },
  // V2 Phase A — runs as a real Next.js + Postgres + Drizzle dev container
  // managed by the orchestrator. Promptable as a SaaS, not a static site.
  {
    id: "fullstack",
    label: "Full-stack SaaS",
    hint: "Next.js + Postgres + Drizzle, live dev-сервер",
  },
];

export function NewProjectDialog() {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [template, setTemplate] = useState<ProjectTemplate>("landing");
  const router = useRouter();
  const qc = useQueryClient();

  const mutation = useMutation({
    mutationFn: createProject,
    onSuccess: (project) => {
      toast.success(`Проект «${project.name}» создан`);
      qc.invalidateQueries({ queryKey: ["projects"] });
      setOpen(false);
      setName("");
      router.push(`/projects/${project.id}`);
    },
    onError: () => {
      toast.error("Не удалось создать проект");
    },
  });

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    mutation.mutate({ name: name.trim(), template });
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button>
          <Plus className="h-4 w-4" />
          Новый проект
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Новый проект</DialogTitle>
          <DialogDescription>
            Выберите шаблон и дайте название. Можно менять промптом потом.
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={submit} className="space-y-5">
          <div className="space-y-2">
            <Label htmlFor="project-name">Название</Label>
            <Input
              id="project-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Кофейня в Казани"
              autoFocus
            />
          </div>

          <div className="space-y-2">
            <Label>Шаблон</Label>
            <div className="grid grid-cols-2 gap-2">
              {TEMPLATES.map((t) => (
                <button
                  key={t.id}
                  type="button"
                  onClick={() => setTemplate(t.id)}
                  className={cn(
                    "rounded-md border p-3 text-left transition-colors",
                    template === t.id
                      ? "border-accent bg-accent-subtle"
                      : "border-border-default hover:border-border-strong",
                  )}
                >
                  <div className="text-sm font-medium">{t.label}</div>
                  <div className="text-xs text-fg-tertiary mt-0.5">
                    {t.hint}
                  </div>
                </button>
              ))}
            </div>
          </div>

          <DialogFooter>
            <Button
              type="button"
              variant="ghost"
              onClick={() => setOpen(false)}
            >
              Отмена
            </Button>
            <Button
              type="submit"
              disabled={mutation.isPending || !name.trim()}
            >
              {mutation.isPending ? "Создание…" : "Создать"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
