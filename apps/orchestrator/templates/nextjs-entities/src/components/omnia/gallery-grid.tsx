"use client";

import * as React from "react";
import { ImageIcon, Search } from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "./empty-state";

export interface MediaCardProps {
  /** Image URL (a seeded `data:` tile or a real photo) OR a custom node. When
   *  absent, a tinted gradient placeholder with `icon` is shown — never a broken
   *  image. */
  image?: string | React.ReactNode;
  imageAlt?: string;
  /** Card title — the record's name. */
  title: React.ReactNode;
  /** One muted line under the title (category, location, author…). */
  subtitle?: React.ReactNode;
  /** Overlay pill on the image, e.g. «Хит» / status. Top-left. */
  badge?: React.ReactNode;
  /** Prominent footer value, e.g. a price. */
  price?: React.ReactNode;
  /** Quiet footer value on the right, e.g. a rating or area. */
  metaRight?: React.ReactNode;
  /** Glyph shown in the placeholder when there is no image. */
  icon?: React.ReactNode;
  /** Image aspect ratio. Default "4/3". */
  aspect?: "video" | "square" | "4/3" | "3/2";
  /** Hover-revealed controls (edit / delete) pinned top-right. Clicks here do
   *  not trigger the card's own `onClick`. */
  actions?: React.ReactNode;
  onClick?: () => void;
  className?: string;
}

const ASPECT: Record<NonNullable<MediaCardProps["aspect"]>, string> = {
  video: "aspect-video",
  square: "aspect-square",
  "4/3": "aspect-[4/3]",
  "3/2": "aspect-[3/2]",
};

/**
 * A single image-forward record card — the catalog/gallery analogue of a table
 * row (Mobbin: Airbnb, Booking, Shopify product grids). A cover image with a
 * slow hover-zoom, an optional overlay badge, the title + one muted line, and a
 * price/meta footer. Use it inside <GalleryGrid>, or stand-alone.
 */
