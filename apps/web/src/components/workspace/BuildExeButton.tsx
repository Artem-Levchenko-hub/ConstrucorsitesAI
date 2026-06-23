"use client";

import { Hammer, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { useExeBuild } from "@/hooks/useExeBuild";

/**
 * «Собрать .exe» — triggers a Windows installer build for the project and
 * tracks progress via the project WebSocket (`exe.*` events).
 *
 * States:
 *   idle / failed  → ghost Button "Собрать .exe" (failed shows retry hint)
 *   starting       → disabled spinner "Готовлю…"
 *   build          → disabled spinner "Собираю…"
 *   heal           → disabled spinner "Чиню… (попытка N)"
 *   ready          → download column: installer link + optional portable link + notice
 */
export function BuildExeButton({ projectId }: { projectId: string }) {
  const { stage, healAttempt, ready, start } = useExeBuild(projectId);

  const handleStart = async () => {
    try {
      await start();
    } catch {
      toast.error("Не удалось запустить сборку .exe");
    }
  };

  // ── Ready state: download links ────────────────────────────────────────────
  if (stage === "ready" && ready) {
    const sizeMb = Math.round(ready.size / 1e6);
    return (
      <div className="flex flex-col items-end gap-1">
        <a
          href={ready.setup_url}
          download
          className="inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium bg-accent text-accent-fg shadow-sm hover:bg-accent-hover transition-colors"
        >
          Скачать установщик ({sizeMb} МБ)
        </a>
        {ready.exe_url && (
          <a
            href={ready.exe_url}
            download
            className="text-xs text-fg-secondary hover:text-fg-primary transition-colors underline underline-offset-2"
          >
            или портативный .exe
          </a>
        )}
        <p className="text-[10px] text-fg-tertiary leading-tight max-w-[220px] text-right">
          Windows может предупредить о неизвестном издателе — это нормально.
        </p>
      </div>
    );
  }

  // ── In-progress states: disabled spinner ──────────────────────────────────
  if (stage === "starting" || stage === "build" || stage === "heal") {
    const label =
      stage === "starting"
        ? "Готовлю…"
        : stage === "build"
          ? "Собираю…"
          : `Чиню… (попытка ${healAttempt})`;
    return (
      <Button variant="ghost" size="sm" className="gap-1.5" disabled>
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
        {label}
      </Button>
    );
  }

  // ── Idle / failed: trigger button ─────────────────────────────────────────
  return (
    <Button
      variant="ghost"
      size="sm"
      className="gap-1.5"
      onClick={handleStart}
      title={
        stage === "failed"
          ? "Сборка не удалась — нажмите для повторной попытки"
          : "Собрать проект в Windows .exe / установщик"
      }
    >
      <Hammer className="h-3.5 w-3.5" />
      <span className="hidden 2xl:inline">
        {stage === "failed" ? "Повторить .exe" : "Собрать .exe"}
      </span>
    </Button>
  );
}
