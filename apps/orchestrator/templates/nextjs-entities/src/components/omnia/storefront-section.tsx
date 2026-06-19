import * as React from "react";

import { cn } from "@/lib/utils";

export interface FeatureCardProps {
  /** Lucide icon, rendered in a tinted brand chip (mirrors <StatCard>). */
  icon?: React.ReactNode;
  /** Card heading — the value / service / feature name. */
  title: React.ReactNode;
  /** One or two muted lines under the title. */
  description?: React.ReactNode;
  /** Image-forward card (Mobbin: Claude feature grid). A `data-omnia-gen` <img>
   *  string URL OR a custom node; rendered as a cover above the body. */
  media?: string | React.ReactNode;
  /** Alt text for a string `media` image. */
  mediaAlt?: string;
  /** Small overlay/inline pill, e.g. «Новинка» / «Хит». */
  badge?: React.ReactNode;
  /** Makes the WHOLE card a link to a real route (#anchor / /signin …). */
  href?: string;
  /** Optional quiet footer link instead of a whole-card link. */
  cta?: { label: React.ReactNode; href: string };
  className?: string;
}

/**
 * A single value / service / feature card for the public storefront — the
 * marketing-section analogue of <StatCard> (Mobbin: Booking «Benefits», Contra,
 * Cycle, Claude feature grids). A tinted brand-accent icon chip, a confident
 * title and a muted line or two; optionally image-forward, badged, or linked.
 * Drop these inside a <StorefrontSection columns={3}> and they lay out in a
 * staggered, brand-aware grid for free.
 */
export function FeatureCard({
  icon,
  title,
  description,
  media,
  mediaAlt,
  badge,
  href,
  cta,
  className,
}: FeatureCardProps) {
  const linked = !!href;
  const body = (
    <>
      {media ? (
        <div className="relative aspect-[16/10] w-full overflow-hidden rounded-xl border border-border bg-muted [&_img]:size-full [&_img]:object-cover">
          {typeof media === "string" ? (
            // Plain <img>: no next/image remotePatterns config, renders for
            // inline data: tiles and real URLs alike.
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={media}
              alt={mediaAlt ?? ""}
              loading="lazy"
              className="transition-transform duration-500 ease-out group-hover:scale-[1.03] motion-reduce:transition-none motion-reduce:group-hover:scale-100"
            />
          ) : (
            media
          )}
          {badge ? (
            <span className="absolute left-3 top-3 inline-flex items-center rounded-full bg-background/90 px-2.5 py-1 text-xs font-medium text-foreground shadow-sm backdrop-blur-sm">
              {badge}
            </span>
          ) : null}
        </div>
      ) : null}

      <div className="flex flex-1 flex-col">
        {!media && (icon || badge) ? (
          <div className="mb-4 flex items-center justify-between gap-3">
            {icon ? (
              <span className="flex size-11 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary [&_svg]:size-5">
                {icon}
              </span>
            ) : (
              <span />
            )}
            {badge ? (
              <span className="inline-flex items-center rounded-full bg-secondary px-2.5 py-1 text-xs font-medium text-secondary-foreground">
                {badge}
              </span>
            ) : null}
          </div>
        ) : null}

        <h3 className="text-base font-semibold leading-snug tracking-tight text-foreground">
          {title}
        </h3>
        {description ? (
          <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
            {description}
          </p>
        ) : null}

        {cta && !linked ? (
          <a
            href={cta.href}
            className="mt-4 inline-flex items-center gap-1 text-sm font-medium text-primary outline-none hover:underline focus-visible:ring-[3px] focus-visible:ring-ring/40"
          >
            {cta.label}
            <span aria-hidden>→</span>
          </a>
        ) : null}
      </div>
    </>
  );

  const shell = cn(
    "group hover-lift elev-1 flex h-full flex-col gap-4 rounded-2xl border border-border bg-card p-6 outline-none",
    linked && "cursor-pointer focus-visible:ring-[3px] focus-visible:ring-ring/40",
    className,
  );

  return linked ? (
    <a href={href} className={shell}>
      {body}
    </a>
  ) : (
    <div className={shell}>{body}</div>
  );
}

