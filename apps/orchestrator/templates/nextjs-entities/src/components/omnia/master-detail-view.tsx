"use client";

import * as React from "react";
import { ChevronLeft, ImageIcon, Search } from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "./empty-state";

/**
 * One entry in the master-detail layout — a single record mapped to a compact
 * rail row plus its full reading pane. `detail` is the rich body shown on the
 * right when the row is selected (a <RecordDetail>); everything else feeds the
 * left-rail row.
 */
export interface SplitItem {
  id: string;
  /** Rail row heading — the record's name. */
  title: React.ReactNode;
  /** One muted line under the title in the rail (a person, a category…). */
  subtitle?: React.ReactNode;
  /** Rail row footer-left value, e.g. a price or an amount. */
  meta?: React.ReactNode;
  /** Rail row footer-right value, e.g. a timestamp or a status. */
  metaRight?: React.ReactNode;
  /** Small pill on the rail row, e.g. a status or priority. */
  badge?: React.ReactNode;
  /** Optional avatar / thumbnail (a seeded `data:` tile or a real URL). */
  image?: string;
  /** The reading-pane body — typically a <RecordDetail> of this record. */
  detail: React.ReactNode;
  /** Edit / delete controls pinned to the pane header. */
  actions?: React.ReactNode;
  /** Extra text matched by the search box. */
  keywords?: string;
}

export interface MasterDetailViewProps {
  items: SplitItem[];
  loading?: boolean;
  searchable?: boolean;
  searchPlaceholder?: string;
  /** Quiet heading above the list rail (entity name / record count). */
  railLabel?: React.ReactNode;
  /** Primary CTA inside the first-run empty state (no records at all). */
  emptyAction?: React.ReactNode;
  className?: string;
}

function rowText(item: SplitItem): string {
  const parts: string[] = [];
  if (typeof item.title === "string") parts.push(item.title);
  if (typeof item.subtitle === "string") parts.push(item.subtitle);
  if (item.keywords) parts.push(item.keywords);
  return parts.join(" ").toLowerCase();
}

/** First letter of a string title for the avatar fallback. */
function initial(title: React.ReactNode): string | null {
  if (typeof title === "string") {
    const ch = title.trim()[0];
    return ch ? ch.toUpperCase() : null;
  }
  return null;
}

function RailThumb({ item }: { item: SplitItem }) {
  if (item.image) {
    return (
      // Plain <img>: renders inline `data:` tiles and real URLs alike with no
      // next/image config, and never shows a broken image.
      // eslint-disable-next-line @next/next/no-img-element
      <img
        src={item.image}
        alt=""
        className="size-10 shrink-0 rounded-lg object-cover"
      />
    );
  }
  const ch = initial(item.title);
  return (
    <div className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-sm font-semibold text-primary [&_svg]:size-4">
      {ch ?? <ImageIcon />}
    </div>
  );
}

function RailRow({
  item,
  active,
  onSelect,
}: {
  item: SplitItem;
  active: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-current={active ? "true" : undefined}
      className={cn(
        "group relative flex w-full items-start gap-3 rounded-xl border px-3 py-2.5 text-left outline-none transition-colors",
        "focus-visible:ring-[3px] focus-visible:ring-ring/40",
        active
          ? "border-primary/40 bg-primary/10"
          : "border-transparent hover:bg-muted/60",
      )}
    >
      {/* Selected accent rail */}
      <span
        aria-hidden
        className={cn(
          "absolute left-0 top-1/2 h-7 w-1 -translate-y-1/2 rounded-r-full bg-primary transition-opacity",
          active ? "opacity-100" : "opacity-0",
        )}
      />
      <RailThumb item={item} />
      <div className="min-w-0 flex-1">
        <div className="flex items-start justify-between gap-2">
          <p
            className={cn(
              "truncate text-sm font-semibold leading-tight tracking-tight",
              active ? "text-foreground" : "text-foreground/90",
            )}
          >
            {item.title}
          </p>
          {item.metaRight != null ? (
            <span className="shrink-0 text-xs text-muted-foreground tabular-nums">
              {item.metaRight}
            </span>
          ) : null}
        </div>
        {item.subtitle != null ? (
          <p className="mt-0.5 truncate text-xs text-muted-foreground">{item.subtitle}</p>
        ) : null}
        {item.meta != null || item.badge != null ? (
          <div className="mt-1 flex items-center justify-between gap-2">
            {item.meta != null ? (
              <span className="text-xs font-semibold tracking-tight tabular-nums text-foreground/80">
                {item.meta}
              </span>
            ) : (
              <span />
            )}
            {item.badge != null ? (
              <span className="shrink-0 rounded-full bg-muted px-2 py-0.5 text-[11px] font-medium text-muted-foreground">
                {item.badge}
              </span>
            ) : null}
          </div>
        ) : null}
      </div>
    </button>
  );
}

