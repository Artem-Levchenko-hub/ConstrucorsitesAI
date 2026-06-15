/**
 * `<DashboardHero>` — the confident, brand-aurora anchor the cabinet opens with
 * (Linear, Vercel, Stripe Dashboard), not a flat heading on a bare canvas. It
 * gives the first screen depth: a signature `.omnia-hero-art` aurora painted in
 * the project accent behind the greeting, a big tracked title, an optional
 * dominant focal metric, and a quiet strip of supporting stats.
 *
 * Use it at the TOP of a cabinet screen in place of <PageHeader> — it renders
 * the page's single <h1>, so never use both (one h1 per screen). Then follow
 * with the secondary <StatCard> grid and the records <DataTable>.
 *
 * Pure and server-component-safe (no hooks): query with Drizzle, pass the
 * numbers down. Self-contained on Tailwind + lucide + the `--brand` token
 * (pinned from `share.accent` by `brandTokens`) — exactly like the rest of the
 * drizzle `omnia/` cabinet kit, so it costs zero per-app model budget and
 * matches the default landing and auth chrome. `metric`/`metricLabel` are
 * optional: omit them for an honest fresh-project greeting band, pass them once
 * there is a real focal number (e.g. revenue) to show large.
 *
 *   <DashboardHero
 *     eyebrow="Обзор" title="Дашборд"
 *     metricLabel="Выручка за месяц" metric="128 400 ₽"
 *     trend={{ value: "+12%", positive: true }}
 *     stats={[
 *       { label: "Клиентов", value: 24 },
 *       { label: "Открытых сделок", value: 6 },
 *     ]}
 *   />
 */
import * as React from "react";
import { TrendingDown, TrendingUp } from "lucide-react";

export interface HeroStat {
  label: React.ReactNode;
  value: React.ReactNode;
  /** Optional lucide icon shown muted before the value. */
  icon?: React.ReactNode;
}

export interface DashboardHeroProps {
  /** Small uppercase context label above the title, e.g. «Обзор» or a greeting. */
  eyebrow?: React.ReactNode;
  /** The screen title. Rendered as the page's single <h1>, so use <DashboardHero>
   *  INSTEAD of <PageHeader> on the dashboard — never both. */
  title: React.ReactNode;
  /** One line of context under the title. */
  description?: React.ReactNode;
  /** Label for the dominant metric, e.g. «Выручка за месяц». Optional. */
  metricLabel?: React.ReactNode;
  /** The dominant metric — the screen's one focal number, set large. Optional
   *  (omit for an honest fresh-project greeting band). */
  metric?: React.ReactNode;
  /** Delta pill next to the metric, e.g. `{ value: "+12%", positive: true }`. */
  trend?: { value: React.ReactNode; positive?: boolean };
  /** Up to ~3 supporting stats shown as a quiet strip beneath the band. Keep the
   *  emphasis on the single dominant metric — these stay muted. */
  stats?: HeroStat[];
  /** Right-aligned controls in the header row (e.g. a period switch or «Создать»). */
  actions?: React.ReactNode;
  className?: string;
}

function TrendPill({ value, positive }: { value: React.ReactNode; positive?: boolean }) {
  const down = positive === false;
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${
        down ? "bg-destructive/10 text-destructive" : "bg-success/10 text-success"
      }`}
    >
      {down ? <TrendingDown className="size-3.5" /> : <TrendingUp className="size-3.5" />}
      {value}
    </span>
  );
}

export function DashboardHero({
  eyebrow,
  title,
  description,
  metricLabel,
  metric,
  trend,
  stats,
  actions,
  className,
}: DashboardHeroProps) {
  const supporting = (stats ?? []).slice(0, 3);
  return (
    <section
      className={`fade-up elev-1 relative mb-8 overflow-hidden rounded-2xl border border-border bg-card ${
        className ?? ""
      }`}
    >
      {/* Signature surface: a brand-tinted aurora mesh for real depth — the kit's
          "surface zero" (tone-on-tone, contrast-safe), not a flat single glow. */}
      <div aria-hidden className="omnia-hero-art" />
      <div className="relative p-6 sm:p-8">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0 space-y-1.5">
            {eyebrow ? (
              <p className="flex items-center gap-2 text-xs font-semibold uppercase tracking-widest text-[var(--brand)]">
                <span
                  aria-hidden
                  className="size-1.5 rounded-full bg-[var(--brand)] shadow-[0_0_10px_var(--brand)]"
                />
                {eyebrow}
              </p>
            ) : null}
            <h1 className="omnia-display text-balance text-2xl font-semibold leading-tight text-foreground sm:text-3xl">
              {title}
            </h1>
            {description ? (
              <p className="max-w-2xl text-pretty text-sm leading-relaxed text-muted-foreground">
                {description}
              </p>
            ) : null}
          </div>
          {actions ? (
            <div className="flex shrink-0 items-center gap-2">{actions}</div>
          ) : null}
        </div>

        {metric ? (
          <div className="mt-6 min-w-0">
            {metricLabel ? (
              <p className="text-sm font-medium text-muted-foreground">{metricLabel}</p>
            ) : null}
            <div className="mt-1 flex flex-wrap items-baseline gap-x-3 gap-y-1">
              <span className="omnia-metric text-4xl font-semibold tabular-nums text-foreground sm:text-5xl">
                {metric}
              </span>
              {trend ? <TrendPill {...trend} /> : null}
            </div>
          </div>
        ) : null}

        {supporting.length ? (
          <div className="mt-6 flex flex-wrap gap-x-8 gap-y-3 border-t border-border pt-5">
            {supporting.map((s, i) => (
              <div key={i} className="min-w-0">
                <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                  {s.label}
                </p>
                <p className="mt-0.5 flex items-center gap-1.5 text-lg font-semibold tracking-tight tabular-nums text-foreground [&_svg]:size-4 [&_svg]:text-muted-foreground">
                  {s.icon}
                  {s.value}
                </p>
              </div>
            ))}
          </div>
        ) : null}
      </div>
    </section>
  );
}
