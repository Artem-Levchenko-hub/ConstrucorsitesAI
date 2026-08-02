"use client";

import { GitCommitHorizontal, Loader2 } from "lucide-react";

import type { Snapshot } from "@/lib/api/types";
import {
  maxSnapshotLabel,
  maxSnapshotVersion,
} from "@/lib/max-version-history";
import { cn } from "@/lib/utils";

export function MaxVersionRail({
  snapshots,
  currentSnapshotId,
  selectedSnapshotId,
  loading,
  onSelect,
}: {
  snapshots: Snapshot[];
  currentSnapshotId: string | null;
  selectedSnapshotId: string | null;
  loading: boolean;
  onSelect: (snapshotId: string | null) => void;
}) {
  return (
    <nav
      className="max-projects-scroll relative h-full w-[76px] shrink-0 overflow-y-auto overscroll-contain py-2 pl-1 pr-1"
      aria-label="История версий"
      aria-busy={loading}
      data-testid="max-version-rail"
    >
      {loading ? (
        <div className="flex min-h-28 items-center justify-center" role="status">
          <Loader2 className="size-3.5 animate-spin text-accent" />
          <span className="sr-only">Загружаем историю версий</span>
        </div>
      ) : snapshots.length === 0 ? (
        <div className="flex min-h-28 flex-col items-center justify-center gap-2 px-1 text-center text-[9px] leading-3 text-[#aaa59b]">
          <GitCommitHorizontal className="size-3.5" />
          Версии появятся здесь
        </div>
      ) : (
        <ol className="relative flex min-h-full flex-col items-stretch justify-center py-1">
          <span
            className="absolute bottom-6 left-[15px] top-6 w-px bg-[#d8d4cb]"
            aria-hidden="true"
          />
          {snapshots.map((snapshot) => {
            const version = maxSnapshotVersion(snapshots, snapshot.id);
            const isCurrent = snapshot.id === currentSnapshotId;
            const isSelected = snapshot.id === selectedSnapshotId;
            const label = maxSnapshotLabel(snapshot);
            const stateLabel = isCurrent
              ? "текущая"
              : isSelected
                ? "открыта для просмотра"
                : "";

            return (
              <li key={snapshot.id} className="relative z-10 min-h-11">
                <button
                  type="button"
                  onClick={() => onSelect(isCurrent ? null : snapshot.id)}
                  aria-pressed={isCurrent || isSelected}
                  aria-label={`Версия ${version}: ${label}${stateLabel ? `, ${stateLabel}` : ""}`}
                  title={`v${version} · ${label}`}
                  className={cn(
                    "group grid min-h-11 w-full grid-cols-[22px_minmax(0,1fr)] items-center gap-1 rounded-[8px] text-left transition-colors focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent",
                    isSelected
                      ? "bg-accent/10"
                      : "hover:bg-[#f5f3ee]",
                  )}
                  data-testid={`max-version-${version}`}
                >
                  <span className="grid size-[22px] place-items-center">
                    <span
                      className={cn(
                        "block size-2.5 rounded-full border-2 bg-[#fcfbf7] transition-[border-color,background-color,box-shadow]",
                        isCurrent
                          ? "border-accent bg-accent shadow-[0_0_0_3px_rgba(85,79,196,.12)]"
                          : isSelected
                            ? "border-accent bg-[#fcfbf7] shadow-[0_0_0_3px_rgba(85,79,196,.12)]"
                            : "border-[#aaa59b] group-hover:border-accent",
                      )}
                      aria-hidden="true"
                    />
                  </span>
                  <span className="min-w-0 pr-0.5">
                    <span
                      className={cn(
                        "block text-[9px] font-semibold leading-3 tabular-nums",
                        isCurrent || isSelected
                          ? "text-accent"
                          : "text-[#6d6962]",
                      )}
                    >
                      v{version}
                    </span>
                    <span className="block truncate text-[9px] leading-3 text-[#6d6962]">
                      {isCurrent ? "Текущая" : label}
                    </span>
                  </span>
                </button>
              </li>
            );
          })}
        </ol>
      )}
    </nav>
  );
}
