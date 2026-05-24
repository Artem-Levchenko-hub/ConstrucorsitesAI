"use client";

/**
 * 🎨 Картинки — per-project toggle for auto image generation.
 *
 * When ON (default), each prompt sweeps the AI-generated files for
 * `<img data-omnia-gen="...">` tags and resolves them to real photos via
 * gpt-image-1 (gateway → MinIO). When OFF, the tags pass through and render
 * as broken images — useful when the owner wants to iterate on a layout
 * without burning ₽ on regenerated photos every prompt.
 *
 * Mutation is optimistic so the chip flips immediately; on error we revert
 * and surface a toast. The page-level `["project", projectId]` cache is
 * patched so other consumers (Workspace, PreviewFrame) see the new value
 * without an extra round trip.
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Image as ImageIcon, ImageOff } from "lucide-react";
import { toast } from "sonner";
import { updateProject } from "@/lib/api/projects";
import type { Project } from "@/lib/api/types";
import { cn } from "@/lib/utils";

// Rough per-prompt cost estimate shown in the tooltip. Mirrors the per-image
// ceiling in the gateway (`_PRICE_PER_IMAGE_RUB = 1.5₽`) × the api-side cap
// (`MAX_IMAGES_PER_RESOLVE = 30`).
const MAX_IMAGES = 30;
const PRICE_PER_IMAGE_RUB = 1.5;

export function ImageGenToggle({
  projectId,
  imageGenEnabled,
}: {
  projectId: string;
  imageGenEnabled: boolean;
}) {
  const qc = useQueryClient();

  const mut = useMutation({
    mutationFn: (next: boolean) =>
      updateProject(projectId, { image_gen_enabled: next }),
    onMutate: async (next) => {
      await qc.cancelQueries({ queryKey: ["project", projectId] });
      const prev = qc.getQueryData<Project>(["project", projectId]);
      if (prev) {
        qc.setQueryData<Project>(["project", projectId], {
          ...prev,
          image_gen_enabled: next,
        });
      }
      return { prev };
    },
    onError: (err, _next, ctx) => {
      if (ctx?.prev) {
        qc.setQueryData(["project", projectId], ctx.prev);
      }
      const msg = err instanceof Error ? err.message : "не удалось переключить";
      toast.error("Не удалось обновить настройку картинок", { description: msg });
    },
    onSuccess: (project) => {
      qc.setQueryData(["project", projectId], project);
      toast.success(
        project.image_gen_enabled
          ? "Картинки будут генерироваться автоматически"
          : "Картинки выключены — теги остаются в коде",
      );
    },
  });

  const enabled = imageGenEnabled;
  const tooltip = enabled
    ? `Авто-генерация картинок включена. До ${MAX_IMAGES} картинок на промпт, ≈₽${Math.round(MAX_IMAGES * PRICE_PER_IMAGE_RUB)} максимум.`
    : "Авто-генерация картинок выключена. AI-теги останутся в коде без подмены.";

  return (
    <button
      type="button"
      aria-pressed={enabled}
      onClick={() => mut.mutate(!enabled)}
      disabled={mut.isPending}
      title={tooltip}
      className={cn(
        "inline-flex items-center gap-1 h-7 px-2 rounded-md text-xs whitespace-nowrap transition-colors border",
        enabled
          ? "border-accent/40 bg-accent/[0.08] text-fg-primary hover:bg-accent/[0.14]"
          : "border-border-default bg-surface-base text-fg-tertiary hover:bg-surface-muted",
        mut.isPending && "opacity-60 cursor-wait",
      )}
    >
      {enabled ? (
        <ImageIcon className="h-3.5 w-3.5" />
      ) : (
        <ImageOff className="h-3.5 w-3.5" />
      )}
      <span>Картинки</span>
    </button>
  );
}
