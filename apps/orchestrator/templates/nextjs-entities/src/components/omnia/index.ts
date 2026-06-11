/**
 * Omnia app kit — data-aware components built on the shadcn/ui primitives in
 * `@/components/ui`. Import these for the app shell, dashboards and CRUD screens;
 * drop down to `@/components/ui/*` (or raw Tailwind) for anything bespoke.
 *
 *   import { AppShell, PageHeader, StatCard, CrudResource } from "@/components/omnia";
 */
export { AppShell, type NavItem, type AppShellUser, type AppShellProps } from "./app-shell";
export { PageHeader, type PageHeaderProps } from "./page-header";
export { StatCard, type StatCardProps } from "./stat-card";
export { CountUp, type CountUpProps } from "./count-up";
export { EmptyState, type EmptyStateProps } from "./empty-state";
export { DataTable, type Column, type DataTableProps, type FilterTab } from "./data-table";
export {
  EntityForm,
  type FieldSpec,
  type FieldKind,
  type EntityFormProps,
} from "./entity-form";
export { CrudResource, type CrudResourceProps } from "./crud-resource";
export { useEntity, type UseEntity } from "./use-entity";
export {
  Sparkline,
  type SparklineProps,
  TrendArea,
  type TrendAreaProps,
  BarMini,
  type BarMiniProps,
  type BarMiniDatum,
  DonutStat,
  type DonutStatProps,
} from "./charts";
