"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { toast } from "sonner";
import { createProject, importProject } from "@/lib/api/projects";
import { getGithubStatus, getGithubConnectUrl } from "@/lib/api/github";
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

// Zero-friction: no template picker. The project starts blank; the in-chat
// discovery + auto stack-routing decide the real stack (static landing vs
// full SaaS) from what the user describes, then provision it. One click → name
// → workspace.
export function NewProjectDialog() {
  const [open, setOpen] = useState(false);
  // "create" | "import" — toggle between the two dialog modes.
  const [mode, setMode] = useState<"create" | "import">("create");

  // Create-mode state.
  const [name, setName] = useState("");

  // Import-mode state.
  const [repoUrl, setRepoUrl] = useState("");
  const [ref, setRef] = useState("");
  const [importName, setImportName] = useState("");

  const router = useRouter();
  const qc = useQueryClient();

  // Create mutation.
  const createMutation = useMutation({
    mutationFn: createProject,
    onSuccess: (project) => {
      toast.success(`Проект «${project.name}» создан`);
      qc.invalidateQueries({ queryKey: ["projects"] });
      closeAndReset();
      router.push(`/projects/${project.id}`);
    },
    onError: () => {
      toast.error("Не удалось создать проект");
    },
  });

  // Import mutation.
  const importMutation = useMutation({
    mutationFn: importProject,
    onSuccess: (project) => {
      toast.success(`Репозиторий импортирован как «${project.name}»`);
      qc.invalidateQueries({ queryKey: ["projects"] });
      closeAndReset();
      router.push(`/projects/${project.id}`);
    },
    onError: (err: Error) => {
      toast.error(err.message || "Не удалось импортировать репозиторий");
    },
  });

  // GitHub connection status — fail-soft: if it errors we simply don't show the
  // hint. Only fetched when the dialog is open and the user is in import mode.
  const { data: githubStatus } = useQuery({
    queryKey: ["github-status"],
    queryFn: getGithubStatus,
    enabled: open && mode === "import",
    // Background refetch is noisy here; stale is fine for a hint.
    staleTime: 60_000,
    retry: false,
  });

  function closeAndReset() {
    setOpen(false);
    setName("");
    setRepoUrl("");
    setRef("");
    setImportName("");
    setMode("create");
  }

  const handleOpenChange = (next: boolean) => {
    if (!next) closeAndReset();
    else setOpen(true);
  };

  const submitCreate = (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    createMutation.mutate({ name: name.trim(), template: "blank" });
  };

  const submitImport = (e: React.FormEvent) => {
    e.preventDefault();
    if (!repoUrl.trim()) return;
    importMutation.mutate({
      repo_url: repoUrl.trim(),
      ref: ref.trim() || undefined,
      name: importName.trim() || undefined,
    });
  };

  const isPending = createMutation.isPending || importMutation.isPending;

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogTrigger asChild>
        <Button>
          <Plus className="h-4 w-4" />
          Новый проект
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          {/* Mode toggle — pill between «Новый проект» and «Импорт из GitHub». */}
          <div className="flex items-center gap-1 rounded-full border border-border-subtle bg-surface-raised p-0.5 w-fit mb-3">
            <button
              type="button"
              onClick={() => setMode("create")}
              className={`px-3 h-7 rounded-full text-xs font-medium transition-colors ${
                mode === "create"
                  ? "bg-accent text-white shadow-sm"
                  : "text-fg-tertiary hover:text-fg-secondary"
              }`}
            >
              Новый проект
            </button>
            <button
              type="button"
              onClick={() => setMode("import")}
              className={`px-3 h-7 rounded-full text-xs font-medium transition-colors ${
                mode === "import"
                  ? "bg-accent text-white shadow-sm"
                  : "text-fg-tertiary hover:text-fg-secondary"
              }`}
            >
              Импорт из GitHub
            </button>
          </div>

          <DialogTitle>
            {mode === "create" ? "Новый проект" : "Импорт из GitHub"}
          </DialogTitle>
          <DialogDescription>
            {mode === "create"
              ? "Дайте название — остальное Omnia выяснит в чате и соберёт под вас."
              : "Вставьте ссылку на публичный (или приватный с подключённым GitHub) репозиторий."}
          </DialogDescription>
        </DialogHeader>

        {mode === "create" ? (
          <form onSubmit={submitCreate} className="space-y-5">
            <div className="space-y-2">
              <Label htmlFor="project-name">Название</Label>
              <Input
                id="project-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Кофейня в Казани"
                autoFocus
                disabled={isPending}
              />
            </div>

            <DialogFooter>
              <Button
                type="button"
                variant="ghost"
                onClick={closeAndReset}
              >
                Отмена
              </Button>
              <Button
                type="submit"
                disabled={isPending || !name.trim()}
              >
                {createMutation.isPending ? "Создание…" : "Создать"}
              </Button>
            </DialogFooter>
          </form>
        ) : (
          <form onSubmit={submitImport} className="space-y-5">
            <div className="space-y-2">
              <Label htmlFor="import-url">Ссылка на репозиторий</Label>
              <Input
                id="import-url"
                value={repoUrl}
                onChange={(e) => setRepoUrl(e.target.value)}
                placeholder="https://github.com/owner/repo"
                autoFocus
                disabled={isPending}
              />
              {/* Fail-soft GitHub hint: shown only when status is loaded and NOT connected. */}
              {githubStatus && !githubStatus.connected && (
                <p className="text-[11px] text-fg-tertiary leading-4">
                  Приватные репозитории —{" "}
                  <button
                    type="button"
                    className="underline text-accent hover:opacity-80"
                    onClick={async () => {
                      try {
                        const { authorize_url } = await getGithubConnectUrl();
                        window.location.assign(authorize_url);
                      } catch {
                        toast.error("Не удалось получить ссылку для подключения GitHub");
                      }
                    }}
                  >
                    подключите GitHub
                  </button>
                </p>
              )}
            </div>

            <div className="space-y-2">
              <Label htmlFor="import-ref">
                Ветка или тег{" "}
                <span className="text-fg-tertiary font-normal">(необязательно)</span>
              </Label>
              <Input
                id="import-ref"
                value={ref}
                onChange={(e) => setRef(e.target.value)}
                placeholder="main"
                disabled={isPending}
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="import-name">
                Название проекта{" "}
                <span className="text-fg-tertiary font-normal">(необязательно)</span>
              </Label>
              <Input
                id="import-name"
                value={importName}
                onChange={(e) => setImportName(e.target.value)}
                placeholder="Оставьте пустым — возьмём имя репо"
                disabled={isPending}
              />
            </div>

            <DialogFooter>
              <Button
                type="button"
                variant="ghost"
                onClick={closeAndReset}
                disabled={isPending}
              >
                Отмена
              </Button>
              <Button
                type="submit"
                disabled={isPending || !repoUrl.trim()}
              >
                {importMutation.isPending ? "Импортируем…" : "Импортировать"}
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}
