/**
 * Omnia app kit — data-aware components built on the shadcn/ui primitives in
 * `@/components/ui`. Import these for the app shell, dashboards and CRUD screens;
 * drop down to `@/components/ui/*` (or raw Tailwind) for anything bespoke.
 *
 *   import { AppShell, PageHeader, StatCard, CrudResource } from "@/components/omnia";
 */
export {
  AppShell,
  type NavItem,
  type AppShellUser,
  type AppShellProps,
  type PlanInfo,
} from "./app-shell";
export { PageHeader, type PageHeaderProps } from "./page-header";
export {
  DashboardHero,
  type DashboardHeroProps,
  type HeroStat,
} from "./dashboard-hero";
export { StatCard, type StatCardProps } from "./stat-card";
export { CountUp, type CountUpProps } from "./count-up";
export { EmptyState, type EmptyStateProps } from "./empty-state";
export { DataTable, type Column, type DataTableProps, type FilterTab } from "./data-table";
export {
  GalleryGrid,
  type GalleryGridProps,
  type GalleryItem,
  MediaCard,
  type MediaCardProps,
} from "./gallery-grid";
export {
  EntityForm,
  type FieldSpec,
  type FieldKind,
  type EntityFormProps,
} from "./entity-form";
export { CrudResource, type CrudResourceProps } from "./crud-resource";
export {
  RecordDetail,
  type RecordDetailProps,
  type DetailField,
} from "./record-detail";
export {
  SettingsShell,
  type SettingsShellProps,
  type SettingsNavItem,
  SettingsSection,
  type SettingsSectionProps,
  FieldRow,
  type FieldRowProps,
  FieldGrid,
  type FieldGridProps,
  DangerZone,
  type DangerZoneProps,
} from "./settings";
export {
  SetupChecklist,
  type SetupChecklistProps,
  type ChecklistStep,
  type ChecklistAction,
} from "./setup-checklist";
export {
  CommandPalette,
  useCommandPalette,
  type CommandItem,
  type CommandPaletteProps,
} from "./command-palette";
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
