"use client";

import { useState } from "react";
import { Undo2, Loader2 } from "lucide-react";
import { AnimatePresence, motion } from "framer-motion";
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
  const [hovered, setHovered] = useState(false);

  return (
    <motion.div
      layout
      initial={{ opacity: 0, x: 10 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ layout: { duration: 0.22, ease: [0.16, 1, 0.3, 1] }, duration: 0.18 }}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      onFocus={() => setHovered(true)}
      onBlur={() => setHovered(false)}
      // Базовая карточка реально (layout-wise) меньше прежней на ~33% —
      // достигается за счёт меньшего шрифта prompt-текста, тонких отступов
      // и схлопывания кнопки "Откатить" в hover-only. На hover мы плавно
      // расширяемся до примерно 80% прежнего размера (вместо 100% — по
      // просьбе уменьшить «взрыв при наведении» на 20%). framer-motion
      // `layout` обеспечивает плавный morph между двумя реальными размерами,
      // без CSS transform — поэтому никаких пустых зазоров в потоке нет.
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
      {/* Preview короче (aspect-[16/7] вместо 16/10) — карточка становится
          ещё ниже в потоке Timeline. На hover пропорции остаются, потому что
          aspect-ratio — это про сам preview, а не про meta-блок снизу. */}
      <div
        className={cn(
          "bg-surface-base relative overflow-hidden transition-[aspect-ratio] duration-200",
          hovered ? "aspect-[16/9]" : "aspect-[16/7]",
        )}
      >
        {snapshot.preview_url ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={snapshot.preview_url}
            alt={snapshot.prompt_text ?? "Preview"}
            className="w-full h-full object-cover"
          />
        ) : (
          <div className="absolute inset-0 flex items-center justify-center text-fg-tertiary text-[9px]">
            <Loader2 className="h-2.5 w-2.5 animate-spin mr-1" />
            Рендер…
          </div>
        )}

        {isCurrent && (
          <Badge
            variant="accent"
            className={cn(
              "absolute top-1 left-1 px-1 py-0 transition-[font-size]",
              hovered ? "text-[8px]" : "text-[6px]",
            )}
          >
            Текущая
          </Badge>
        )}
        {snapshot.is_rollback_target && (
          <Badge
            variant="outline"
            className={cn(
              "absolute top-1 right-1 px-1 py-0 transition-[font-size]",
              hovered ? "text-[8px]" : "text-[6px]",
            )}
          >
            Откат
          </Badge>
        )}
      </div>

      <motion.div
        layout="position"
        className={cn(
          "transition-[padding] duration-200",
          hovered ? "px-1.5 py-1 space-y-0.5" : "px-1 py-0.5 space-y-0",
        )}
      >
        <div
          className={cn(
            "text-fg-primary line-clamp-1 transition-[font-size,line-height] duration-200",
            hovered ? "text-[9px] leading-3.5" : "text-[7px] leading-[10px]",
          )}
        >
          {snapshot.prompt_text ?? (
            <span className="text-fg-tertiary italic">
              {snapshot.parent_id ? "Откат к версии" : "Стартовый"}
            </span>
          )}
        </div>

        <div
          className={cn(
            "flex items-center justify-between font-mono text-fg-tertiary transition-[font-size] duration-200",
            hovered ? "text-[8px]" : "text-[6px]",
          )}
        >
          <span>{shortSha(snapshot.commit_sha)}</span>
          <span>{formatRelativeTime(snapshot.created_at)}</span>
        </div>

        {/* Кнопка "Откатить" появляется только при hover/focus — на компактной
            карточке она занимала бы лишнее место и мешала бы лайнам сетки. */}
        <AnimatePresence initial={false}>
          {!isCurrent && hovered && (
            <motion.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: "auto" }}
              exit={{ opacity: 0, height: 0 }}
              transition={{ duration: 0.18, ease: [0.16, 1, 0.3, 1] }}
            >
              <Button
                size="sm"
                variant="secondary"
                className="w-full gap-1 h-5 text-[10px] px-2 mt-0.5"
                onClick={(e) => {
                  e.stopPropagation();
                  setConfirmOpen(true);
                }}
              >
                <Undo2 className="h-2.5 w-2.5" />
                Откатить
              </Button>
            </motion.div>
          )}
        </AnimatePresence>
      </motion.div>

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
