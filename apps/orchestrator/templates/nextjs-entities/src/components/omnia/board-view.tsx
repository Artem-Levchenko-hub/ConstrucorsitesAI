"use client";

import * as React from "react";
import { Search } from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "./empty-state";

/**
 * One column of the board — a stage in the workflow (Новая · В работе · Готово).
 * `value` is the raw status stored on the record; `label` is what the operator
 * reads. The column order is the order you pass them in.
 */
export interface BoardColumn {
  value: string;
  label: React.ReactNode;
}

/**
 * One card on the board — a single record, mapped to a compact text-forward
 * tile. `status` decides which column it lands in; everything else is display.
 */
export interface BoardCard {
  id: string;
  /** Raw status value — matched against a column's `value`. */
  status: string | null | undefined;
  title: React.ReactNode;
  /** One muted line under the title (a person, a category…). */
  subtitle?: React.ReactNode;
  /** Footer-left value, e.g. a price or a due date. */
  meta?: React.ReactNode;
  /** Footer-right value, e.g. an assignee or a count. */
  metaRight?: React.ReactNode;
  /** Small pill, e.g. a priority — NOT the status (that is the column). */
  badge?: React.ReactNode;
  /** Hover-revealed controls (edit / delete) pinned top-right. */
  actions?: React.ReactNode;
  /** Extra text matched by the search box. */
  keywords?: string;
  onClick?: () => void;
}

export interface BoardViewProps {
  columns: BoardColumn[];
  cards: BoardCard[];
  loading?: boolean;
  searchable?: boolean;
  searchPlaceholder?: string;
  /**
   * Let the operator drag a card to another column. When it lands, `onMove` is
   * called with the record id and the destination column's `value`. Off unless
   * `onMove` is wired.
   */
  onMove?: (id: string, toStatus: string) => void | Promise<void>;
  /** Primary CTA inside the first-run empty state (no records at all). */
  emptyAction?: React.ReactNode;
  className?: string;
}

// A monochrome primary-tinted ladder for the column accent bar. Static class
// strings (Tailwind can't see interpolated ones) and token-based, so the whole
// board recolours with the brand/theme — no hard-coded hues to go stale.
const ACCENT = [
  "bg-primary",
  "bg-primary/70",
  "bg-primary/45",
  "bg-primary/85",
  "bg-primary/55",
  "bg-primary/35",
];

function cardText(card: BoardCard): string {
  const parts: string[] = [];
  if (typeof card.title === "string") parts.push(card.title);
  if (typeof card.subtitle === "string") parts.push(card.subtitle);
  if (card.keywords) parts.push(card.keywords);
  return parts.join(" ").toLowerCase();
}

