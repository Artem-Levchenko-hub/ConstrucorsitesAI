import * as React from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export interface StorefrontCta {
  /** Button copy, e.g. «Записаться» / «Открыть каталог». */
  label: React.ReactNode;
  /** Where it goes — a real route (/signin, /dashboard, #прайс …), never a dead «#». */
  href: string;
}

export interface StorefrontStat {
  /** Quiet caption under the figure, e.g. «довольных клиентов». */
  label: React.ReactNode;
  /** The proof figure itself, e.g. «8 лет», «4.9★», «1 200+». Set large + tabular. */
  value: React.ReactNode;
}

export interface StorefrontHeroProps {
  /** Small uppercase kicker above the title — brand category or a one-word promise. */
  eyebrow?: React.ReactNode;
  /** Optional trust pill above the eyebrow (e.g. «№1 в городе» / «Запись онлайн»). */
  badge?: React.ReactNode;
  /** The offer headline. Rendered as the page's single public <h1>. */
  title: React.ReactNode;
  /** One supporting line of context under the headline — the value proposition. */
  subtitle?: React.ReactNode;
  /** The dominant action. Rendered as a large primary button. */
  primaryCta?: StorefrontCta;
  /** Optional second, quieter action (outline). */
  secondaryCta?: StorefrontCta;
  /** Up to 3 brief-derived proof figures shown as a strip under the CTAs. */
  stats?: StorefrontStat[];
  /** Hero visual slot — drop a `data-omnia-gen` <img> (or any node) here. On `left`
   *  align it sits to the right on desktop; on `center` it sits as a wide frame below.
   *  Omit it and the hero is a confident type-first banner. */
  media?: React.ReactNode;
  /** Layout: `left` = split (text left, media right on desktop); `center` = centered
   *  type-first with the media as a wide frame below. Defaults to `left` when `media`
   *  is given, otherwise `center`. */
  align?: "left" | "center";
  className?: string;
}

/**
 * The public storefront's hero band — the full-bleed first screen a visitor (or a
 * shared-link stranger) lands on, the way Linear / Stripe / Framer marketing pages
 * open: one dominant offer set large, a single brand-accent glow for depth, a clear
 * primary CTA, and a quiet strip of proof figures. It is the public counterpart of
 * <DashboardHero> — use it at the TOP of `src/app/page.tsx` (the public «/» route),
 * NOT inside the cabinet.
 *
 * It is brand-aware out of the box: every colour comes from the project palette
 * tokens (`--primary` / `--accent`), so the same component reads as a different
 * brand per niche the moment the art-director sets the palette — no per-app styling.
 *
 *   <StorefrontHero
 *     badge="Запись онлайн"
 *     eyebrow="Стоматология в центре"
 *     title="Здоровая улыбка без страха и очередей"
 *     subtitle="Современная клиника с щадящим лечением и прозрачными ценами."
 *     primaryCta={{ label: "Записаться", href: "/signin" }}
 *     secondaryCta={{ label: "Смотреть услуги", href: "#услуги" }}
 *     stats={[
 *       { value: "8 лет", label: "на рынке" },
 *       { value: "4.9★", label: "средняя оценка" },
 *       { value: "12 000+", label: "пациентов" },
 *     ]}
 *     media={
 *       <img data-omnia-gen="english prompt: bright modern dental clinic interior, soft daylight, 35mm"
 *            alt="Интерьер клиники"
 *            style={{ background: "linear-gradient(135deg,var(--primary),var(--accent))" }}
 *            className="h-full w-full object-cover" />
 *     }
 *   />
 */
export function StorefrontHero({
  eyebrow,
  badge,
  title,
  subtitle,
  primaryCta,
  secondaryCta,
  stats,
  media,
  align,
  className,
}: StorefrontHeroProps) {
  const layout = align ?? (media ? "left" : "center");
  const centered = layout === "center";
  const proof = (stats ?? []).slice(0, 3);

  const copy = (
    <div
      className={cn(
        "stagger flex min-w-0 flex-col",
        centered ? "items-center text-center" : "items-start text-left",
      )}
    >
      {badge ? (
        <span className="mb-5 inline-flex items-center gap-1.5 rounded-full border border-border bg-card/70 px-3 py-1 text-xs font-medium text-muted-foreground backdrop-blur-sm [&_svg]:size-3.5 [&_svg]:text-primary">
          {badge}
        </span>
      ) : null}
      {eyebrow ? (
        <p className="text-sm font-semibold uppercase tracking-[0.18em] text-primary">
          {eyebrow}
        </p>
      ) : null}
      <h1
        className={cn(
          "mt-3 max-w-2xl text-pretty text-4xl font-semibold leading-[1.05] tracking-tight text-foreground sm:text-5xl lg:text-6xl",
          centered && "mx-auto text-balance",
        )}
      >
        {title}
      </h1>
      {subtitle ? (
        <p
          className={cn(
            "mt-5 max-w-xl text-lg leading-relaxed text-muted-foreground",
            centered && "mx-auto",
          )}
        >
          {subtitle}
        </p>
      ) : null}

      {primaryCta || secondaryCta ? (
        <div
          className={cn(
            "mt-8 flex flex-wrap gap-3",
            centered && "justify-center",
          )}
        >
          {primaryCta ? (
            <Button asChild size="lg" className="h-12 px-7 text-base">
              <a href={primaryCta.href}>{primaryCta.label}</a>
            </Button>
          ) : null}
          {secondaryCta ? (
            <Button asChild size="lg" variant="outline" className="h-12 px-7 text-base">
              <a href={secondaryCta.href}>{secondaryCta.label}</a>
            </Button>
          ) : null}
        </div>
      ) : null}

      {proof.length ? (
        <dl
          className={cn(
            "mt-12 flex flex-wrap gap-x-10 gap-y-5 border-t border-border pt-7",
            centered ? "justify-center" : "",
          )}
        >
          {proof.map((s, i) => (
            <div key={i} className={cn("min-w-0", centered && "text-center")}>
              <dt className="order-2 mt-1 text-sm text-muted-foreground">{s.label}</dt>
              <dd className="order-1 text-3xl font-semibold tracking-tight tabular-nums text-foreground">
                {s.value}
              </dd>
            </div>
          ))}
        </dl>
      ) : null}
    </div>
  );

  const frame = media ? (
    <div className="elev-2 fade-up relative aspect-[4/3] w-full overflow-hidden rounded-3xl border border-border bg-muted [&_img]:h-full [&_img]:w-full [&_img]:object-cover">
      {media}
    </div>
  ) : null;

  return (
    <section
      className={cn(
        "relative isolate overflow-hidden border-b border-border",
        className,
      )}
    >
      {/* Brand-accent depth — two soft glows tied to the palette, never chrome. */}
      <div aria-hidden className="pointer-events-none absolute inset-0 -z-10">
        <div className="absolute -right-32 -top-40 size-[34rem] rounded-full bg-primary/15 blur-[110px]" />
        <div className="absolute -bottom-40 -left-24 size-[26rem] rounded-full bg-accent/40 blur-[120px]" />
      </div>

      <div className="mx-auto w-full max-w-6xl px-6 py-20 sm:py-28 lg:py-32">
        {centered ? (
          <div className="flex flex-col items-center">
            {copy}
            {frame ? <div className="mt-16 w-full max-w-4xl">{frame}</div> : null}
          </div>
        ) : (
          <div className="grid items-center gap-12 lg:grid-cols-[minmax(0,1fr)_minmax(0,0.9fr)] lg:gap-16">
            {copy}
            {frame}
          </div>
        )}
      </div>
    </section>
  );
}
