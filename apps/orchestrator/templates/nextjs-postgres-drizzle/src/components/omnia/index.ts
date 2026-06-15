/**
 * `@/components/omnia` — the premium, self-contained cabinet kit for the
 * fullstack (drizzle) template. Every piece is built from Tailwind + lucide and
 * wears the project colour through the `--brand` token (see `@/lib/brand`), so
 * a generated cabinet matches the default landing and auth chrome with zero
 * per-app model cost. Presentational components are server-component-safe —
 * query with Drizzle, pass rows/values down as props.
 *
 *   import { AppShell, PageHeader, StatCard, DataTable, EmptyState } from "@/components/omnia";
 *   import { StorefrontHero, StorefrontSection, FeatureCard } from "@/components/omnia";
 *   import { Sparkline, TrendArea, BarMini, DonutStat } from "@/components/omnia";
 */
export { AppShell } from "./app-shell";
export type { AppShellProps, AppShellUser, NavItem } from "./app-shell";
export { DashboardHero } from "./dashboard-hero";
export type { DashboardHeroProps, HeroStat } from "./dashboard-hero";
export { StorefrontHero } from "./storefront-hero";
export type { StorefrontHeroProps, StorefrontCta, StorefrontStat } from "./storefront-hero";
export { StorefrontSection, FeatureCard } from "./storefront-section";
export type { StorefrontSectionProps, FeatureCardProps } from "./storefront-section";
export { PageHeader } from "./page-header";
export type { PageHeaderProps } from "./page-header";
export { StatCard } from "./stat-card";
export type { StatCardProps } from "./stat-card";
export { EmptyState } from "./empty-state";
export type { EmptyStateProps } from "./empty-state";
export { DataTable } from "./data-table";
export type { Column, DataTableProps } from "./data-table";
export { Sparkline, TrendArea, BarMini, DonutStat } from "./charts";
export type {
  SparklineProps,
  TrendAreaProps,
  BarMiniProps,
  BarMiniDatum,
  DonutStatProps,
} from "./charts";