/**
 * A master-detail (inbox) layout — the read-heavy alternative to a flat table
 * for entities whose value is ONE rich record at a time: dossiers, profiles,
 * cases, conversations, tickets, documents (Mobbin: Gmail / Linear / Notion /
 * Intercom). A compact list rail on the left; the selected record's full detail
 * fills the reading pane on the right. On mobile the pane takes over the screen
 * with a back button. Branded selection accent, a staggered entrance, optional
 * search, a loading skeleton and a warm empty state.
 *
 *   <MasterDetailView
 *     items={clients.map((c) => ({ id: c.id, title: c.name, subtitle: c.company,
 *       metaRight: formatDate(c.lastContact),
 *       detail: <RecordDetail title={c.name} fields={[…]} /> }))}
 *   />
 */
export function MasterDetailView({
  items,
  loading,
  searchable,
  searchPlaceholder = "Поиск…",
  railLabel,
  emptyAction,
  className,
}: MasterDetailViewProps) {
  const [query, setQuery] = React.useState("");
  const [selectedId, setSelectedId] = React.useState<string | null>(null);
  // Mobile only: whether the reading pane has taken over the screen.
  const [mobileOpen, setMobileOpen] = React.useState(false);

  const all = Array.isArray(items) ? items : [];
  const rawCount = all.length;

  const visible = React.useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return all;
    return all.filter((it) => rowText(it).includes(q));
  }, [all, query]);

  // The shown record: the explicitly selected one if it still exists, else the
  // first available (so the pane is never empty on desktop). Derived — no effect.
  const selected =
    all.find((it) => it.id === selectedId) ?? visible[0] ?? all[0] ?? null;

  function select(id: string) {
    setSelectedId(id);
    setMobileOpen(true);
  }

  if (loading) {
    return (
      <div
        className={cn(
          "grid gap-4 md:h-[calc(100dvh-13rem)] md:min-h-[30rem] md:grid-cols-[minmax(0,21rem)_minmax(0,1fr)]",
          className,
        )}
      >
        <div className="space-y-2.5">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={`sr-${i}`} className="flex items-start gap-3 rounded-xl px-3 py-2.5">
              <Skeleton className="size-10 shrink-0 rounded-lg" />
              <div className="flex-1 space-y-2">
                <Skeleton className="h-4 w-3/5 rounded" />
                <Skeleton className="h-3 w-4/5 rounded" />
              </div>
            </div>
          ))}
        </div>
        <div className="hidden rounded-2xl border border-border bg-card p-6 md:block">
          <Skeleton className="h-7 w-1/2 rounded-md" />
          <Skeleton className="mt-4 h-40 w-full rounded-xl" />
          <div className="mt-4 space-y-2.5">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={`sd-${i}`} className="h-5 w-full rounded" />
            ))}
          </div>
        </div>
      </div>
    );
  }

  if (rawCount === 0) {
    return (
      <EmptyState
        illustration="list"
        title="Пока пусто"
        description="Здесь появятся записи, как только вы их добавите."
        action={emptyAction}
      />
    );
  }

  return (
    <div
      data-omnia-split=""
      data-omnia-rows={rawCount}
      className={cn(
        "grid gap-4 md:h-[calc(100dvh-13rem)] md:min-h-[30rem] md:grid-cols-[minmax(0,21rem)_minmax(0,1fr)]",
        className,
      )}
    >
      {/* List rail — hidden on mobile once a record is open. */}
      <aside
        className={cn(
          "flex min-h-0 flex-col gap-3 md:overflow-hidden",
          mobileOpen && "hidden md:flex",
        )}
      >
        {railLabel != null || searchable ? (
          <div className="flex flex-col gap-2.5">
            {railLabel != null ? (
              <p className="px-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                {railLabel}
              </p>
            ) : null}
            {searchable ? (
              <div className="relative">
                <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder={searchPlaceholder}
                  className="pl-9"
                />
              </div>
            ) : null}
          </div>
        ) : null}

        <div className="stagger -mx-1 flex flex-1 flex-col gap-1.5 overflow-y-auto px-1 pb-1 md:min-h-0">
          {visible.length === 0 ? (
            <div className="rounded-xl border border-dashed border-border/60 px-3 py-8 text-center text-sm text-muted-foreground">
              Ничего не найдено
            </div>
          ) : (
            visible.map((item) => (
              <RailRow
                key={item.id}
                item={item}
                active={selected?.id === item.id}
                onSelect={() => select(item.id)}
              />
            ))
          )}
        </div>
      </aside>

      {/* Reading pane — full-screen on mobile once a record is open. */}
      <section
        className={cn(
          "min-h-0 flex-col rounded-2xl border border-border bg-card md:flex md:overflow-hidden",
          mobileOpen ? "flex" : "hidden md:flex",
        )}
      >
        {selected ? (
          <>
            <header className="flex items-center gap-2 border-b border-border px-4 py-2.5 md:px-5">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setMobileOpen(false)}
                className="-ml-2 md:hidden"
              >
                <ChevronLeft className="size-4" />
                Назад
              </Button>
              {selected.actions ? (
                <div className="ml-auto flex items-center gap-2">{selected.actions}</div>
              ) : null}
            </header>
            <div
              key={selected.id}
              className="fade-in min-h-0 flex-1 overflow-y-auto p-4 md:p-6"
            >
              {selected.detail}
            </div>
          </>
        ) : (
          <div className="flex flex-1 items-center justify-center p-10 text-center text-sm text-muted-foreground">
            Выберите запись слева, чтобы увидеть детали.
          </div>
        )}
      </section>
    </div>
  );
}