export function MediaCard({
  image,
  imageAlt,
  title,
  subtitle,
  badge,
  price,
  metaRight,
  icon,
  aspect = "4/3",
  actions,
  onClick,
  className,
}: MediaCardProps) {
  const interactive = !!onClick;
  return (
    <div
      onClick={onClick}
      role={interactive ? "button" : undefined}
      tabIndex={interactive ? 0 : undefined}
      onKeyDown={
        interactive
          ? (e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onClick?.();
              }
            }
          : undefined
      }
      className={cn(
        "group hover-lift elev-1 flex flex-col overflow-hidden rounded-xl border border-border bg-card outline-none",
        interactive &&
          "cursor-pointer focus-visible:ring-[3px] focus-visible:ring-ring/40",
        className,
      )}
    >
      <div className={cn("relative overflow-hidden bg-muted", ASPECT[aspect])}>
        {typeof image === "string" ? (
          // Plain <img>: works for inline `data:` tiles and real URLs alike with
          // no next/image remotePatterns config, and always renders.
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={image}
            alt={imageAlt ?? ""}
            loading="lazy"
            className="size-full object-cover transition-transform duration-500 ease-out group-hover:scale-105 motion-reduce:transition-none motion-reduce:group-hover:scale-100"
          />
        ) : image ? (
          image
        ) : (
          <div className="flex size-full items-center justify-center bg-gradient-to-br from-primary/15 to-primary/5 text-primary/40 [&_svg]:size-10">
            {icon ?? <ImageIcon />}
          </div>
        )}
        {badge ? (
          <div className="absolute left-3 top-3 inline-flex items-center rounded-full bg-background/90 px-2.5 py-1 text-xs font-medium text-foreground shadow-sm backdrop-blur-sm">
            {badge}
          </div>
        ) : null}
        {actions ? (
          <div
            onClick={(e) => e.stopPropagation()}
            className="absolute right-2 top-2 flex items-center gap-0.5 rounded-lg bg-background/85 p-0.5 opacity-0 shadow-sm backdrop-blur-sm transition-opacity group-hover:opacity-100 focus-within:opacity-100"
          >
            {actions}
          </div>
        ) : null}
      </div>

      <div className="flex flex-1 flex-col gap-1 p-4">
        <p className="truncate font-semibold leading-tight tracking-tight">{title}</p>
        {subtitle ? (
          <p className="truncate text-sm text-muted-foreground">{subtitle}</p>
        ) : null}
        {price != null || metaRight != null ? (
          <div className="mt-auto flex items-baseline justify-between gap-3 pt-2">
            {price != null ? (
              <span className="text-base font-semibold tracking-tight tabular-nums">
                {price}
              </span>
            ) : (
              <span />
            )}
            {metaRight != null ? (
              <span className="shrink-0 text-sm text-muted-foreground tabular-nums">
                {metaRight}
              </span>
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}

export interface GalleryItem extends MediaCardProps {
  id: string;
  /** Extra text matched by the search box (beyond a string title/subtitle). */
  keywords?: string;
}

export interface GalleryGridProps {
  items: GalleryItem[];
  loading?: boolean;
  /** Column count at the widest breakpoint; responsive below. Default 3. */
  columns?: 2 | 3 | 4;
  /** Image aspect for every card. Default "4/3". */
  aspect?: MediaCardProps["aspect"];
  searchable?: boolean;
  searchPlaceholder?: string;
  /** How many cards to show before a «Показать ещё» button. Default 12. */
  pageSize?: number;
  /** Override the first-run empty state entirely. */
  empty?: React.ReactNode;
  /** Primary CTA inside the first-run empty state (no items at all). */
  emptyAction?: React.ReactNode;
  /** Controls on the toolbar row, left of the search box. */
  toolbar?: React.ReactNode;
  className?: string;
}

const COLS: Record<NonNullable<GalleryGridProps["columns"]>, string> = {
  2: "sm:grid-cols-2",
  3: "sm:grid-cols-2 lg:grid-cols-3",
  4: "sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4",
};

const DEFAULT_PAGE_SIZE = 12;

function searchText(item: GalleryItem): string {
  const parts: string[] = [];
  if (typeof item.title === "string") parts.push(item.title);
  if (typeof item.subtitle === "string") parts.push(item.subtitle);
  if (item.keywords) parts.push(item.keywords);
  return parts.join(" ").toLowerCase();
}

/**
 * An image-forward collection view — the visual alternative to <DataTable> for
 * niches a card grid sells better than a row of text (каталог товаров,
 * недвижимость, меню, портфолио, события). Cover image, title, price/meta, a
 * staggered entrance and a slow hover-zoom. Optional search + «Показать ещё», a
 * loading skeleton and a warm empty state, all handled here.
 *
 *   <GalleryGrid
 *     searchable
 *     items={products.map((p) => ({
 *       id: p.id, image: p.image, title: p.name, subtitle: p.category,
 *       price: formatRub(p.price), badge: p.isHit ? "Хит" : undefined,
 *       onClick: () => open(p),
 *     }))}
 *   />
 */
export function GalleryGrid({
  items,
  loading,
  columns = 3,
  aspect = "4/3",
  searchable,
  searchPlaceholder = "Поиск…",
  pageSize = DEFAULT_PAGE_SIZE,
  empty,
  emptyAction,
  toolbar,
  className,
}: GalleryGridProps) {
  const [query, setQuery] = React.useState("");
  const [shown, setShown] = React.useState(pageSize);

  const all = Array.isArray(items) ? items : [];
  const rawCount = all.length;

  const filtered = React.useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return all;
    return all.filter((it) => searchText(it).includes(q));
  }, [all, query]);

  // Reset the «Показать ещё» window whenever the result set changes.
  React.useEffect(() => {
    setShown(pageSize);
  }, [query, pageSize]);

  const visible = filtered.slice(0, shown);
  const noRecords = rawCount === 0;
  const gridCols = COLS[columns];

  return (
    <div
      className={cn("space-y-4", className)}
      data-omnia-collection=""
      data-omnia-rows={rawCount}
    >
      {(searchable || toolbar) && rawCount > 0 ? (
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex flex-wrap items-center gap-2">{toolbar}</div>
          {searchable ? (
            <div className="relative sm:w-64">
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

      {loading ? (
        <div className={cn("grid grid-cols-1 gap-4", gridCols)}>
          {Array.from({ length: Math.min(pageSize, 6) }).map((_, i) => (
            <div
              key={`s-${i}`}
              className="overflow-hidden rounded-xl border border-border bg-card"
            >
              <Skeleton className={cn("w-full rounded-none", ASPECT[aspect ?? "4/3"])} />
              <div className="space-y-2 p-4">
                <Skeleton className="h-4 w-3/4" />
                <Skeleton className="h-3 w-1/2" />
                <Skeleton className="h-4 w-1/3" />
              </div>
            </div>
          ))}
        </div>
      ) : visible.length === 0 ? (
        empty ?? (
          <EmptyState
            illustration={noRecords ? "list" : "search"}
            title={noRecords ? "Пока пусто" : "Ничего не найдено"}
            description={
              noRecords
                ? "Здесь появятся записи, как только вы их добавите."
                : "Измените запрос."
            }
            action={noRecords ? emptyAction : undefined}
          />
        )
      ) : (
        <>
          <div className={cn("stagger grid grid-cols-1 gap-4", gridCols)}>
            {visible.map((item) => {
              const { id, keywords: _keywords, ...card } = item;
              return <MediaCard key={id} aspect={aspect} {...card} />;
            })}
          </div>
          {shown < filtered.length ? (
            <div className="flex justify-center pt-2">
              <Button
                type="button"
                variant="outline"
                onClick={() => setShown((n) => n + pageSize)}
              >
                Показать ещё
              </Button>
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}
