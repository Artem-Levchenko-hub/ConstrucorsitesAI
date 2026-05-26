"use client";

/**
 * 🎨 Дизайн-пресет — per-project dropdown for the 8 Awwwards-tier presets.
 *
 * Auto-classifier (preset_classifier.py) picks one on the first prompt;
 * this dropdown lets the owner override it. Effective on the NEXT prompt
 * — already-generated snapshots keep whichever preset was used when they
 * ran (no implicit re-render).
 *
 * Pattern mirrors ImageGenToggle:
 * - useQuery binds to ["project", projectId] so optimistic flips here
 *   reach every consumer (TopBar, PreviewFrame, ChatPanel) without
 *   a page refresh.
 * - useMutation is optimistic for instant feedback; rolls back + toasts
 *   on PUT failure so the user notices the override didn't stick.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, ChevronDown, Palette } from "lucide-react";
import { toast } from "sonner";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  listDesignPresets,
  setProjectDesignPreset,
} from "@/lib/api/design-presets";
import { getProject } from "@/lib/api/projects";
import type { Project } from "@/lib/api/types";
import { cn } from "@/lib/utils";

export function DesignPresetSelector({
  projectId,
  initialPresetId,
  initialPresetName,
}: {
  projectId: string;
  initialPresetId?: string;
  initialPresetName?: string;
}) {
  const qc = useQueryClient();

  // Same ["project", projectId] cache key ImageGenToggle uses — when this
  // mutation flips it, ImageGenToggle (and any other consumer) also sees
  // the new preset id without a refetch.
  const { data: project } = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => getProject(projectId),
    initialData: () =>
      ({
        id: projectId,
        design_preset_id: initialPresetId,
        design_preset_name: initialPresetName,
      }) as Project,
    refetchOnMount: false,
    refetchOnWindowFocus: false,
    staleTime: Infinity,
  });

  const { data: presets } = useQuery({
    queryKey: ["design-presets"],
    queryFn: listDesignPresets,
    // Catalog is static — declared in services/design_presets.py. No reason
    // to refetch on mount or window focus; once loaded it's good for the
    // session.
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });

  const mut = useMutation({
    mutationFn: (presetId: string | null) =>
      setProjectDesignPreset(projectId, presetId),
    onMutate: async (next) => {
      await qc.cancelQueries({ queryKey: ["project", projectId] });
      const prev = qc.getQueryData<Project>(["project", projectId]);
      const nextName = next
        ? (presets?.find((p) => p.id === next)?.name ?? next)
        : undefined;
      if (prev) {
        qc.setQueryData<Project>(["project", projectId], {
          ...prev,
          design_preset_id: next ?? undefined,
          design_preset_name: nextName,
        });
      }
      return { prev };
    },
    onError: (err, _next, ctx) => {
      if (ctx?.prev) qc.setQueryData(["project", projectId], ctx.prev);
      const msg = err instanceof Error ? err.message : "не удалось переключить";
      toast.error("Не удалось обновить дизайн-пресет", { description: msg });
    },
    onSuccess: (_res, next) => {
      toast.success(
        next
          ? `Пресет: ${presets?.find((p) => p.id === next)?.name ?? next}`
          : "Пресет сброшен — выберется автоматически на следующем промпте",
      );
    },
  });

  const currentId =
    project?.design_preset_id ?? initialPresetId ?? undefined;
  const currentName =
    project?.design_preset_name ??
    presets?.find((p) => p.id === currentId)?.name ??
    initialPresetName ??
    "Авто";

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          disabled={mut.isPending}
          title={
            currentId
              ? `Дизайн-пресет: ${currentName}. Нажми чтобы сменить.`
              : "Пресет ещё не выбран — будет назначен AI на первом промпте"
          }
          className={cn(
            "inline-flex items-center gap-1 h-7 px-2.5 rounded-full text-xs whitespace-nowrap transition-all border",
            "border-border-default bg-surface-raised text-fg-secondary hover:border-border-strong hover:text-fg-primary",
            mut.isPending && "opacity-60 cursor-wait",
          )}
        >
          <Palette className="h-3.5 w-3.5" />
          <span className="truncate max-w-[140px]">{currentName}</span>
          <ChevronDown className="h-3 w-3 text-fg-tertiary" />
        </button>
      </DropdownMenuTrigger>

      <DropdownMenuContent align="end" className="w-72">
        <DropdownMenuLabel className="font-normal">
          <div className="text-xs text-fg-tertiary">Дизайн-пресет</div>
          <div className="text-[11px] text-fg-tertiary mt-0.5 leading-snug">
            Влияет на следующий промпт. Уже сгенерированные снапшоты не
            перерисовываются.
          </div>
        </DropdownMenuLabel>
        <DropdownMenuSeparator />

        {(presets ?? []).map((p) => {
          const selected = p.id === currentId;
          return (
            <DropdownMenuItem
              key={p.id}
              onSelect={() => {
                if (!selected) mut.mutate(p.id);
              }}
              className="flex items-start gap-2 py-2"
            >
              <div
                aria-hidden="true"
                className="mt-1 h-4 w-4 rounded-md border border-border-subtle shrink-0"
                style={{
                  background: `linear-gradient(135deg, ${p.palette.bg ?? "#fff"} 0 50%, ${p.palette.accent ?? "#000"} 50% 100%)`,
                }}
              />
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-1.5">
                  <span className="text-sm font-medium truncate">{p.name}</span>
                  {selected && (
                    <Check className="h-3 w-3 text-accent shrink-0" />
                  )}
                </div>
                <div className="text-[11px] text-fg-tertiary leading-snug line-clamp-2">
                  {p.one_liner}
                </div>
              </div>
            </DropdownMenuItem>
          );
        })}

        {currentId && (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              onSelect={() => mut.mutate(null)}
              className="text-fg-tertiary text-xs"
            >
              Сбросить — выбрать заново на следующем промпте
            </DropdownMenuItem>
          </>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
