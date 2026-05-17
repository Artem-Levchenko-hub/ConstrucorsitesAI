"use client";

import { useState } from "react";
import { Undo2, Loader2 } from "lucide-react";
import { motion } from "framer-motion";
import type { Snapshot } from "@/lib/api/types";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { cn, formatRelativeTime, shortSha } from "@/lib/utils";

export function SnapshotCard({
  snapshot,
  isCurrent,
  isSelected,
  onSelect,
  onRollback,
  rolling,
}: {
  snapshot: Snapshot;
  isCurrent: boolean;
  isSelected: boolean;
  onSelect: () => void;
  onRollback: () => void;
  rolling: boolean;
}) {
  const [confirmOpen, setConfirmOpen] = useState(false);

  return (
    <motion.div
      initial={{ opacity: 0, x: 10 }}
      animate={{ opacity: 1, x: 0 }}
      whileHover={{ scale: 1.04 }}
      transition={{ duration: 0.18, ease: [0.16, 1, 0.3, 1] }}
      // Hover лифтит карточку на ~4%, выше z-index, чтобы соседи не залезали.
      // origin-center → масштаб равномерно во все стороны, не "выпрыгивает" из ленты.
      style={{ transformOrigin: "center" }}
      className={cn(
        "rounded-md border bg-surface-raised overflow-hidden cursor-pointer transition-colors",
        "hover:z-10 hover:shadow-xl hover:shadow-black/40 relative",
        isSelected
          ? "border-accent"
          : "border-border-default hover:border-border-strong",
      )}
      onClick={onSelect}
      role="button"
      tabIndex={0}
    >
      <div className="aspect-[16/10] bg-surface-base relative overflow-hidden">
        {snapshot.preview_url ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={snapshot.preview_url}
            alt={snapshot.prompt_text ?? "Preview"}
            className="w-full h-full object-cover"
          />
        ) : (
          <div className="absolute inset-0 flex items-center justify-center text-fg-tertiary text-[10px]">
            <Loader2 className="h-3 w-3 animate-spin mr-1" />
            Рендер…
          </div>
        )}

        {isCurrent && (
          <Badge variant="accent" className="absolute top-1.5 left-1.5 text-[9px] px-1.5 py-0">
            Текущая
          </Badge>
        )}
        {snapshot.is_rollback_target && (
          <Badge variant="outline" className="absolute top-1.5 right-1.5 text-[9px] px-1.5 py-0">
            Откат
          </Badge>
        )}
      </div>

      <div className="px-2 py-1.5 space-y-1">
        <div className="text-[11px] text-fg-primary line-clamp-1 leading-4">
          {snapshot.prompt_text ?? (
            <span className="text-fg-tertiary italic">
              {snapshot.parent_id ? "Откат к версии" : "Стартовый"}
            </span>
          )}
        </div>

        <div className="flex items-center justify-between text-[10px] font-mono text-fg-tertiary">
          <span>{shortSha(snapshot.commit_sha)}</span>
          <span>{formatRelativeTime(snapshot.created_at)}</span>
        </div>

        {!isCurrent && (
          <Button
            size="sm"
            variant="secondary"
            className="w-full gap-1 h-6 text-[11px] px-2"
            onClick={(e) => {
              e.stopPropagation();
              setConfirmOpen(true);
            }}
          >
            <Undo2 className="h-3 w-3" />
            Откатить
          </Button>
        )}
      </div>

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Откатиться к этой версии?</DialogTitle>
            <DialogDescription>
              Создадим новый snapshot на основе{" "}
              <span className="font-mono">
                {shortSha(snapshot.commit_sha)}
              </span>
              . Текущее состояние сохранится в истории — вернуться можно в
              один клик.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="ghost"
              onClick={() => setConfirmOpen(false)}
              disabled={rolling}
            >
              Отмена
            </Button>
            <Button
              onClick={() => {
                setConfirmOpen(false);
                onRollback();
              }}
              disabled={rolling}
            >
              {rolling ? "Откат…" : "Откатить"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </motion.div>
  );
}
