"use client";

import * as React from "react";
import { ArrowDown, ArrowUp, ChevronsUpDown, Search } from "lucide-react";

import { cn } from "@/lib/utils";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
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
  /** Right-aligned per-row controls (edit / delete …) rendered in a last column. */
  rowActions?: (row: T) => React.ReactNode;
  onRowClick?: (row: T) => void;
  empty?: React.ReactNode;
  /** Extra controls shown on the toolbar row, left of the search box. */
  toolbar?: React.ReactNode;
  className?: string;
}

type SortState = { key: string; dir: "asc" | "desc" } | null;

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
  rowActions,
  onRowClick,
  empty,
  toolbar,
  className,
}: DataTableProps<T>) {
  const [query, setQuery] = React.useState("");
  const [sort, setSort] = React.useState<SortState>(null);
  const [page, setPage] = React.useState(1);
  const [tabIdx, setTabIdx] = React.useState(0);

  const keys = searchKeys ?? columns.map((c) => c.key);
  const tabs = filterField ? filterTabs : undefined;

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
  const pages = pageSize ? Math.max(1, Math.ceil(total / pageSize)) : 1;
  const current = Math.min(page, pages);
  const visible = pageSize
    ? sorted.slice((current - 1) * pageSize, current * pageSize)
    : sorted;

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

  const colCount = columns.length + (rowActions ? 1 : 0);

  return (
    <div className={cn("space-y-3", className)}>
      {(searchable || toolbar || (tabs && tabs.length > 1)) && (
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
          {searchable && (
            <div className="relative sm:w-64">
              <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={searchPlaceholder}
                className="pl-9"
              />
            </div>
          )}
        </div>
      )}

      <div className="elev-1 overflow-hidden rounded-xl border border-border bg-card">
        <Table>
          <TableHeader className="bg-muted/40">
            <TableRow>
              {columns.map((col) => {
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
          <TableBody>
            {loading ? (
              Array.from({ length: pageSize ?? 5 }).map((_, i) => (
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
                      title={query ? "Ничего не найдено" : "Пока пусто"}
                      description={
                        query
                          ? "Измените запрос или сбросьте фильтр."
                          : "Здесь появятся записи, как только вы их добавите."
                      }
                    />
                  )}
                </TableCell>
              </TableRow>
            ) : (
              visible.map((row) => (
                <TableRow
                  key={row.id}
                  onClick={onRowClick ? () => onRowClick(row) : undefined}
                  className={onRowClick ? "cursor-pointer" : undefined}
                >
                  {columns.map((col) => (
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

      {pageSize && total > pageSize ? (
        <div className="flex items-center justify-between gap-2 text-sm text-muted-foreground">
          <span>
            {(current - 1) * pageSize + 1}–{Math.min(current * pageSize, total)} из {total}
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