export interface StorefrontSectionProps {
  /** Anchor id so the hero's CTAs can scroll here (#услуги / #features …). */
  id?: string;
  /** Small uppercase brand kicker above the heading. */
  eyebrow?: React.ReactNode;
  /** Section heading — rendered as an <h2> (the page's single <h1> is the hero). */
  title?: React.ReactNode;
  /** One supporting paragraph under the heading — the section's lead. */
  lead?: React.ReactNode;
  /** Heading alignment. `center` centers the header (and grid text). Default `left`. */
  align?: "left" | "center";
  /** Paint a muted background band — use on every OTHER section to give the
   *  public page rhythm (Mobbin: Booking alternates plain / tinted sections). */
  tint?: boolean;
  /** When set, children are wrapped in a staggered, brand-aware responsive grid
   *  of that many columns (for a <FeatureCard> grid). Omit for custom content
   *  (pricing table, testimonials, FAQ) and lay it out yourself. */
  columns?: 2 | 3 | 4;
  /** Optional controls under the lead, e.g. a single section CTA. */
  actions?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}

const GRID_COLS: Record<NonNullable<StorefrontSectionProps["columns"]>, string> = {
  2: "sm:grid-cols-2",
  3: "sm:grid-cols-2 lg:grid-cols-3",
  4: "sm:grid-cols-2 lg:grid-cols-4",
};

/**
 * A public-storefront content section — the connective marketing tissue BELOW
 * <StorefrontHero> on `src/app/page.tsx` (услуги / возможности / преимущества /
 * отзывы / цены / FAQ). It owns the section rhythm an enterprise marketing page
 * has (Linear / Stripe / Framer): a consistent max-width, generous vertical
 * spacing, an eyebrow + <h2> + lead header, and — with `columns` — a staggered,
 * brand-aware card grid, so the most shareable surface stops being hand-rolled
 * raw Tailwind below the fold.
 *
 * Brand-aware out of the box: every accent reads from the palette tokens
 * (`--primary`), so the same section reads as a different brand per niche.
 *
 *   <StorefrontSection
 *     id="услуги" eyebrow="Что мы делаем" title="Услуги клиники"
 *     lead="От профилактики до имплантации — щадяще и прозрачно по цене."
 *     align="center" columns={3}
 *   >
 *     <FeatureCard icon={<Smile />} title="Гигиена и профилактика"
 *       description="Чистка, фторирование, контроль каждые 6 месяцев." />
 *     <FeatureCard icon={<Stethoscope />} title="Лечение кариеса"
 *       description="Безболезненно под местной анестезией за один визит." />
 *     <FeatureCard icon={<Sparkles />} title="Эстетика и отбеливание"
 *       description="Виниры и отбеливание ZOOM с гарантией результата." />
 *   </StorefrontSection>
 */
export function StorefrontSection({
  id,
  eyebrow,
  title,
  lead,
  align = "left",
  tint,
  columns,
  actions,
  children,
  className,
}: StorefrontSectionProps) {
  const centered = align === "center";
  const hasHeader = !!(eyebrow || title || lead || actions);

  return (
    <section
      id={id}
      className={cn(
        "scroll-mt-20 border-b border-border",
        tint && "bg-muted/40",
        className,
      )}
    >
      <div className="mx-auto w-full max-w-6xl px-6 py-20 sm:py-24">
        {hasHeader ? (
          <div
            className={cn(
              "flex flex-col",
              centered ? "items-center text-center" : "items-start text-left",
            )}
          >
            {eyebrow ? (
              <p className="text-sm font-semibold uppercase tracking-[0.18em] text-primary">
                {eyebrow}
              </p>
            ) : null}
            {title ? (
              <h2
                className={cn(
                  "mt-3 max-w-2xl text-pretty text-3xl font-semibold leading-tight tracking-tight text-foreground sm:text-4xl",
                  centered && "mx-auto text-balance",
                )}
              >
                {title}
              </h2>
            ) : null}
            {lead ? (
              <p
                className={cn(
                  "mt-4 max-w-2xl text-lg leading-relaxed text-muted-foreground",
                  centered && "mx-auto",
                )}
              >
                {lead}
              </p>
            ) : null}
            {actions ? (
              <div className={cn("mt-7 flex flex-wrap gap-3", centered && "justify-center")}>
                {actions}
              </div>
            ) : null}
          </div>
        ) : null}

        {columns ? (
          <div
            className={cn(
              "stagger grid grid-cols-1 gap-5",
              GRID_COLS[columns],
              hasHeader && "mt-12",
            )}
          >
            {children}
          </div>
        ) : (
          <div className={cn(hasHeader && "mt-12")}>{children}</div>
        )}
      </div>
    </section>
  );
}
