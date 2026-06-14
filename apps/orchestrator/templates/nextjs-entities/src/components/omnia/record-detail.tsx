"use client";

import * as React from "react";
import { ImageIcon } from "lucide-react";

import { cn } from "@/lib/utils";
import type { MediaCardProps } from "./gallery-grid";

const ASPECT: Record<NonNullable<MediaCardProps["aspect"]>, string> = {
  video: "aspect-video",
  square: "aspect-square",
  "4/3": "aspect-[4/3]",
  "3/2": "aspect-[3/2]",
};

export interface DetailField {
  label: React.ReactNode;
  value: React.ReactNode;
}

export interface RecordDetailProps {
  /** Dominant heading — the record's name. */
  title: React.ReactNode;
  /** Muted eyebrow above the title (category, kind label, author…). */
  eyebrow?: React.ReactNode;
  /**
   * Cover image (a seeded `data:` tile or a real URL) OR a custom node. When
   * set, the layout splits into a cover panel + info panel on desktop; without
   * it the detail is a single column led by the title.
   */
  image?: string | React.ReactNode;
  imageAlt?: string;
  /** Status / «Хит» pill shown next to the title. */
  badge?: React.ReactNode;
  /** Prominent accent value, e.g. a price. */
  price?: React.ReactNode;
  /** Quiet value beside the price, e.g. a rating. */
  metaRight?: React.ReactNode;
  /** Every remaining field of the record, label→value — the attribute list. */
  fields: DetailField[];
  /** Glyph for the cover placeholder when there is no image. */
  icon?: React.ReactNode;
  /** Cover aspect ratio. Default "4/3". */
  aspect?: MediaCardProps["aspect"];
  className?: string;
}

function Cover({
  image,
  imageAlt,
  badge,
  icon,
  aspect = "4/3",
}: Pick<RecordDetailProps, "image" | "imageAlt" | "badge" | "icon" | "aspect">) {
  return (
    <div className={cn("relative overflow-hidden rounded-xl bg-muted", ASPECT[aspect])}>
      {typeof image === "string" ? (
        // Plain <img>: renders inline `data:` tiles and real URLs alike with no
        // next/image remotePatterns config, and never shows a broken image.
        // eslint-disable-next-line @next/next/no-img-element
        <img src={image} alt={imageAlt ?? ""} className="size-full object-cover" />
      ) : image ? (
        image
      ) : (
        <div className="flex size-full items-center justify-center bg-gradient-to-br from-primary/15 to-primary/5 text-primary/40 [&_svg]:size-12">
          {icon ?? <ImageIcon />}
        </div>
      )}
      {badge ? (
        <div className="absolute left-3 top-3 inline-flex items-center rounded-full bg-background/90 px-2.5 py-1 text-xs font-medium text-foreground shadow-sm backdrop-blur-sm">
          {badge}
        </div>
      ) : null}
    </div>
  );
}

function TitleBlock({
  eyebrow,
  title,
  badge,
  price,
  metaRight,
  showBadge,
}: Pick<RecordDetailProps, "eyebrow" | "title" | "badge" | "price" | "metaRight"> & {
  /** The cover already carries the badge in the split layout, so suppress it here. */
  showBadge: boolean;
}) {
  return (
    <div className="space-y-2">
      {eyebrow ? (
        <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          {eyebrow}
        </p>
      ) : null}
      <div className="flex items-start gap-2">
        <h3 className="text-xl font-semibold leading-tight tracking-tight text-foreground">
          {title}
        </h3>
        {showBadge && badge ? (
          <span className="mt-0.5 inline-flex shrink-0 items-center rounded-full bg-secondary px-2.5 py-0.5 text-xs font-medium text-secondary-foreground">
            {badge}
          </span>
        ) : null}
      </div>
      {price != null || metaRight != null ? (
        <div className="flex items-baseline gap-3 pt-0.5">
          {price != null ? (
            <span className="text-2xl font-semibold tracking-tight tabular-nums text-foreground">
              {price}
            </span>
          ) : null}
          {metaRight != null ? (
            <span className="text-sm text-muted-foreground tabular-nums">{metaRight}</span>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function FieldList({ fields }: { fields: DetailField[] }) {
  if (fields.length === 0) return null;
  return (
    <dl className="divide-y divide-border overflow-hidden rounded-lg border border-border">
      {fields.map((f, i) => (
        <div
          key={i}
          className="grid grid-cols-1 gap-0.5 px-3.5 py-2.5 text-sm odd:bg-muted/30 sm:grid-cols-[9rem_1fr] sm:gap-3"
        >
          <dt className="text-muted-foreground">{f.label}</dt>
          <dd className="font-medium break-words">{f.value}</dd>
        </div>
      ))}
    </dl>
  );
}

/**
 * A polished read view of one record — the detail analogue of a <DataTable> row
 * or a <MediaCard> (Mobbin: Shopify / Faire / HODINKEE product pages). With an
 * `image` it splits into a cover panel + an info panel (eyebrow, big title,
 * prominent price, attribute list); without one it leads with the title. Far
 * richer than a flat key→value dump, yet still shows every field.
 *
 * Used by <CrudResource>'s row-detail dialog; usable stand-alone for a bespoke
 * detail page or drawer.
 */
export function RecordDetail({
  title,
  eyebrow,
  image,
  imageAlt,
  badge,
  price,
  metaRight,
  fields,
  icon,
  aspect = "4/3",
  className,
}: RecordDetailProps) {
  const hasCover = image != null;

  if (hasCover) {
    return (
      <div className={cn("grid gap-5 sm:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]", className)}>
        <Cover image={image} imageAlt={imageAlt} badge={badge} icon={icon} aspect={aspect} />
        <div className="space-y-4">
          {/* Badge already sits on the cover, so don't repeat it by the title. */}
          <TitleBlock
            eyebrow={eyebrow}
            title={title}
            badge={badge}
            price={price}
            metaRight={metaRight}
            showBadge={false}
          />
          <FieldList fields={fields} />
        </div>
      </div>
    );
  }

  return (
    <div className={cn("space-y-4", className)}>
      <TitleBlock
        eyebrow={eyebrow}
        title={title}
        badge={badge}
        price={price}
        metaRight={metaRight}
        showBadge
      />
      <FieldList fields={fields} />
    </div>
  );
}
