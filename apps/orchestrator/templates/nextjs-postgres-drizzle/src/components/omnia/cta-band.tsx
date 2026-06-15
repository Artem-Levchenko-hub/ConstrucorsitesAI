import * as React from "react";

/** Tiny class joiner — the drizzle kit ships no `cn`/`clsx`, so each
 *  self-contained piece joins its own conditional classes. */
function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

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
  /** The dominant action. On the brand band it renders as a high-contrast pill. */
  primaryCta?: CtaBandAction;
  /** Optional second, quieter action (ghost outline that reads on the band). */
  secondaryCta?: CtaBandAction;
  /** Tiny reassurance line under the buttons (OpenPhone «Без карты • Отмена в любой момент»). */
  note?: React.ReactNode;
  /**
   * Visual weight:
   * - `brand` (default) — a full-bleed SATURATED brand panel with inverted text,
   *   the conversion climax of the page (OpenTable / OpenPhone / Tripadvisor).
   * - `soft` — a lighter brand-tinted card; use when the hero is already saturated so
   *   the page doesn't carry two heavy bands.
   */
  tone?: "brand" | "soft";
  /** Header + actions alignment. Default `center` (the canonical closing-CTA layout). */
  align?: "left" | "center";
  className?: string;
}

/**
 * `<CtaBand>` — the public storefront's closing call-to-action band for the
 * fullstack (drizzle) template: the conversion climax that sits near the FOOT of
 * `src/app/page.tsx`, just above the footer, the way enterprise marketing pages
 * end (Mobbin: OpenTable, OpenPhone, Blue Apron «Hungry for more?»): a full-bleed
 * saturated brand panel, one bold closing headline, a single supporting line and
 * one prominent high-contrast CTA.
 *
 * It is the ONLY full-bleed inverted (on-brand) section in the kit — a genuinely
 * distinct premium pattern from the dark content sections above it, so it reads as
 * a deliberate climax rather than another card. Brand-aware out of the box: the
 * panel and the high-contrast pill come from the `--brand` / `--brand-fg` tokens,
 * so the same band reads as a different brand per niche. Self-contained — no shadcn.
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
    <section className={cx("px-6 py-16 sm:py-20", className)}>
      <div
        className={cx(
          "relative isolate mx-auto w-full max-w-6xl overflow-hidden rounded-[2rem] px-8 py-16 sm:px-14 sm:py-20",
          brand
            ? "bg-gradient-to-br from-[var(--brand)] to-[color-mix(in_oklab,var(--brand),black_22%)] text-[var(--brand-fg)]"
            : "border border-white/10 bg-white/[0.03] text-white backdrop-blur-sm",
        )}
      >
        {/* Brand-band depth — two soft inner glows so the panel never reads flat. */}
        {brand ? (
          <div aria-hidden className="pointer-events-none absolute inset-0 -z-10">
            <div className="absolute -right-24 -top-28 size-[26rem] rounded-full bg-[var(--brand-fg)] opacity-10 blur-[90px]" />
            <div className="absolute -bottom-32 -left-20 size-[22rem] rounded-full bg-[var(--brand-fg)] opacity-10 blur-[100px]" />
          </div>
        ) : null}

        <div
          className={cx(
            "flex flex-col",
            centered ? "items-center text-center" : "items-start text-left",
          )}
        >
          {eyebrow ? (
            <p
              className={cx(
                "text-sm font-semibold uppercase tracking-[0.18em]",
                brand ? "opacity-75" : "text-[var(--brand)]",
              )}
            >
              {eyebrow}
            </p>
          ) : null}

          <h2
            className={cx(
              "omnia-display mt-3 max-w-2xl text-pretty text-3xl font-semibold leading-tight tracking-tight sm:text-4xl lg:text-5xl",
              centered && "mx-auto text-balance",
            )}
          >
            {title}
          </h2>

          {subtitle ? (
            <p
              className={cx(
                "mt-5 max-w-xl text-lg leading-relaxed",
                brand ? "opacity-90" : "text-zinc-400",
                centered && "mx-auto",
              )}
            >
              {subtitle}
            </p>
          ) : null}

          {primaryCta || secondaryCta ? (
            <div
              className={cx(
                "mt-9 flex flex-wrap gap-3",
                centered && "justify-center",
              )}
            >
              {primaryCta ? (
                <a
                  href={primaryCta.href}
                  className={cx(
                    "inline-flex h-12 items-center justify-center rounded-xl px-7 text-base font-semibold shadow-lg transition hover:brightness-110",
                    // High-contrast pill on the saturated band (Tripadvisor /
                    // OpenTable closing CTA): the brand foreground becomes the pill,
                    // the brand becomes the text — adaptively legible per accent.
                    brand
                      ? "bg-[var(--brand-fg)] text-[var(--brand)]"
                      : "bg-[var(--brand)] text-[var(--brand-fg)] shadow-[color-mix(in_oklab,var(--brand),transparent_70%)]",
                  )}
                >
                  {primaryCta.label}
                </a>
              ) : null}
              {secondaryCta ? (
                <a
                  href={secondaryCta.href}
                  className={cx(
                    "inline-flex h-12 items-center justify-center rounded-xl px-7 text-base font-semibold transition",
                    brand
                      ? "border border-[color-mix(in_oklab,var(--brand-fg),transparent_65%)] text-[var(--brand-fg)] hover:bg-[color-mix(in_oklab,var(--brand-fg),transparent_88%)]"
                      : "border border-white/15 bg-white/5 text-zinc-100 hover:bg-white/10",
                  )}
                >
                  {secondaryCta.label}
                </a>
              ) : null}
            </div>
          ) : null}

          {note ? (
            <p
              className={cx(
                "mt-5 text-sm",
                brand ? "opacity-70" : "text-zinc-400",
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
