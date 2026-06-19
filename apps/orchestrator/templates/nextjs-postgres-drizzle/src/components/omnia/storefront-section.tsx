import * as React from "react";

/** Tiny class joiner — the drizzle kit ships no `cn`/`clsx`, so each
 *  self-contained piece joins its own conditional classes. */
function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

export interface FeatureCardProps {
  /** Lucide icon, rendered in a tinted brand chip (mirrors <StatCard>). */
  icon?: React.ReactNode;
  /** Card heading — the value / service / feature name. */
  title: React.ReactNode;
  /** One or two muted lines under the title. */
  description?: React.ReactNode;
  /** Image-forward card (Mobbin: Claude feature grid). A string image URL OR a
   *  custom node; rendered as a cover above the body. */
  media?: string | React.ReactNode;
  /** Alt text for a string `media` image. */
  mediaAlt?: string;
  /** Small overlay/inline pill, e.g. «Новинка» / «Хит». */
  badge?: React.ReactNode;
  /** Makes the WHOLE card a link to a real route (#anchor / /signup …). */
  href?: string;
  /** Optional quiet footer link instead of a whole-card link. */
  cta?: { label: React.ReactNode; href: string };
  className?: string;
}

/**
 * `<FeatureCard>` — a single value / service / feature card for the public
 * storefront, the marketing-section analogue of <StatCard> (Mobbin: Booking
 * «Benefits», Claude feature grids). A tinted brand-accent icon chip, a
 * confident title and a muted line or two; optionally image-forward, badged, or
 * linked. Drop these inside a <StorefrontSection columns={3}> and they lay out in
 * a brand-aware grid for free. Self-contained on the `--brand` token — no shadcn.
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
            <span className="absolute left-3 top-3 inline-flex items-center rounded-full bg-black/60 px-2.5 py-1 text-xs font-medium text-white shadow-sm backdrop-blur-sm">
              {badge}
            </span>
          ) : null}
        </div>
      ) : null}

      <div className="flex flex-1 flex-col">
        {!media && (icon || badge) ? (
          <div className="mb-4 flex items-center justify-between gap-3">
            {icon ? (
              <span className="grid size-11 shrink-0 place-items-center rounded-xl bg-[color-mix(in_oklab,var(--brand),transparent_82%)] text-[var(--brand)] ring-1 ring-inset ring-border [&_svg]:size-5">
                {icon}
              </span>
            ) : (
              <span />
            )}
            {badge ? (
              <span className="inline-flex items-center rounded-full bg-muted px-2.5 py-1 text-xs font-medium text-muted-foreground">
                {badge}
              </span>
            ) : null}
          </div>
        ) : null}

        <h3 className="text-base font-semibold leading-snug tracking-tight text-foreground">
          {title}
        </h3>
        {description ? (
          <p className="mt-2 text-sm leading-relaxed text-muted-foreground">{description}</p>
        ) : null}

        {cta && !linked ? (
          <a
            href={cta.href}
            className="mt-4 inline-flex items-center gap-1 text-sm font-medium text-[var(--brand)] outline-none transition hover:gap-1.5 hover:brightness-110"
          >
            {cta.label}
            <span aria-hidden>→</span>
          </a>
        ) : null}
      </div>
    </>
  );

  const shell = cx(
    "group hover-lift elev-1 flex h-full flex-col gap-4 rounded-2xl border border-border bg-card p-6 outline-none transition-colors hover:border-foreground/20 hover:bg-muted",
    linked && "cursor-pointer",
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
  /** Paint a faint tinted band — use on every OTHER section to give the public
   *  page rhythm (Mobbin: Booking alternates plain / tinted sections). */
  tint?: boolean;
  /** When set, children are wrapped in a brand-aware responsive grid of that many
   *  columns (for a <FeatureCard> grid). Omit for custom content (pricing,
   *  testimonials, FAQ) and lay it out yourself. */
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
 * `<StorefrontSection>` — a public-storefront content section, the connective
 * marketing tissue BELOW <StorefrontHero> on `src/app/page.tsx` (услуги /
 * возможности / преимущества / отзывы / цены / FAQ). It owns the section rhythm
 * an enterprise marketing page has (Linear / Stripe / Framer): a consistent
 * max-width, generous vertical spacing, an eyebrow + <h2> + lead header, and —
 * with `columns` — a brand-aware <FeatureCard> grid, so the most shareable
 * surface stops being hand-rolled raw Tailwind below the fold.
 *
 * Self-contained on the `--brand` token (no shadcn): the same section reads as a
 * different brand per niche the moment the accent changes.
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
 *       description="Виниры и отбеливание с гарантией результата." />
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
    <section id={id} className={cx("scroll-mt-20 border-b border-border", tint && "bg-muted/40", className)}>
      <div className="mx-auto w-full max-w-6xl px-6 py-20 sm:py-24">
        {hasHeader ? (
          <div className={cx("flex flex-col", centered ? "items-center text-center" : "items-start text-left")}>
            {eyebrow ? (
              <p className="text-sm font-semibold uppercase tracking-[0.18em] text-[var(--brand)]">{eyebrow}</p>
            ) : null}
            {title ? (
              <h2
                className={cx(
                  "omnia-display mt-3 max-w-2xl text-pretty text-3xl font-semibold leading-tight tracking-tight text-foreground sm:text-4xl",
                  centered && "mx-auto text-balance",
                )}
              >
                {title}
              </h2>
            ) : null}
            {lead ? (
              <p className={cx("mt-4 max-w-2xl text-lg leading-relaxed text-muted-foreground", centered && "mx-auto")}>
                {lead}
              </p>
            ) : null}
            {actions ? (
              <div className={cx("mt-7 flex flex-wrap gap-3", centered && "justify-center")}>{actions}</div>
            ) : null}
          </div>
        ) : null}

        {columns ? (
          <div className={cx("grid grid-cols-1 gap-5", GRID_COLS[columns], hasHeader && "mt-12")}>{children}</div>
        ) : (
          <div className={cx(hasHeader && "mt-12")}>{children}</div>
        )}
      </div>
    </section>
  );
}
