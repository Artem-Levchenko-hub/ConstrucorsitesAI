import * as React from "react";

/** Tiny class joiner — the drizzle kit ships no `cn`/`clsx` (no shadcn token
 *  system), so each self-contained piece joins its own conditional classes. */
function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

export interface StorefrontCta {
  /** Button copy, e.g. «Записаться» / «Открыть каталог». */
  label: React.ReactNode;
  /** Where it goes — a real route (/signin, /signup, #прайс …), never a dead «#». */
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
  /** The dominant action. Rendered as a large primary brand button. */
  primaryCta?: StorefrontCta;
  /** Optional second, quieter action (outline). */
  secondaryCta?: StorefrontCta;
  /** Up to 3 brief-derived proof figures shown as a strip under the CTAs. */
  stats?: StorefrontStat[];
  /** Hero visual slot — drop an <img> (or any node) here. On `left` align it
   *  sits to the right on desktop; on `center` it sits as a wide frame below.
   *  Omit it and the hero is a confident type-first banner. */
  media?: React.ReactNode;
  /** Layout: `left` = split (text left, media right on desktop); `center` = centered
   *  type-first with the media as a wide frame below. Defaults to `left` when `media`
   *  is given, otherwise `center`. */
  align?: "left" | "center";
  className?: string;
}

/**
 * `<StorefrontHero>` — the public storefront's hero band, the full-bleed first
 * screen a visitor (or a shared-link stranger on `/p/<slug>`) lands on, the way
 * Linear / Stripe / Framer marketing pages open: one dominant offer set large, a
 * brand-accent aurora for depth, a clear primary CTA, and a quiet strip of proof
 * figures. It is the public counterpart of <DashboardHero> — use it at the TOP
 * of `src/app/page.tsx` (the public «/» route), NOT inside the cabinet.
 *
 * Self-contained on Tailwind + the `--brand` token (pinned from `share.accent`
 * by `brandTokens`) — exactly like the default landing and the rest of the
 * drizzle `omnia/` kit, so it reads as a different brand per niche the moment the
 * accent changes, with zero per-app model budget. Server-component-safe (no
 * hooks): pass copy + a real CTA href down.
 *
 *   <StorefrontHero
 *     badge="Запись онлайн"
 *     eyebrow="Стоматология в центре"
 *     title="Здоровая улыбка без страха и очередей"
 *     subtitle="Современная клиника с щадящим лечением и прозрачными ценами."
 *     primaryCta={{ label: "Записаться", href: "/signup" }}
 *     secondaryCta={{ label: "Смотреть услуги", href: "#услуги" }}
 *     stats={[
 *       { value: "8 лет", label: "на рынке" },
 *       { value: "4.9★", label: "средняя оценка" },
 *       { value: "12 000+", label: "пациентов" },
 *     ]}
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
    <div className={cx("flex min-w-0 flex-col", centered ? "items-center text-center" : "items-start text-left")}>
      {badge ? (
        <span className="fade-up mb-5 inline-flex items-center gap-1.5 rounded-full border border-white/10 bg-white/5 px-3 py-1 text-xs font-medium uppercase tracking-widest text-zinc-300 backdrop-blur-sm [&_svg]:size-3.5 [&_svg]:text-[var(--brand)]">
          <span aria-hidden className="size-1.5 rounded-full bg-[var(--brand)] shadow-[0_0_12px_var(--brand)]" />
          {badge}
        </span>
      ) : null}
      {eyebrow ? (
        <p className="fade-up delay-1 text-sm font-semibold uppercase tracking-[0.18em] text-[var(--brand)]">
          {eyebrow}
        </p>
      ) : null}
      <h1
        className={cx(
          "omnia-display fade-up delay-1 mt-3 max-w-2xl text-pretty text-4xl font-semibold leading-[1.05] tracking-tight text-white sm:text-5xl lg:text-6xl",
          centered && "mx-auto text-balance",
        )}
      >
        {title}
      </h1>
      {subtitle ? (
        <p className={cx("fade-up delay-2 mt-5 max-w-xl text-lg leading-relaxed text-zinc-400", centered && "mx-auto")}>
          {subtitle}
        </p>
      ) : null}

      {primaryCta || secondaryCta ? (
        <div className={cx("fade-up delay-3 mt-8 flex flex-wrap gap-3", centered && "justify-center")}>
          {primaryCta ? (
            <a
              href={primaryCta.href}
              className="hover-lift inline-flex h-12 items-center justify-center gap-2 rounded-xl bg-[var(--brand)] px-7 text-base font-semibold text-[var(--brand-fg)] shadow-lg shadow-[color-mix(in_oklab,var(--brand),transparent_70%)] transition hover:brightness-110"
            >
              {primaryCta.label}
            </a>
          ) : null}
          {secondaryCta ? (
            <a
              href={secondaryCta.href}
              className="inline-flex h-12 items-center justify-center gap-2 rounded-xl border border-white/15 bg-white/5 px-7 text-base font-semibold text-zinc-100 backdrop-blur-sm transition hover:bg-white/10"
            >
              {secondaryCta.label}
            </a>
          ) : null}
        </div>
      ) : null}

      {proof.length ? (
        <dl className={cx("fade-up delay-3 mt-12 flex flex-wrap gap-x-10 gap-y-5 border-t border-white/10 pt-7", centered && "justify-center")}>
          {proof.map((s, i) => (
            <div key={i} className={cx("min-w-0", centered && "text-center")}>
              <dt className="order-2 mt-1 text-sm text-zinc-500">{s.label}</dt>
              <dd className="omnia-metric order-1 text-3xl font-semibold tracking-tight tabular-nums text-white">
                {s.value}
              </dd>
            </div>
          ))}
        </dl>
      ) : null}
    </div>
  );

  const frame = media ? (
    <div className="fade-up delay-2 relative aspect-[4/3] w-full overflow-hidden rounded-3xl border border-white/10 bg-white/[0.03] shadow-2xl shadow-black/40 [&_img]:h-full [&_img]:w-full [&_img]:object-cover">
      {media}
    </div>
  ) : null;

  return (
    <section className={cx("relative isolate overflow-hidden border-b border-white/10", className)}>
      {/* Brand-accent aurora — same recipe as the default landing, palette-tied. */}
      <div aria-hidden className="pointer-events-none absolute inset-0 -z-10">
        <div className="absolute inset-0 bg-[radial-gradient(110%_80%_at_50%_-10%,color-mix(in_oklab,var(--brand),transparent_60%),transparent_60%)] opacity-70" />
        <div className="absolute -right-32 -top-40 size-[34rem] rounded-full bg-[var(--brand)] opacity-20 blur-[120px]" />
        <div className="absolute -bottom-40 -left-24 size-[26rem] rounded-full bg-[var(--brand)] opacity-10 blur-[140px]" />
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
