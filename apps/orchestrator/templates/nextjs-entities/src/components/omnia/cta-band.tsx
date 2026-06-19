import * as React from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export interface CtaBandAction {
  /** Button copy, e.g. «Записаться онлайн» / «Создать аккаунт». */
  label: React.ReactNode;
  /** Where it goes — a real route (/signin, /dashboard, #прайс …), never a dead «#». */
  href: string;
}

export interface CtaBandProps {
  /** Small uppercase kicker above the headline (Blue Apron «HUNGRY FOR MORE?»). */
  eyebrow?: React.ReactNode;
  /** The closing ask. Rendered large as an <h2> (the page's single <h1> is the hero). */
  title: React.ReactNode;
  /** One supporting line under the headline — the final nudge. */
  subtitle?: React.ReactNode;
  /** The dominant action. On the brand band it renders as a high-contrast light pill. */
  primaryCta?: CtaBandAction;
  /** Optional second, quieter action (ghost outline that reads on the band). */
  secondaryCta?: CtaBandAction;
  /** Tiny reassurance line under the buttons (OpenPhone «Без карты • Отмена в любой момент»). */
  note?: React.ReactNode;
  /**
   * Visual weight:
   * - `brand` (default) — a full-bleed SATURATED brand-gradient panel with inverted
   *   text, the conversion climax of the page (OpenTable / OpenPhone / Tripadvisor).
   * - `soft` — a lighter brand-tinted card; use when the hero is already saturated so
   *   the page doesn't carry two heavy bands.
   */
  tone?: "brand" | "soft";
  /** Header + actions alignment. Default `center` (the canonical closing-CTA layout). */
  align?: "left" | "center";
  className?: string;
}

/**
 * The public storefront's closing call-to-action band — the conversion climax that
 * sits near the FOOT of `src/app/page.tsx`, just above the footer, the way enterprise
 * marketing pages end (Mobbin: OpenTable, OpenPhone, Blue Apron «Hungry for more?»,
 * Tripadvisor «Ready to tell your story?»): a full-bleed saturated brand panel, one
 * bold closing headline, a single supporting line and one prominent high-contrast CTA.
 *
 * It is the ONLY full-bleed inverted (on-brand) section in the kit — a genuinely
 * distinct premium pattern from the light content sections above it, so it reads as a
 * deliberate climax rather than another card grid. Brand-aware out of the box: the
 * panel and accents come from the palette tokens (`--primary` / `--accent`), so the
 * same band reads as a different brand per niche with no per-app styling.
 *
 * Drop it directly on the public «/» page (NOT inside a <StorefrontSection> — it owns
 * its own full-bleed band), after the last content section and before the footer.
 *
 *   <CtaBand
 *     eyebrow="Готовы начать?"
 *     title="Запишитесь на приём сегодня"
 *     subtitle="Свободные окна на этой неделе — выберите удобное время за минуту."
 *     primaryCta={{ label: "Записаться онлайн", href: "/signin" }}
 *     secondaryCta={{ label: "Услуги и цены", href: "#цены" }}
 *     note="Без предоплаты • Отмена в любой момент"
 *   />
 */
export function CtaBand({
  eyebrow,
  title,
  subtitle,
  primaryCta,
  secondaryCta,
  note,
  tone = "brand",
  align = "center",
  className,
}: CtaBandProps) {
  const brand = tone === "brand";
  const centered = align === "center";

  return (
    <section className={cn("px-6 py-16 sm:py-20", className)}>
      <div
        className={cn(
          "relative isolate mx-auto w-full max-w-6xl overflow-hidden rounded-[2rem] px-8 py-16 sm:px-14 sm:py-20",
          brand
            ? "bg-gradient-to-br from-primary to-accent text-primary-foreground"
            : "border border-border bg-muted/50 text-foreground",
        )}
      >
        {/* Brand-band depth — two soft inner glows so the panel never reads flat. */}
        {brand ? (
          <div aria-hidden className="pointer-events-none absolute inset-0 -z-10">
            <div className="absolute -right-24 -top-28 size-[26rem] rounded-full bg-primary-foreground/10 blur-[90px]" />
            <div className="absolute -bottom-32 -left-20 size-[22rem] rounded-full bg-primary-foreground/10 blur-[100px]" />
          </div>
        ) : null}

        <div
          className={cn(
            "stagger flex flex-col",
            centered ? "items-center text-center" : "items-start text-left",
          )}
        >
          {eyebrow ? (
            <p
              className={cn(
                "text-sm font-semibold uppercase tracking-[0.18em]",
                brand ? "text-primary-foreground/75" : "text-primary",
              )}
            >
              {eyebrow}
            </p>
          ) : null}

          <h2
            className={cn(
              "mt-3 max-w-2xl text-pretty text-3xl font-semibold leading-tight tracking-tight sm:text-4xl lg:text-5xl",
              centered && "mx-auto text-balance",
            )}
          >
            {title}
          </h2>

          {subtitle ? (
            <p
              className={cn(
                "mt-5 max-w-xl text-lg leading-relaxed",
                brand ? "text-primary-foreground/85" : "text-muted-foreground",
                centered && "mx-auto",
              )}
            >
              {subtitle}
            </p>
          ) : null}

          {primaryCta || secondaryCta ? (
            <div
              className={cn(
                "mt-9 flex flex-wrap gap-3",
                centered && "justify-center",
              )}
            >
              {primaryCta ? (
                <Button
                  asChild
                  size="lg"
                  className={cn(
                    "h-12 px-7 text-base",
                    // High-contrast light pill on the saturated band (Tripadvisor /
                    // OpenTable closing CTA); default brand button on the soft tone.
                    brand &&
                      "bg-background text-foreground shadow-sm hover:bg-background/90",
                  )}
                >
                  <a href={primaryCta.href}>{primaryCta.label}</a>
                </Button>
              ) : null}
              {secondaryCta ? (
                <Button
                  asChild
                  size="lg"
                  variant="outline"
                  className={cn(
                    "h-12 px-7 text-base",
                    brand &&
                      "border-primary-foreground/35 bg-transparent text-primary-foreground hover:bg-primary-foreground/10 hover:text-primary-foreground",
                  )}
                >
                  <a href={secondaryCta.href}>{secondaryCta.label}</a>
                </Button>
              ) : null}
            </div>
          ) : null}

          {note ? (
            <p
              className={cn(
                "mt-5 text-sm",
                brand ? "text-primary-foreground/70" : "text-muted-foreground",
              )}
            >
              {note}
            </p>
          ) : null}
        </div>
      </div>
    </section>
  );
}
