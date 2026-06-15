"use client";

import * as React from "react";
import {
  ArrowDown,
  ArrowUp,
  ChevronsUpDown,
  Download,
  Rows3,
  Search,
  SlidersHorizontal,
  X,
} from "lucide-react";

import { cn } from "@/lib/utils";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { EmptyState } from "./empty-state";

export interface Column<T> {
  key: string;
  header: string;
  /** Custom cell. Without it the raw `row[key]` is shown (—/Да/Нет/text). */
  render?: (row: T) => React.ReactNode;
  sortable?: boolean;
  align?: "left" | "right" | "center";
  className?: string;
  headerClassName?: string;
}

/**
 * One segment of the quick-filter control shown above the table. Declarative on
 * purpose — the caller lists the values to filter by, the table owns the state,
 * matching and a11y. `value: null` is the "show everything" segment.
 */
export interface FilterTab {
  label: string;
  value: string | number | null;
}

export interface DataTableProps<T> {
  columns: Column<T>[];
  rows: T[];
  loading?: boolean;
  searchable?: boolean;
  /** Row keys searched by the search box. Defaults to every column key. */
  searchKeys?: string[];
  searchPlaceholder?: string;
  /** Row key the quick-filter segments match against (e.g. "status"). */
  filterField?: string;
  /** Quick-filter segments above the table. First is usually `{ label: "Все", value: null }`. */
  filterTabs?: FilterTab[];
  /** Set to enable client pagination. */
  pageSize?: number;
  /** Show a "Экспорт" button that downloads the filtered+sorted rows as CSV. */
  exportable?: boolean;
  /** File name for the CSV export (defaults to "export.csv"). */
  exportFilename?: string;
  /**
   * Show a "Колонки" menu that lets the user hide/show individual columns.
   * Only appears once there are enough columns to be worth it (≥3).
   */
  columnToggle?: boolean;
  /** Show a leading checkbox column so rows can be selected for bulk actions. */
  selectable?: boolean;
  /**
   * Show a density toggle (comfortable ⇄ compact rows). Only appears once the
   * list is long enough (≥10 rows) to be worth tightening; short lists are
   * unaffected and render exactly as before.
   */
  densityToggle?: boolean;
  /**
   * Controls shown in the selection toolbar when ≥1 row is selected. Receives
   * the selected rows (full objects, pruned to those still present) and a
   * `clear` callback to reset the selection after acting.
   */
  bulkActions?: (selected: T[], clear: () => void) => React.ReactNode;
  /** Right-aligned per-row controls (edit / delete …) rendered in a last column. */
  rowActions?: (row: T) => React.ReactNode;
  onRowClick?: (row: T) => void;
  empty?: React.ReactNode;
  /**
   * Primary call-to-action shown inside the *first-run* empty state (no records
   * at all) — usually a "create the first record" button. It is NOT shown on a
   * no-match empty state (records exist but a search/filter hid them), where the
   * active-filter chips already offer "Сбросить всё". `empty` overrides this.
   */
  emptyAction?: React.ReactNode;
  /** Extra controls shown on the toolbar row, left of the search box. */
  toolbar?: React.ReactNode;
  className?: string;
}

type SortState = { key: string; dir: "asc" | "desc" } | null;

/**
 * When the caller forgets `pageSize` (common — the model doesn't always set it),
 * a list with real production volume would dump every row into the DOM, which
 * reads as broken rather than enterprise. Past this many rows we fall back to
 * paginating so the table never does that. Kept generous so seeded demo tables
 * (usually a handful of rows) still render in one page, unchanged.
 */
const AUTO_PAGE_SIZE = 20;

/**
 * Hiding columns only earns its toolbar button once a table is wide enough to
 * be worth tidying. Below this many columns the toggle stays hidden, so small
 * tables render exactly as before even with `columnToggle` on.
 */
const COLUMN_TOGGLE_MIN = 3;

/**
 * Tightening row height only matters once a list has some volume. Below this
 * many rows the density toggle stays hidden, so small tables render exactly as
 * before even with `densityToggle` on.
 */
const DENSITY_TOGGLE_MIN = 10;

function rawValue(row: Record<string, unknown>, key: string): unknown {
  return row[key];
}

function compare(a: unknown, b: unknown): number {
  if (a == null) return b == null ? 0 : -1;
  if (b == null) return 1;
  if (typeof a === "number" && typeof b === "number") return a - b;
  return String(a).localeCompare(String(b), "ru", { numeric: true });
}

function defaultCell(value: unknown): React.ReactNode {
  if (value == null || value === "") return <span className="text-muted-foreground">—</span>;
  if (typeof value === "boolean") return value ? "Да" : "Нет";
  return String(value);
}

