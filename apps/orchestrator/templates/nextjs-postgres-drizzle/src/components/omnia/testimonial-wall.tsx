import * as React from "react";
import { Quote, Star } from "lucide-react";

/** Tiny class joiner — the drizzle kit ships no `cn`/`clsx`, so each
 *  self-contained piece joins its own conditional classes. */
function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

export interface Testimonial {
  /** The testimonial body — the card's hero. Keep it one honest paragraph. */
  quote: React.ReactNode;
  /** Who said it. */
  name: React.ReactNode;
  /** Their role / company, e.g. «Владелица, Кофейня "Утро"». */
  role?: React.ReactNode;
  /** Optional headshot. A `data-omnia-gen` <img> URL or a real URL; falls back to
   *  initials in a brand-tinted chip when absent (the common generated case). */
  avatar?: string;
  /** Star rating 1–5 (Whop / Trustpilot pattern). Omit to hide the stars. */
  rating?: number;
}

export interface TestimonialWallProps {
  /** The testimonials, in display order. */
  items: Testimonial[];
  className?: string;
}

const GRID_COLS: Record<number, string> = {
  1: "max-w-2xl mx-auto",
  2: "sm:grid-cols-2 max-w-4xl mx-auto",
  3: "sm:grid-cols-2 lg:grid-cols-3",
  4: "sm:grid-cols-2 lg:grid-cols-4",
};

/** First letters of up to two words — the initials fallback for a missing photo. */
function initials(name: React.ReactNode): string {
  if (typeof name !== "string") return "";
  return name
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase() ?? "")
    .join("");
}

function Stars({ rating }: { rating: number }) {
  const n = Math.max(0, Math.min(5, Math.round(rating)));
  return (
    <div className="flex items-center gap-0.5" aria-label={`Оценка ${n} из 5`}>
      {Array.from({ length: 5 }).map((_, i) => (
        <Star
          key={i}
          aria-hidden
          strokeWidth={0}
          className={cx(
            "size-4",
            i < n
              ? "fill-[var(--brand)] text-[var(--brand)]"
              : "fill-muted-foreground/25 text-muted-foreground/25",
          )}
        />
      ))}
    </div>
  );
}

function Avatar({ item }: { item: Testimonial }) {
  if (item.avatar) {
    return (
      // Plain <img>: no next/image remotePatterns config needed; renders for
      // inline data: tiles and real URLs alike (mirrors <FeatureCard>).
      // eslint-disable-next-line @next/next/no-img-element
      <img
        src={item.avatar}
        alt=""
        loading="lazy"
        className="size-11 shrink-0 rounded-full border border-border object-cover"
      />
    );
  }
  return (
    <span
      aria-hidden
      className="flex size-11 shrink-0 items-center justify-center rounded-full bg-[color-mix(in_oklab,var(--brand),transparent_80%)] text-sm font-semibold text-[var(--brand)]"
    >
      {initials(item.name) || <Quote className="size-4" />}
    </span>
  );
}

function TestimonialCard({ item }: { item: Testimonial }) {
  return (
    <figure className="hover-lift elev-1 flex h-full flex-col gap-5 rounded-2xl border border-border bg-card p-6 sm:p-7">
      <div className="flex items-center justify-between gap-3">
        <Quote
          aria-hidden
          strokeWidth={0}
          className="size-8 shrink-0 fill-[color-mix(in_oklab,var(--brand),transparent_82%)] text-[color-mix(in_oklab,var(--brand),transparent_82%)]"
        />
        {typeof item.rating === "number" ? <Stars rating={item.rating} /> : null}
      </div>

      <blockquote className="flex-1 text-pretty text-[15px] leading-relaxed text-foreground sm:text-base">
        {item.quote}
      </blockquote>

      <figcaption className="flex items-center gap-3 border-t border-border pt-5">
        <Avatar item={item} />
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold tracking-tight text-foreground">
            {item.name}
          </div>
          {item.role ? (
            <div className="truncate text-sm text-muted-foreground">{item.role}</div>
          ) : null}
        </div>
      </figcaption>
    </figure>
  );
}

/**
 * `<TestimonialWall>` — the public storefront's social-proof wall for the
 * fullstack (drizzle) template: a brand-aware grid of customer testimonials the
 * way enterprise marketing pages present them (Mobbin: Savee, Contra, Whop): a
 * quote-forward card with a decorative brand quote-mark, optional star rating,
 * and a footer pairing a headshot (or an initials chip when no photo) with the
 * person's name and role. Drop it inside a <StorefrontSection> (omit that
 * section's `columns` — this component owns its own responsive grid), and it
 * reads as a different brand per niche straight from the `--brand` token with
 * zero per-app styling. Self-contained — no shadcn.
 *
 *   <StorefrontSection id="отзывы" eyebrow="Отзывы" title="Нас рекомендуют"
 *     lead="Что говорят пациенты после лечения." align="center" tint>
 *     <TestimonialWall items={[
 *       { quote: "Записалась утром — приняли в тот же день. Лечение без боли, всё объяснили.",
 *         name: "Анна Котова", role: "Пациентка", rating: 5 },
 *       { quote: "Прозрачные цены и аккуратная работа. Веду сюда всю семью второй год.",
 *         name: "Игорь Лебедев", role: "Пациент", rating: 5 },
 *       { quote: "Детский стоматолог — золото. Ребёнок впервые пошёл к врачу без слёз.",
 *         name: "Мария Зайцева", role: "Мама пациента", rating: 5 },
 *     ]} />
 *   </StorefrontSection>
 */
export function TestimonialWall({ items, className }: TestimonialWallProps) {
  const cols = GRID_COLS[Math.min(items.length, 4)] ?? GRID_COLS[3];

  return (
    <div
      className={cx(
        "grid grid-cols-1 items-stretch gap-5",
        cols,
        className,
      )}
    >
      {items.map((item, i) => (
        <TestimonialCard key={i} item={item} />
      ))}
    </div>
  );
}