function BoardTile({
  card,
  draggable,
  onDragStart,
  onDragEnd,
}: {
  card: BoardCard;
  draggable: boolean;
  onDragStart?: () => void;
  onDragEnd?: () => void;
}) {
  const interactive = !!card.onClick;
  const hasFooter = card.meta != null || card.metaRight != null;
  return (
    <div
      draggable={draggable}
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
      onClick={card.onClick}
      role={interactive ? "button" : undefined}
      tabIndex={interactive ? 0 : undefined}
      onKeyDown={
        interactive
          ? (e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                card.onClick?.();
              }
            }
          : undefined
      }
      className={cn(
        "group hover-lift elev-1 relative flex flex-col gap-1.5 rounded-xl border border-border bg-card p-3.5 outline-none",
        draggable && "cursor-grab active:cursor-grabbing",
        interactive && "focus-visible:ring-[3px] focus-visible:ring-ring/40",
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <p className="font-semibold leading-tight tracking-tight">{card.title}</p>
        {card.badge != null ? (
          <span className="shrink-0 rounded-full bg-muted px-2 py-0.5 text-xs font-medium text-muted-foreground">
            {card.badge}
          </span>
        ) : null}
      </div>
      {card.subtitle != null ? (
        <p className="truncate text-sm text-muted-foreground">{card.subtitle}</p>
      ) : null}
      {hasFooter ? (
        <div className="mt-1 flex items-baseline justify-between gap-3">
          {card.meta != null ? (
            <span className="text-sm font-semibold tracking-tight tabular-nums">
              {card.meta}
            </span>
          ) : (
            <span />
          )}
          {card.metaRight != null ? (
            <span className="shrink-0 text-xs text-muted-foreground tabular-nums">
              {card.metaRight}
            </span>
          ) : null}
        </div>
      ) : null}
      {card.actions ? (
        <div
          onClick={(e) => e.stopPropagation()}
          className="absolute right-1.5 top-1.5 flex items-center gap-0.5 rounded-lg bg-background/85 p-0.5 opacity-0 shadow-sm backdrop-blur-sm transition-opacity group-hover:opacity-100 focus-within:opacity-100"
        >
          {card.actions}
        </div>
      ) : null}
    </div>
  );
}

/**
 * A drag-and-drop kanban board — the workflow alternative to <DataTable> for
 * anything that moves through stages (заявки, тикеты, заказы, сделки, задачи).
 * Cards are grouped into status columns; drag one to another column and its
 * status is saved. Branded column accents, per-column counts, a staggered
 * entrance, optional search, a loading skeleton and a warm empty state.
 *
 *   <BoardView
 *     columns={[{ value: "new", label: "Новая" }, { value: "done", label: "Готово" }]}
 *     cards={deals.map((d) => ({ id: d.id, status: d.stage, title: d.name,
 *       subtitle: d.client, meta: formatRub(d.amount) }))}
 *     onMove={(id, stage) => deals.update(id, { stage })}
 *   />
 */
export function BoardView({
  columns,
  cards,
  loading,
  searchable,
  searchPlaceholder = "Поиск…",
  onMove,
  emptyAction,
  className,
}: BoardViewProps) {
  const [query, setQuery] = React.useState("");
  const [dragId, setDragId] = React.useState<string | null>(null);
  const [overCol, setOverCol] = React.useState<string | null>(null);
  const canMove = !!onMove;

  const all = Array.isArray(cards) ? cards : [];
  const rawCount = all.length;

  const filtered = React.useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return all;
    return all.filter((c) => cardText(c).includes(q));
  }, [all, query]);

  // Group the (filtered) cards by status into the declared columns, plus a
  // trailing «Без статуса» bucket for any value that matches no column — shown
  // only when it actually holds cards, so a clean board stays clean.
  const known = React.useMemo(() => new Set(columns.map((c) => c.value)), [columns]);
  const grouped = React.useMemo(() => {
    const map = new Map<string, BoardCard[]>();
    for (const col of columns) map.set(col.value, []);
    const orphans: BoardCard[] = [];
    for (const card of filtered) {
      const key = card.status == null ? "" : String(card.status);
      if (known.has(key)) map.get(key)!.push(card);
      else orphans.push(card);
    }
    return { map, orphans };
  }, [filtered, columns, known]);

  const displayColumns: BoardColumn[] = grouped.orphans.length
    ? [...columns, { value: "", label: "Без статуса" }]
    : columns;

  async function drop(toStatus: string) {
    const id = dragId;
    setDragId(null);
    setOverCol(null);
    if (!id || !onMove) return;
    const card = all.find((c) => c.id === id);
    const from = card?.status == null ? "" : String(card.status);
    if (from === toStatus) return; // same column → no-op
    await onMove(id, toStatus);
  }

  const noRecords = rawCount === 0;

  if (loading) {
    return (
      <div className={cn("flex gap-4 overflow-x-auto pb-2", className)}>
        {Array.from({ length: Math.max(3, Math.min(columns.length || 3, 5)) }).map((_, i) => (
          <div key={`sc-${i}`} className="w-72 shrink-0 space-y-3">
            <Skeleton className="h-7 w-32 rounded-md" />
            {Array.from({ length: 3 - (i % 2) }).map((__, j) => (
              <Skeleton key={`st-${i}-${j}`} className="h-24 w-full rounded-xl" />
            ))}
          </div>
        ))}
      </div>
    );
  }

  if (noRecords) {
    return (
      <EmptyState
        illustration="list"
        title="Пока пусто"
        description="Здесь появятся карточки, как только вы добавите записи."
        action={emptyAction}
      />
    );
  }

  return (
    <div className={cn("space-y-4", className)} data-omnia-board="" data-omnia-rows={rawCount}>
      {searchable ? (
        <div className="flex">
          <div className="relative w-full sm:w-72">
            <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={searchPlaceholder}
              className="pl-9"
            />
          </div>
        </div>
      ) : null}

      <div className="flex gap-4 overflow-x-auto pb-2 [scroll-snap-type:x_proximity]">
        {displayColumns.map((col, i) => {
          const items = col.value === "" ? grouped.orphans : grouped.map.get(col.value) ?? [];
          const isOver = canMove && overCol === col.value;
          return (
            <section
              key={col.value || "_none"}
              onDragOver={
                canMove
                  ? (e) => {
                      e.preventDefault();
                      if (overCol !== col.value) setOverCol(col.value);
                    }
                  : undefined
              }
              onDragLeave={
                canMove
                  ? (e) => {
                      // Only clear when the pointer actually leaves the column,
                      // not when crossing onto a child card.
                      if (!e.currentTarget.contains(e.relatedTarget as Node)) {
                        setOverCol((c) => (c === col.value ? null : c));
                      }
                    }
                  : undefined
              }
              onDrop={canMove ? () => void drop(col.value) : undefined}
              className={cn(
                "flex min-h-[60vh] w-72 shrink-0 flex-col rounded-2xl border bg-muted/30 [scroll-snap-align:start]",
                isOver ? "border-primary/50 bg-primary/5 ring-2 ring-primary/30" : "border-border/60",
              )}
            >
              <header className="flex items-center gap-2 px-3.5 pb-2.5 pt-3">
                <span
                  className={cn("h-2.5 w-2.5 rounded-full", ACCENT[i % ACCENT.length])}
                  aria-hidden
                />
                <span className="truncate text-sm font-semibold tracking-tight">{col.label}</span>
                <span className="ml-auto rounded-full bg-background px-2 py-0.5 text-xs font-medium tabular-nums text-muted-foreground">
                  {items.length}
                </span>
              </header>
              <div className="stagger flex min-h-24 flex-1 flex-col gap-2.5 px-2.5 pb-3">
                {items.length === 0 ? (
                  <div className="flex flex-1 items-center justify-center rounded-xl border border-dashed border-border/60 px-3 py-6 text-center text-xs text-muted-foreground">
                    {isOver ? "Отпустите здесь" : "Пусто"}
                  </div>
                ) : (
                  items.map((card) => (
                    <BoardTile
                      key={card.id}
                      card={card}
                      draggable={canMove}
                      onDragStart={() => setDragId(card.id)}
                      onDragEnd={() => {
                        setDragId(null);
                        setOverCol(null);
                      }}
                    />
                  ))
                )}
              </div>
            </section>
          );
        })}
      </div>
    </div>
  );
}