const alignClass = { left: "text-left", right: "text-right", center: "text-center" } as const;

/** UTF-8 BOM so Excel detects the encoding and renders Cyrillic correctly. */
const BOM = String.fromCharCode(0xfeff);

/** Plain-text value for a CSV cell (no JSX) — mirrors defaultCell's wording. */
function cellText(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "boolean") return value ? "Да" : "Нет";
  return String(value);
}

/** RFC-4180 field escaping: quote when the value holds a comma, quote or newline. */
function csvField(text: string): string {
  return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

/**
 * Build a CSV string from the visible columns and the given rows. CRLF line
 * endings keep Excel happy; the caller prepends a UTF-8 BOM so Cyrillic opens
 * correctly. The action column is intentionally skipped — it has no data value.
 */
function rowsToCsv<T>(columns: Column<T>[], rows: T[]): string {
  const header = columns.map((c) => csvField(c.header)).join(",");
  const body = rows.map((row) =>
    columns
      .map((c) => csvField(cellText(rawValue(row as Record<string, unknown>, c.key))))
      .join(","),
  );
  return [header, ...body].join("\r\n");
}

/**
 * Presentational data table with client-side search, sort and pagination, plus
 * loading skeletons and an empty state. Pass already-loaded `rows`; for a fully
 * wired entity screen use <CrudResource>, which renders this for you.
 */
export function DataTable<T extends { id: string }>({
  columns,
  rows,
  loading,
  searchable,
  searchKeys,
  searchPlaceholder = "Поиск…",
  filterField,
  filterTabs,
  pageSize,
  exportable,
  exportFilename,
  columnToggle,
  selectable,
  densityToggle,
  bulkActions,
  rowActions,
  onRowClick,
  empty,
  emptyAction,
  toolbar,
  className,
}: DataTableProps<T>) {
  const [query, setQuery] = React.useState("");
  const [sort, setSort] = React.useState<SortState>(null);
  const [page, setPage] = React.useState(1);
  const [tabIdx, setTabIdx] = React.useState(0);
  const [selected, setSelected] = React.useState<Set<string>>(() => new Set());
  const [hidden, setHidden] = React.useState<Set<string>>(() => new Set());
  const [density, setDensity] = React.useState<"comfortable" | "compact">("comfortable");

  const keys = searchKeys ?? columns.map((c) => c.key);
  const tabs = filterField ? filterTabs : undefined;

  // Gate on the raw row count (not the filtered total) so the control's
  // presence stays stable while the operator searches/filters.
  const showDensityToggle =
    !!densityToggle && (Array.isArray(rows) ? rows.length : 0) >= DENSITY_TOGGLE_MIN;

  // The toggle only shows for wide-enough tables; below the threshold `hidden`
  // can never change, so `visibleColumns` stays identical to `columns`.
  const showColumnToggle = !!columnToggle && columns.length >= COLUMN_TOGGLE_MIN;
  const visibleColumns = React.useMemo(
    () => (showColumnToggle ? columns.filter((c) => !hidden.has(c.key)) : columns),
    [showColumnToggle, columns, hidden],
  );

  function toggleColumn(key: string) {
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  const segmented = React.useMemo(() => {
    // Defensive: callers often start with `useState()` (undefined) before data
    // loads and pass it straight in — never let that crash the table.
    const all = Array.isArray(rows) ? rows : [];
    const tab = tabs?.[tabIdx];
    if (!tab || tab.value == null || !filterField) return all;
    const want = String(tab.value);
    return all.filter(
      (row) => String(rawValue(row as Record<string, unknown>, filterField) ?? "") === want,
    );
  }, [rows, tabs, tabIdx, filterField]);

  const filtered = React.useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return segmented;
    return segmented.filter((row) =>
      keys.some((k) => {
        const v = rawValue(row as Record<string, unknown>, k);
        return v != null && String(v).toLowerCase().includes(q);
      }),
    );
  }, [segmented, query, keys]);

  const sorted = React.useMemo(() => {
    if (!sort) return filtered;
    const out = [...filtered];
    out.sort((a, b) => {
      const r = compare(
        rawValue(a as Record<string, unknown>, sort.key),
        rawValue(b as Record<string, unknown>, sort.key),
      );
      return sort.dir === "asc" ? r : -r;
    });
    return out;
  }, [filtered, sort]);

  const total = sorted.length;
  // Honour an explicit pageSize; otherwise auto-paginate only once a table is
  // genuinely long, so short lists stay on a single page (R-10 fail-safe).
  const effectivePageSize = pageSize ?? (total > AUTO_PAGE_SIZE ? AUTO_PAGE_SIZE : undefined);
  const pages = effectivePageSize ? Math.max(1, Math.ceil(total / effectivePageSize)) : 1;
  const current = Math.min(page, pages);
  const visible = effectivePageSize
    ? sorted.slice((current - 1) * effectivePageSize, current * effectivePageSize)
    : sorted;

  // Distinguish a true first-run empty (no records at all → invite the first
  // one) from a no-match empty (records exist but a search/filter hid them →
  // the active-filter chips already offer a reset). Gate on the raw count, not
  // `query`, so an empty-result filter tab is also classified correctly.
  const rawRowCount = Array.isArray(rows) ? rows.length : 0;
  const noRecords = rawRowCount === 0;

  // Selection is keyed by id so it survives pagination/filter/sort. Derive the
  // selected rows from the current filtered set so it self-prunes to rows that
  // are still present (deleted or filtered-out ids drop out silently).
  const selectedRows = React.useMemo(
    () => (selectable ? sorted.filter((row) => selected.has(row.id)) : []),
    [selectable, sorted, selected],
  );
  const allSelected = sorted.length > 0 && selectedRows.length === sorted.length;
  const someSelected = selectedRows.length > 0 && !allSelected;

  function clearSelection() {
    setSelected(new Set());
  }
  function toggleRow(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }
  function toggleAll() {
    setSelected(allSelected ? new Set() : new Set(sorted.map((r) => r.id)));
  }

  React.useEffect(() => {
    setPage(1);
  }, [query, sort, tabIdx]);

  function toggleSort(key: string) {
    setSort((s) =>
      s?.key === key
        ? s.dir === "asc"
          ? { key, dir: "desc" }
          : null
        : { key, dir: "asc" },
    );
  }

  function handleExport() {
    // Export the selected rows when there is a selection, otherwise everything
    // that matches the current filter/search/sort — not just the visible page.
    const exportRows = selectedRows.length ? selectedRows : sorted;
    // Export mirrors what's on screen — hidden columns are left out.
    const csv = BOM + rowsToCsv(visibleColumns, exportRows);
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = exportFilename ?? "export.csv";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }

  // Surface the filters that are actually narrowing the table as removable
  // chips. Derived from the state the table already owns (search + active
  // quick-filter tab) — no new props, so every searchable/filterable table
  // gets them for free, and a table with nothing applied renders no bar at all.
  // The "show everything" tab (value == null) is not a filter, so it gets no chip.
  const activeTab = tabs?.[tabIdx];
  const trimmedQuery = query.trim();
  const activeFilters: { key: string; label: string; clear: () => void }[] = [];
  if (trimmedQuery) {
    activeFilters.push({
      key: "q",
      label: `Поиск: «${trimmedQuery}»`,
      clear: () => setQuery(""),
    });
  }
  if (activeTab && activeTab.value != null) {
    activeFilters.push({
      key: "tab",
      label: activeTab.label,
      clear: () => setTabIdx(0),
    });
  }
  function clearAllFilters() {
    setQuery("");
    setTabIdx(0);
  }

  const colCount = visibleColumns.length + (selectable ? 1 : 0) + (rowActions ? 1 : 0);

  return (
    // `data-omnia-collection` + `data-omnia-rows` are the stable signal the
    // acceptance gauntlet's data gate (V1.6 5/5) keys on to prove the demo
    // seeder filled the catalog — the RAW record count (filter/pagination
    // independent), so a search box hiding rows never reads as an empty catalog.
    <div
      className={cn("space-y-3", className)}
      data-omnia-collection=""
      data-omnia-rows={rawRowCount}
    >
      {(searchable ||
        toolbar ||
        exportable ||
        showColumnToggle ||
        showDensityToggle ||
        (tabs && tabs.length > 1)) && (
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex flex-wrap items-center gap-2">
            {tabs && tabs.length > 1 ? (
              <div
                role="group"
                aria-label="Быстрый фильтр"
                className="inline-flex flex-wrap items-center gap-1 rounded-lg bg-muted p-1"
              >
                {tabs.map((tab, i) => {
                  const active = i === tabIdx;
                  return (
                    <button
                      key={`${tab.label}-${i}`}
                      type="button"
                      aria-pressed={active}
                      onClick={() => setTabIdx(i)}
                      className={cn(
                        "rounded-md px-3 py-1.5 text-sm font-medium outline-none transition-colors focus-visible:ring-[3px] focus-visible:ring-ring/40",
                        active
                          ? "bg-background text-foreground shadow-sm"
                          : "text-muted-foreground hover:text-foreground",
                      )}
                    >
                      {tab.label}
                    </button>
                  );
                })}
              </div>
            ) : null}
            {toolbar}
          </div>
          {(searchable || exportable || showColumnToggle || showDensityToggle) && (
            <div className="flex items-center gap-2">
              {showDensityToggle ? (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  aria-pressed={density === "compact"}
                  onClick={() =>
                    setDensity((d) => (d === "compact" ? "comfortable" : "compact"))
                  }
                  aria-label={
                    density === "compact" ? "Обычная плотность строк" : "Компактные строки"
                  }
                >
                  <Rows3 className="size-4" />
                  <span className="hidden sm:inline">
                    {density === "compact" ? "Обычно" : "Компактно"}
                  </span>
                </Button>
              ) : null}
              {showColumnToggle ? (
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      aria-label="Показать или скрыть колонки"
                    >
                      <SlidersHorizontal className="size-4" />
                      <span className="hidden sm:inline">Колонки</span>
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end" className="w-48">
                    <DropdownMenuLabel>Показать колонки</DropdownMenuLabel>
                    <DropdownMenuSeparator />
                    {columns.map((col) => {
                      const isVisible = !hidden.has(col.key);
                      return (
                        <DropdownMenuCheckboxItem
                          key={col.key}
                          checked={isVisible}
                          // Keep the menu open so several columns can be toggled.
                          onSelect={(e) => e.preventDefault()}
                          onCheckedChange={() => toggleColumn(col.key)}
                          // Never let the user hide the last visible column.
                          disabled={isVisible && visibleColumns.length === 1}
                        >
                          {col.header}
                        </DropdownMenuCheckboxItem>
                      );
                    })}
                  </DropdownMenuContent>
                </DropdownMenu>
              ) : null}
              {exportable ? (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={handleExport}
                  disabled={total === 0}
                  aria-label="Экспортировать в CSV"
                >
                  <Download className="size-4" />
                  <span className="hidden sm:inline">Экспорт</span>
                </Button>
              ) : null}
              {searchable ? (
                <div className="relative flex-1 sm:w-64 sm:flex-none">
                  <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                  <Input
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder={searchPlaceholder}
                    className="pl-9"
                  />
                </div>
              ) : null}
            </div>
          )}
        </div>
      )}

      {activeFilters.length > 0 ? (
        <div
          role="group"
          aria-label="Активные фильтры"
          className="flex flex-wrap items-center gap-2"
        >
          {activeFilters.map((f) => (
            <button
              key={f.key}
              type="button"
              onClick={f.clear}
              aria-label={`Убрать фильтр «${f.label}»`}
              className="inline-flex min-h-8 items-center gap-1.5 rounded-full border border-border bg-muted/60 py-1 pl-3 pr-2 text-sm font-medium text-foreground outline-none transition-colors hover:bg-muted focus-visible:ring-[3px] focus-visible:ring-ring/40"
            >
              <span className="max-w-[16rem] truncate">{f.label}</span>
              <X className="size-3.5 shrink-0 opacity-70" />
            </button>
          ))}
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={clearAllFilters}
            className="h-8 text-muted-foreground"
          >
            Сбросить всё
          </Button>
        </div>
      ) : null}

      {selectable && selectedRows.length > 0 ? (
        <div
          role="region"
          aria-label="Действия с выбранными"
          className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border bg-muted/50 px-3 py-2"
        >
          <span className="text-sm font-medium" aria-live="polite">
            Выбрано: {selectedRows.length}
          </span>
          <div className="flex items-center gap-2">
            {bulkActions?.(selectedRows, clearSelection)}
            <Button type="button" variant="ghost" size="sm" onClick={clearSelection}>
              Снять выбор
            </Button>
          </div>
        </div>
      ) : null}

      <div
        className={cn(
          "elev-1 overflow-hidden rounded-xl border border-border bg-card",
          density === "compact" && "[&_td]:py-1.5 [&_th]:h-9",
        )}
      >
        <Table containerClassName="max-h-[70vh]">
          <TableHeader className="[&_th]:sticky [&_th]:top-0 [&_th]:z-10 [&_th]:bg-muted">
            <TableRow>
              {selectable ? (
                <TableHead className="w-0">
                  <Checkbox
                    checked={allSelected ? true : someSelected ? "indeterminate" : false}
                    onCheckedChange={toggleAll}
                    aria-label="Выбрать все"
                    disabled={sorted.length === 0}
                  />
                </TableHead>
              ) : null}
              {visibleColumns.map((col) => {
                const sortedAsc = sort?.key === col.key && sort.dir === "asc";
                const sortedDesc = sort?.key === col.key && sort.dir === "desc";
                return (
                  <TableHead
                    key={col.key}
                    aria-sort={
                      col.sortable
                        ? sortedAsc
                          ? "ascending"
                          : sortedDesc
                            ? "descending"
                            : "none"
                        : undefined
                    }
                    className={cn(col.align && alignClass[col.align], col.headerClassName)}
                  >
                    {col.sortable ? (
                      <button
                        type="button"
                        onClick={() => toggleSort(col.key)}
                        aria-label={`Сортировать по «${col.header}»${
                          sortedAsc ? ", по возрастанию" : sortedDesc ? ", по убыванию" : ""
                        }`}
                        className="-mx-2 inline-flex min-h-9 items-center gap-1 rounded-md px-2 outline-none transition-colors hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/40"
                      >
                        {col.header}
                        {sort?.key === col.key ? (
                          sort.dir === "asc" ? (
                            <ArrowUp className="size-3.5" />
                          ) : (
                            <ArrowDown className="size-3.5" />
                          )
                        ) : (
                          <ChevronsUpDown className="size-3.5 opacity-50" />
                        )}
                      </button>
                    ) : (
                      col.header
                    )}
                  </TableHead>
                );
              })}
              {rowActions ? <TableHead className="w-0 text-right">Действия</TableHead> : null}
            </TableRow>
          </TableHeader>
          {/* `stagger` gives the most-shipped surface the same born-cascade the
           * board/gallery views already have — rows rise in sequence on mount,
           * obeying the per-app `--omnia-ease`/`--omnia-dur` MOTION-DNA. Plays on
           * mount (and when a fresh page of rows keys in); reduced-motion settles
           * instantly. Pillar-3 hypnosis on the default table view. */}
          <TableBody className="stagger">
            {loading ? (
              Array.from({ length: effectivePageSize ?? 5 }).map((_, i) => (
                <TableRow key={`s-${i}`}>
                  {Array.from({ length: colCount }).map((__, j) => (
                    <TableCell key={j}>
                      <Skeleton className="h-5 w-full max-w-32" />
                    </TableCell>
                  ))}
                </TableRow>
              ))
            ) : visible.length === 0 ? (
              <TableRow className="hover:bg-transparent">
                <TableCell colSpan={colCount} className="p-0">
                  {empty ?? (
                    <EmptyState
                      className="rounded-none border-0"
                      title={noRecords ? "Пока пусто" : "Ничего не найдено"}
                      description={
                        noRecords
                          ? "Здесь появятся записи, как только вы их добавите."
                          : "Измените запрос или сбросьте фильтр."
                      }
                      action={noRecords ? emptyAction : undefined}
                    />
                  )}
                </TableCell>
              </TableRow>
            ) : (
              visible.map((row) => (
                <TableRow
                  key={row.id}
                  data-state={selectable && selected.has(row.id) ? "selected" : undefined}
                  onClick={onRowClick ? () => onRowClick(row) : undefined}
                  className={onRowClick ? "cursor-pointer" : undefined}
                >
                  {selectable ? (
                    <TableCell className="w-0" onClick={(e) => e.stopPropagation()}>
                      <Checkbox
                        checked={selected.has(row.id)}
                        onCheckedChange={() => toggleRow(row.id)}
                        aria-label="Выбрать строку"
                      />
                    </TableCell>
                  ) : null}
                  {visibleColumns.map((col) => (
                    <TableCell
                      key={col.key}
                      className={cn(col.align && alignClass[col.align], col.className)}
                    >
                      {col.render
                        ? col.render(row)
                        : defaultCell(rawValue(row as Record<string, unknown>, col.key))}
                    </TableCell>
                  ))}
                  {rowActions ? (
                    <TableCell
                      className="text-right"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <div className="flex justify-end gap-1">{rowActions(row)}</div>
                    </TableCell>
                  ) : null}
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>

      {effectivePageSize && total > effectivePageSize ? (
        <div className="flex items-center justify-between gap-2 text-sm text-muted-foreground">
          <span>
            {(current - 1) * effectivePageSize + 1}–{Math.min(current * effectivePageSize, total)} из{" "}
            {total}
          </span>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={current <= 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
            >
              Назад
            </Button>
            <span className="tabular-nums">
              {current} / {pages}
            </span>
            <Button
              variant="outline"
              size="sm"
              disabled={current >= pages}
              onClick={() => setPage((p) => Math.min(pages, p + 1))}
            >
              Вперёд
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
